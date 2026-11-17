"""共享计算的宿主身份；评测身份不会伪装成Run/Thread。"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ChatIdentity:
    user_id: UUID
    thread_id: UUID
    run_id: UUID

    def __post_init__(self):
        if not all(isinstance(v, UUID) for v in (self.user_id, self.thread_id, self.run_id)):
            raise ValueError("Server-owned chat identity required")

    @property
    def checkpoint_key(self):
        return str(self.run_id)


@dataclass(frozen=True)
class CaseIdentity:
    user_id: UUID
    job_id: UUID
    case_id: UUID
    attempt_id: UUID

    def __post_init__(self):
        if not all(
            isinstance(v, UUID) for v in (self.user_id, self.job_id, self.case_id, self.attempt_id)
        ):
            raise ValueError("Server-owned evaluation identity required")

    @property
    def checkpoint_key(self):
        # 资格attempt更换不改变同Case已持久的图命名空间。
        return f"evaluation:{self.job_id}:{self.case_id}"
