"""把已经选定的完整历史问答组织成有界模型输入。"""

import re
from collections.abc import Sequence

from minimew.execution.provider import InputMessage


class ContextInvalid(Exception):
    pass


HISTORY_NOTICE = (
    "【历史助手回答：检索描述属于过去轮次，旧引用已移除；本次核验情况以当前工具结果为准。】\n"
)


def project_history_answer(answer: str) -> str:
    """只改变模型输入；持久消息、历史引用材料与用户当前问题均保持原样。"""
    answer = re.sub(r"\[E[1-9][0-9]*\]", "", answer)
    answer = re.sub(r"(?m)^## 文档依据\r?$", "历史回答中的文档说明", answer)
    answer = re.sub(r"(?m)^## 模型补充（未由本次资料验证）\r?$", "历史回答中的模型补充", answer)
    return HISTORY_NOTICE + answer


def build_messages(
    current: str, newest_pairs: Sequence[tuple[str, str]], *, max_turns=20, max_chars=60000
) -> tuple[InputMessage, ...]:
    if not current.strip() or "\x00" in current or len(current) > max_chars:
        raise ContextInvalid
    size = len(current)
    chosen = []
    for user, answer in newest_pairs[:max_turns]:
        if not user.strip() or not answer.strip() or "\x00" in user + answer:
            raise ContextInvalid
        answer = project_history_answer(answer)
        if size + len(user) + len(answer) > max_chars:
            break  # 只保留最近的完整后缀，不跳过大轮次后捡更旧内容。
        chosen.append((user, answer))
        size += len(user) + len(answer)
    messages = []
    for user, answer in reversed(chosen):
        messages.extend([InputMessage("user", user), InputMessage("assistant", answer)])
    messages.append(InputMessage("user", current))
    return tuple(messages)
