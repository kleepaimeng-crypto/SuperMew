"""宿主无关计算上下文；资格、保存与取消由宿主提供。"""

from dataclasses import dataclass, field
from uuid import UUID

from minimew.evidence import EvidenceDirectory
from minimew.execution.identity import CaseIdentity, ChatIdentity
from minimew.rag.protocol import RagPolicy


@dataclass
class TraceState:
    last_trace: object | None = None


@dataclass(frozen=True)
class RagContext:
    execution_identity: ChatIdentity | CaseIdentity
    evidence: EvidenceDirectory
    rag_policy: RagPolicy = field(default_factory=RagPolicy)
    deadline: float | None = None
    reasoning: object | None = None
    repairs: object | None = None
    model_caller: object | None = None
    run_budget: object | None = None
    question: str = ""
    hitl: object | None = None
    checkpoint: dict | None = None
    trace_state: TraceState = field(default_factory=TraceState)
    progress: object | None = None
    process: object | None = None
    rerank_credential_id: UUID | None = None

    def __post_init__(self):
        if not isinstance(self.execution_identity, (ChatIdentity, CaseIdentity)):
            raise ValueError("Typed execution owner required")
        if not self.evidence.owned_by(self.execution_identity):
            raise ValueError("Evidence belongs to another execution")

    @property
    def user_id(self):
        return self.execution_identity.user_id

    @property
    def run_id(self):
        return (
            self.execution_identity.run_id
            if isinstance(self.execution_identity, ChatIdentity)
            else None
        )

    @property
    def thread_id(self):
        return (
            self.execution_identity.thread_id
            if isinstance(self.execution_identity, ChatIdentity)
            else None
        )


def from_chat(context):
    return RagContext(
        execution_identity=context.execution_identity,
        evidence=context.evidence,
        **{
            name: getattr(context, name)
            for name in (
                "rag_policy",
                "deadline",
                "reasoning",
                "repairs",
                "model_caller",
                "run_budget",
                "question",
                "hitl",
                "checkpoint",
                "process",
                "rerank_credential_id",
            )
        },
    )
