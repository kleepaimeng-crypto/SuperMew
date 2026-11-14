"""受限 JSON 编码和 Run 私有 saver；仅暂停发布事务才持久化，不运行反序列化代码。"""

import dataclasses
import json
from datetime import datetime
from uuid import UUID

from langgraph.checkpoint.base import WRITES_IDX_MAP, BaseCheckpointSaver, CheckpointTuple
from langgraph.types import Interrupt, Send
from pydantic import BaseModel, TypeAdapter


def types():
    # 延迟导入避免 knowledge → workflow → checkpoint 的模块循环。
    from minimew.documents.retrieval import RetrievalScope
    from minimew.evidence import Evidence
    from minimew.execution.messages import InputMessage, ToolCall
    from minimew.knowledge import KnowledgeResult
    from minimew.rag.protocol import Candidate, MaterialGroup, RetrievalResult, RetrievalTrace
    from minimew.rag.reasoning import Grade

    return {
        t.__name__: t
        for t in (
            RetrievalScope,
            Evidence,
            InputMessage,
            ToolCall,
            KnowledgeResult,
            Candidate,
            MaterialGroup,
            RetrievalResult,
            RetrievalTrace,
            Grade,
            Interrupt,
            Send,
        )
    }


def encode(value):
    if value is None or type(value) in (str, int, float, bool):
        return value
    if isinstance(value, UUID):
        return {"@": "uuid", "v": str(value)}
    if isinstance(value, datetime):
        return {"@": "datetime", "v": value.isoformat()}
    if isinstance(value, dict):
        return {"@": "dict", "v": [[encode(k), encode(v)] for k, v in value.items()]}
    if type(value) in (list, tuple, frozenset):
        return {"@": type(value).__name__, "v": [encode(v) for v in value]}
    name = type(value).__name__
    if types().get(name) is not type(value):
        raise ValueError(f"Unsupported checkpoint type: {type(value).__module__}.{name}")
    if name == "Send":
        body = {"node": value.node, "arg": value.arg}
    elif dataclasses.is_dataclass(value):
        # Scope 的产物目录有独立校验器，保存 JSON 数据而非内部 dataclass 类名。
        if name == "RetrievalScope":
            return {"@": name, "json": TypeAdapter(type(value)).dump_json(value).decode()}
        body = {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    elif isinstance(value, BaseModel):
        return {"@": name, "json": value.model_dump_json()}
    else:
        raise ValueError("Unsupported checkpoint value")
    return {"@": name, "v": encode(body)}


def decode(value):
    if not isinstance(value, dict):
        if value is None or type(value) in (str, int, float, bool):
            return value
        raise ValueError("Invalid checkpoint primitive")
    name = value["@"]
    if name == "uuid":
        return UUID(value["v"])
    if name == "datetime":
        return datetime.fromisoformat(value["v"])
    if name == "dict":
        return {decode(k): decode(v) for k, v in value["v"]}
    if name in ("list", "tuple", "frozenset"):
        return {"list": list, "tuple": tuple, "frozenset": frozenset}[name](
            decode(v) for v in value["v"]
        )
    cls = types().get(name)
    if cls is None:
        raise ValueError("Unknown checkpoint type")
    if "json" in value:
        return TypeAdapter(cls).validate_json(value["json"])
    return cls(**decode(value["v"]))


def validate_document(document):
    if document.get("version") != 1:
        raise ValueError("Unsupported checkpoint schema")
    if len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode()) > 8_388_608:
        raise ValueError("Checkpoint exceeds byte budget")
    return document


class BufferedSaver(BaseCheckpointSaver):
    """完整 channel values，单图/单 Run 的异步 saver。只导出最新恢复点及其 pending writes。"""

    def __init__(self, document=None):
        super().__init__()
        self.entries, self.writes, self.latest = {}, {}, None
        if document is not None:
            entry = decode(validate_document(document)["entry"])
            self.latest = entry[1]["id"]
            self.entries[self.latest] = entry[:4]
            self.writes[self.latest] = entry[4]

    async def aget_tuple(self, config):
        key = config["configurable"].get("checkpoint_id", self.latest)
        if key not in self.entries:
            return None
        cfg, checkpoint, metadata, parent = self.entries[key]
        if cfg["configurable"]["thread_id"] != config["configurable"]["thread_id"]:
            raise ValueError("Checkpoint Run mismatch")
        pending = [
            (task, channel, value)
            for (task, _), (channel, value) in self.writes.get(key, {}).items()
        ]
        return CheckpointTuple(cfg, checkpoint, metadata, parent, pending)

    async def aput(self, config, checkpoint, metadata, new_versions):
        identity = {
            k: config["configurable"][k]
            for k in ("thread_id", "checkpoint_ns", "checkpoint_id")
            if k in config["configurable"]
        }
        cfg = {"configurable": {**identity, "checkpoint_id": checkpoint["id"]}}
        parent = {"configurable": identity} if identity.get("checkpoint_id") else None
        # graph 在后台保存；同步深复制，后续节点不能改动已经保存的 channel。
        values = decode(encode((cfg, checkpoint, metadata, parent)))
        self.entries[checkpoint["id"]] = values
        self.latest = checkpoint["id"]
        return cfg

    async def aput_writes(self, config, writes, task_id, task_path=""):
        pending = self.writes.setdefault(config["configurable"]["checkpoint_id"], {})
        for index, (channel, value) in enumerate(writes):
            key = (task_id, WRITES_IDX_MAP.get(channel, index))
            if key not in pending or key[1] < 0:
                pending[key] = (channel, decode(encode(value)))

    def document(self):
        if self.latest is None:
            raise ValueError("No checkpoint produced")
        entry = (*self.entries[self.latest], self.writes.get(self.latest, {}))
        return validate_document({"version": 1, "entry": encode(entry)})

    def state(self):
        return self.entries[self.latest][1]["channel_values"]
