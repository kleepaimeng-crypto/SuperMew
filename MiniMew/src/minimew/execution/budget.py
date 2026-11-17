"""聊天与评测共享的计算额度；不拥有任务状态。"""

from dataclasses import dataclass

from minimew.execution.tools import ToolFailure


@dataclass
class ExecutionBudget:
    model_requests: int = 0
    tool_calls: int = 0
    internal_requests: int = 0
    retrieval_queries: int = 0
    planner_used: bool = False
    rewrite_used: bool = False
    reasoning: bool = False
    clarification_used: bool = False
    finalization: bool = False

    def counts(self):
        return {
            "outer_model_requests": self.model_requests,
            "internal_model_requests": self.internal_requests,
            "total_model_requests": self.model_requests + self.internal_requests,
            "tool_calls": self.tool_calls,
            "retrieval_queries": self.retrieval_queries,
        }

    def exhausted(self, reason):
        from minimew.execution.diagnostics import Diagnostic

        return ToolFailure(
            "AGENT_BUDGET_EXCEEDED",
            diagnostic=Diagnostic("budget", reason, counts=self.counts()),
        )

    def internal_available(self):
        return max(
            0, 12 - self.model_requests - self.internal_requests - (2 if self.finalization else 1)
        )

    def can_request_tools(self):
        return (
            4 - self.model_requests >= 3
            and 12 - self.model_requests - self.internal_requests >= 3
            and self.tool_calls < 6
            and self.retrieval_queries < 8
        )

    def require_model(self):
        if self.model_requests >= 4:
            raise self.exhausted("outer_model_limit")
        if self.reasoning and self.model_requests + self.internal_requests >= 12:
            raise self.exhausted("total_model_limit")

    def take_internal(self):
        if self.finalization and not self.internal_available():
            from minimew.rag.reasoning import FinalizationReserved

            if self.model_requests + self.internal_requests < 12:
                raise FinalizationReserved
            raise self.exhausted("total_model_limit")
        if self.model_requests + self.internal_requests >= 11:
            raise self.exhausted("internal_model_limit")
        self.internal_requests += 1

    def take_retrieval(self):
        if self.retrieval_queries >= 8:
            raise self.exhausted("retrieval_limit")
        self.retrieval_queries += 1

    def require_tools(self, count):
        self.require_model()  # 工具执行后必须仍有机会向模型交回结果。
        if self.finalization and (
            4 - self.model_requests < 2 or 12 - self.model_requests - self.internal_requests < 2
        ):
            raise self.exhausted("finalization_reserve")
        if self.tool_calls + count > 6:
            raise self.exhausted("tool_limit")
