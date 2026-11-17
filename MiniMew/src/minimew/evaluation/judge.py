"""独立受控评价角色；输入gold仅在答卷已保存后构造。"""

import json

from pydantic import Field

from minimew.evaluation.protocol import Case, FrozenModel, Material
from minimew.evaluation.runtime_protocol import JudgeResult


class JudgeInput(FrozenModel):
    standard: Case
    answer: str = Field(min_length=1, max_length=100000)
    materials: tuple[Material, ...]
    source_texts: tuple[str, ...]


class JudgeFailure(Exception):
    def __init__(self, code, retryable=False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


async def controlled_judge(inputs: JudgeInput, scenario, attempt):
    if inputs.standard.case_id != scenario.case_id:
        raise JudgeFailure("JUDGE_INPUT_MISMATCH")
    if scenario.judge_fault == "timeout":
        raise JudgeFailure("JUDGE_TIMEOUT")
    if scenario.judge_fault == "transient" and attempt == 1:
        raise JudgeFailure("JUDGE_RETRYABLE", retryable=True)
    if scenario.judge is None:
        raise JudgeFailure("JUDGE_UNAVAILABLE")
    if scenario.judge_fault == "invalid":
        raise JudgeFailure("JUDGE_INVALID_RESPONSE")
    payload = scenario.judge.model_dump_json()
    # 受控输出只验协议/流程；没有真实模型token计量，不把字符计数冒充usage。
    return JudgeResult.model_validate_json(json.dumps(json.loads(payload)))
