"""PyMilvus 3.0 CPU FLAT；实例独占连接，RPC 有 deadline、无自动重试。"""

import time

from minimew.vector_store.config import VectorSettings
from minimew.vector_store.manifest import (
    COLLECTION,
    SPACE_HASH,
    CandidateScope,
    VectorError,
    canonical,
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
DESCRIPTION = "MiniMew candidate dense v1 " + SPACE_HASH


def scope_filter(scope: CandidateScope) -> str:
    # 只生成固定字段，值做 JSON string 编码，不拼用户提供的表达式。
    return " and ".join(
        f"{key} == {canonical(value)}" for key, value in scope.scalar_fields.items()
    )


class MilvusBackend:
    def __init__(self, settings: VectorSettings, client):
        self.settings = settings
        self.client = client

    @classmethod
    def connect(cls, settings: VectorSettings) -> "MilvusBackend":
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
            return cls(settings, client)
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

    def ensure_collection(self) -> None:
        from pymilvus import DataType

        if not self._rpc(self.client.has_collection, collection_name=COLLECTION):
            schema = self.client.create_schema(
                auto_id=False, enable_dynamic_field=False, description=DESCRIPTION
            )
            for name, size in TEXT_FIELDS.items():
                schema.add_field(name, DataType.VARCHAR, max_length=size, is_primary=name == "id")
            schema.add_field("payload", DataType.JSON)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=512)
            self._rpc(
                self.client.create_collection,
                collection_name=COLLECTION,
                schema=schema,
                consistency_level="Strong",
            )
            indexes = self.client.prepare_index_params()
            indexes.add_index(
                field_name="vector", index_name="dense", index_type="FLAT", metric_type="COSINE"
            )
            self._rpc(
                self.client.create_index,
                collection_name=COLLECTION,
                index_params=indexes,
                sync=False,
            )
        info = self._rpc(self.client.describe_collection, collection_name=COLLECTION)
        fields = {field["name"]: field for field in info["fields"]}
        if (
            info.get("description") != DESCRIPTION
            or info.get("auto_id")
            or info.get("enable_dynamic_field")
            or info.get("consistency_level") != 0
            or set(fields) != set(FIELDS)
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
            self.client.describe_index, collection_name=COLLECTION, index_name="dense"
        )
        if index.get("index_type") != "FLAT" or index.get("metric_type") != "COSINE":
            raise VectorError("VECTOR_COLLECTION_MISMATCH")
        self._rpc(self.client.load_collection, collection_name=COLLECTION, _async=True)
        deadline = time.monotonic() + self.settings.timeout_seconds
        while True:
            state = self._rpc(self.client.get_load_state, collection_name=COLLECTION)
            if int(state["state"]) == 3:  # SDK LoadState.Loaded
                return
            if time.monotonic() >= deadline:
                raise VectorError("VECTOR_LOAD_TIMEOUT")
            time.sleep(0.1)

    def read(self, scope: CandidateScope, max_rows: int) -> list[dict]:
        iterator = self._rpc(
            self.client.query_iterator,
            collection_name=COLLECTION,
            filter=scope_filter(scope),
            output_fields=FIELDS,
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

    def upsert(self, rows: list[dict]) -> None:
        result = self._rpc(self.client.upsert, collection_name=COLLECTION, data=rows)
        if result["upsert_count"] != len(rows):
            raise VectorError("VECTOR_PARTIAL_WRITE")

    def search(self, scope: CandidateScope, vector: list[float], limit: int) -> list[dict]:
        result = self._rpc(
            self.client.search,
            collection_name=COLLECTION,
            data=[vector],
            anns_field="vector",
            filter=scope_filter(scope),
            limit=limit,
            output_fields=FIELDS,
            search_params={"metric_type": "COSINE", "params": {}},
            consistency_level="Strong",
        )
        return result[0]

    def delete(self, scope: CandidateScope) -> None:
        self._rpc(self.client.delete, collection_name=COLLECTION, filter=scope_filter(scope))
