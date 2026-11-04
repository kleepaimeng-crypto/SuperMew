"""把已经选定的完整历史问答组织成有界模型输入。"""

from collections.abc import Sequence

from minimew.execution.provider import InputMessage


class ContextInvalid(Exception):
    pass


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
        if size + len(user) + len(answer) > max_chars:
            break  # 只保留最近的完整后缀，不跳过大轮次后捡更旧内容。
        chosen.append((user, answer))
        size += len(user) + len(answer)
    messages = []
    for user, answer in reversed(chosen):
        messages.extend([InputMessage("user", user), InputMessage("assistant", answer)])
    messages.append(InputMessage("user", current))
    return tuple(messages)
