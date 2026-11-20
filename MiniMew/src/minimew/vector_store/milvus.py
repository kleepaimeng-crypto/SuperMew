"""PyMilvus 3.0 CPU FLAT；实例独占连接，RPC 有 deadline、无自动重试。"""

import time

from minimew.documents.profiles import BAILIAN_LAYOUTS, HYBRID_LAYOUTS
from minimew.vector_store.config import VectorSettings
from minimew.vector_store.manifest import (
    ANALYZER,
    CandidateScope,
    VectorError,
    canonical,
    collection_for,
    encoding_space,
    profile_for_layout,
)

TEXT_FIELDS = {
    "id": 64,
    "chunk_id": 36,
    "scope_id": 64,
    "user_id": 36,
    "knowledge_base_id": 36,
    "document_id": 36,
    "version_id": 36,
    "build_fingerprint": 64,
    "record_sha256": 64,
}
FIELDS = [*TEXT_FIELDS, "payload", "vector"]


def scope_filter(scope: CandidateScope) -> str:
    # 只生成固定字段，值做 JSON string 编码，不拼用户提供的表达式。
    return " and ".join(
        f"{key} == {canonical(value)}" for key, value in scope.scalar_fields.items()
    )


class MilvusBackend:
    def __init__(self, settings: VectorSettings, client, *, namespace=None):
        self.settings = settings
        self.client = client
        if namespace is not None:
            import re

            if re.fullmatch(r"minimew_(eval|project)_[0-9a-f]{32}", namespace) is None:
                raise ValueError("Server-owned evaluation namespace required")
        self.namespace = namespace

    def collection(self, layout):
        default = collection_for(layout)
        if self.namespace is None:
            return default
        kind = "_hybrid" if layout in HYBRID_LAYOUTS else "_dense"
        return self.namespace + kind + ("_bailian" if layout in BAILIAN_LAYOUTS else "")

    def for_namespace(self, namespace):
        # 独立不可变视图，共用有界RPC连接；不修改全局当前集合。
        return MilvusBackend(self.settings, self.client, namespace=namespace)

    @classmethod
    def connect(cls, settings: VectorSettings, *, namespace=None) -> "MilvusBackend":
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise VectorError("VECTOR_DEPENDENCIES_UNAVAILABLE") from exc
        try:
            # SDK 对 numeric timeout 优先按时间重试，故每 RPC timeout=None/retry_times=0；
            # deadline 交给 gRPC service config，transport retry 同时关闭。
            client = MilvusClient(
                uri=settings.uri,
                token=settings.token.get_secret_value(),
                timeout=settings.timeout_seconds,
                dedicated=True,
                grpc_options={
                    "grpc.enable_retries": 0,
                    "grpc.service_config": canonical(
                        {
                            "methodConfig": [
                                {
                                    "name": [{}],
                                    "timeout": f"{settings.timeout_seconds:g}s",
                                }
                            ]
                        }
                    ),
                },
            )
            return cls(settings, client, namespace=namespace)
        except Exception as exc:
            raise VectorError("VECTOR_UNAVAILABLE") from exc

    def _rpc(self, method, **kwargs):
        try:
            return method(timeout=None, retry_times=0, retry_on_rate_limit=False, **kwargs)
        except VectorError:
            raise
        except Exception as exc:
            raise VectorError("VECTOR_UNAVAILABLE") from exc

    def close(self) -> None:
        self.client.close()

    def probe(self) -> None:
        """只检查读取连接，不在API查询运行时创建或修复集合。"""
        self._rpc(self.client.list_collections)

    def ensure_collection(self, *, layout="dense-cosine-v1") -> None:
        from pymilvus import DataType, Function, FunctionType

        collection = self.collection(layout)
        hybrid = layout in HYBRID_LAYOUTS
        description = (
            "MiniMew hybrid jieba bm25 v1 " if hybrid else "MiniMew candidate dense v1 "
        ) + encoding_space(profile_for_layout(layout))

        if not self._rpc(self.client.has_collection, collection_name=collection):
            schema = self.client.create_schema(
                auto_id=False, enable_dynamic_field=False, description=description
            )
            for name, size in TEXT_FIELDS.items():
                schema.add_field(name, DataType.VARCHAR, max_length=size, is_primary=name == "id")
            schema.add_field("payload", DataType.JSON)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=512)
            if hybrid:
                schema.add_field(
                    "retrieval_text",
                    DataType.VARCHAR,
                    max_length=65535,
                    enable_analyzer=True,
                    analyzer_params=ANALYZER,
                )
                schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
                schema.add_function(
                    Function(
                        name="lexical_bm25",
                        input_field_names=["retrieval_text"],
                        output_field_names=["sparse"],
                        function_type=FunctionType.BM25,
                    )
                )
            self._rpc(
                self.client.create_collection,
                collection_name=collection,
                schema=schema,
                consistency_level="Strong",
            )
            indexes = self.client.prepare_index_params()
            indexes.add_index(
                field_name="vector", index_name="dense", index_type="FLAT", metric_type="COSINE"
            )
            if hybrid:
                indexes.add_index(
                    field_name="sparse",
                    index_name="lexical",
                    index_type="SPARSE_INVERTED_INDEX",
                    metric_type="BM25",
                    params={"bm25_k1": 1.2, "bm25_b": 0.75},
                )
            self._rpc(
                self.client.create_index,
                collection_name=collection,
                index_params=indexes,
                sync=False,
            )
        info = self._rpc(self.client.describe_collection, collection_name=collection)
        fields = {field["name"]: field for field in info["fields"]}
        if (
            info.get("description") != description
            or info.get("auto_id")
            or info.get("enable_dynamic_field")
            or info.get("consistency_level") != 0
            or set(fields) != set(FIELDS + (["retrieval_text", "sparse"] if hybrid else []))
            or fields["vector"]["type"] != DataType.FLOAT_VECTOR
            or int(fields["vector"]["params"].get("dim", 0)) != 512
            or fields["payload"]["type"] != DataType.JSON
            or not fields["id"].get("is_primary")
            or any(
                fields[name]["type"] != DataType.VARCHAR
                or int(fields[name]["params"].get("max_length", 0)) != size
                for name, size in TEXT_FIELDS.items()
            )
        ):
            raise VectorError("VECTOR_COLLECTION_MISMATCH")
        index = self._rpc(
            self.client.describe_index, collection_name=collection, index_name="dense"
        )
        if index.get("index_type") != "FLAT" or index.get("metric_type") != "COSINE":
            raise VectorError("VECTOR_COLLECTION_MISMATCH")
        if hybrid:
            params = fields["retrieval_text"].get("params", {})
            analyzer = params.get("analyzer_params")
            if isinstance(analyzer, str):
                import json

                analyzer = json.loads(analyzer)
            functions = info.get("functions", [])
            lexical = self._rpc(
                self.client.describe_index, collection_name=collection, index_name="lexical"
            )
            if (
                fields["sparse"]["type"] != DataType.SPARSE_FLOAT_VECTOR
                or fields["retrieval_text"]["type"] != DataType.VARCHAR
                or int(params.get("max_length", 0)) != 65535
                or str(params.get("enable_analyzer")).lower() != "true"
                or analyzer != ANALYZER
                or len(functions) != 1
                or functions[0].get("input_field_names") != ["retrieval_text"]
                or functions[0].get("output_field_names") != ["sparse"]
                or int(functions[0].get("type", 0)) != int(FunctionType.BM25)
                or lexical.get("metric_type") != "BM25"
                or lexical.get("index_type") != "SPARSE_INVERTED_INDEX"
                or lexical.get("bm25_k1") != "1.2"
                or lexical.get("bm25_b") != "0.75"
            ):
                raise VectorError("VECTOR_COLLECTION_MISMATCH")
        self._rpc(self.client.load_collection, collection_name=collection, _async=True)
        deadline = time.monotonic() + self.settings.timeout_seconds
        while True:
            state = self._rpc(self.client.get_load_state, collection_name=collection)
            if int(state["state"]) == 3:  # SDK LoadState.Loaded
                return
            if time.monotonic() >= deadline:
                raise VectorError("VECTOR_LOAD_TIMEOUT")
            time.sleep(0.1)

    def read(self, scope: CandidateScope, max_rows: int) -> list[dict]:
        iterator = self._rpc(
            self.client.query_iterator,
            collection_name=self.collection(scope.index_layout),
            filter=scope_filter(scope),
            output_fields=FIELDS
            + (["retrieval_text"] if scope.index_layout in HYBRID_LAYOUTS else []),
            batch_size=128,
            consistency_level="Strong",
        )
        rows = []
        try:
            while len(rows) < max_rows:
                batch = iterator.next()
                if not batch:
                    break
                rows.extend(batch)
            return rows[:max_rows]
        except Exception as exc:
            raise VectorError("VECTOR_UNAVAILABLE") from exc
        finally:
            iterator.close()

    def fetch(self, scope, ids):
        if not ids or len(ids) > 6:
            raise VectorError("VECTOR_INVALID_QUERY")
        return self._rpc(
            self.client.query,
            collection_name=self.collection(scope.index_layout),
            filter=scope_filter(scope) + " and chunk_id in " + canonical(list(ids)),
            output_fields=FIELDS
            + (["retrieval_text"] if scope.index_layout in HYBRID_LAYOUTS else []),
            consistency_level="Strong",
            limit=7,
        )

    def upsert(self, rows: list[dict], *, layout="dense-cosine-v1") -> None:
        result = self._rpc(self.client.upsert, collection_name=self.collection(layout), data=rows)
        if result["upsert_count"] != len(rows):
            raise VectorError("VECTOR_PARTIAL_WRITE")

    def search(self, scope: CandidateScope, vector: list[float], limit: int) -> list[dict]:
        result = self._rpc(
            self.client.search,
            collection_name=self.collection(scope.index_layout),
            data=[vector],
            anns_field="vector",
            filter=scope_filter(scope),
            limit=limit,
            output_fields=FIELDS
            + (["retrieval_text"] if scope.index_layout in HYBRID_LAYOUTS else []),
            search_params={"metric_type": "COSINE", "params": {}},
            consistency_level="Strong",
        )
        return result[0]

    def lexical(self, scope, query, limit):
        if scope.index_layout not in HYBRID_LAYOUTS:
            raise VectorError("VECTOR_CAPABILITY_UNSUPPORTED")
        return self._rpc(
            self.client.search,
            collection_name=self.collection(scope.index_layout),
            data=[query],
            anns_field="sparse",
            filter=scope_filter(scope),
            limit=limit,
            output_fields=FIELDS + ["retrieval_text"],
            search_params={"metric_type": "BM25", "params": {}},
            consistency_level="Strong",
        )[0]

    def delete(self, scope: CandidateScope) -> None:
        self._rpc(
            self.client.delete,
            collection_name=self.collection(scope.index_layout),
            filter=scope_filter(scope),
        )
