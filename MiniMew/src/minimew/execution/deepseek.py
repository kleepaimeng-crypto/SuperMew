"""DeepSeek 的 LangChain Adapter；返回公开模型回合，不拥有工具执行、持久化或重试。"""

import asyncio
import math
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx2
import openai
from httpx2 import TimeoutException, TransportError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_deepseek import ChatDeepSeek
from langsmith import tracing_context
from pydantic import Field, PrivateAttr, SecretStr

from minimew.execution.diagnostics import capture
from minimew.execution.messages import (
    MAX_ARGUMENT_CHARS,
    MAX_OUTPUT_CHARS,
    MAX_TOOL_CALLS,
    ModelTurn,
    ToolCall,
    json_object,
    valid_text,
    validate_turn,
)
from minimew.execution.provider import (
    MAX_DELTA_CHARS,
    ProviderCompleted,
    ProviderDelta,
    ProviderFailure,
)


@dataclass
class _ToolFragments:
    id: str
    name: str
    parts: list[str] = field(default_factory=list)
    size: int = 0


class _AnswerChatDeepSeek(ChatDeepSeek):
    expected_tools: frozenset[str] = Field(default_factory=frozenset, exclude=True, repr=False)
    _fragments: dict[int, _ToolFragments] = PrivateAttr(default_factory=dict)
    _wire_finished: bool = PrivateAttr(default=False)

    def _accept_tools(self, calls):
        if not isinstance(calls, list) or (calls and not self.expected_tools):
            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
        for item in calls:
            if not isinstance(item, dict):
                raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
            index, function = item.get("index"), item.get("function")
            if (
                type(index) is not int
                or not 0 <= index < MAX_TOOL_CALLS
                or not isinstance(function, dict)
                or item.get("type") not in (None, "function")
            ):
                raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
            if index not in self._fragments:
                call_id, name = item.get("id"), function.get("name")
                if (
                    not valid_text(call_id, 128, nonblank=True)
                    or name not in self.expected_tools
                    or any(part.id == call_id for part in self._fragments.values())
                ):
                    raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                self._fragments[index] = _ToolFragments(call_id, name)
            fragment = self._fragments[index]
            if item.get("id") not in (None, fragment.id) or function.get("name") not in (
                None,
                fragment.name,
            ):
                raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
            arguments = function.get("arguments")
            if arguments is not None:
                if not isinstance(arguments, str) or (
                    arguments and not valid_text(arguments, MAX_ARGUMENT_CHARS)
                ):
                    raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                fragment.size += len(arguments)
                if fragment.size > MAX_ARGUMENT_CHARS:
                    raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                fragment.parts.append(arguments)

    def completed_turn(self, text, finish):
        if finish == "stop" and not self._fragments:
            turn = ModelTurn("text", text)
        elif finish == "tool_calls" and self.expected_tools and self._fragments:
            if sorted(self._fragments) != list(range(len(self._fragments))):
                raise ValueError("Incomplete call indexes")
            turn = ModelTurn(
                "tool_calls",
                text,
                tuple(
                    ToolCall(value.id, value.name, "".join(value.parts))
                    for _, value in sorted(self._fragments.items())
                ),
            )
        else:
            raise ValueError("Finish reason and tool payload disagree")
        validate_turn(turn)
        return turn

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        # LangChain 默认只取 choices[0]，并会忽略 delta=None。普通聊天应明确拒绝畸形流。
        choices = chunk.get("choices")
        if not isinstance(choices, list) or len(choices) > 1 or chunk.get("error"):
            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
        if choices:
            choice = choices[0]
            if not isinstance(choice, dict):
                raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
            delta = choice.get("delta")
            if (
                type(choice.get("index")) is not int
                or choice.get("index") != 0
                or self._wire_finished
                or not isinstance(delta, dict)
                or delta.get("role") not in (None, "assistant")
                or delta.get("function_call")
                or (delta.get("content") is not None and not isinstance(delta["content"], str))
            ):
                raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
            if delta.get("tool_calls") is not None:
                self._accept_tools(delta["tool_calls"])
            if choice.get("finish_reason") is not None:
                self._wire_finished = True
        return super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )


def retry_after_seconds(headers) -> float:
    value = headers.get("retry-after", "")
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


class DeepSeekProvider:
    def __init__(self, api_key: SecretStr):
        self._api_key = api_key

    async def stream(self, request):
        configuration = request.snapshot.answer.configuration
        url = urlsplit(configuration.base_url)
        if (
            url.scheme != "https"
            or url.hostname != "api.deepseek.com"
            or url.port not in (None, 443)
            or url.path.rstrip("/") not in ("", "/v1")
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or not self._api_key.get_secret_value().strip()
        ):
            raise ProviderFailure()
        resources = AsyncExitStack()
        parts, total, finish_reason = [], 0, None
        try:
            # SDK 3 使用 httpx2；自有客户端关闭重定向及宿主代理/身份注入。
            sync_http = resources.enter_context(httpx2.Client(trust_env=False))
            async_http = await resources.enter_async_context(httpx2.AsyncClient(trust_env=False))
            parameters = dict(
                api_key=self._api_key.get_secret_value(),
                organization="",
                project="",
                base_url=configuration.base_url,
                max_retries=0,
                timeout=configuration.timeout_seconds,
            )
            sync_client = openai.OpenAI(**parameters, http_client=sync_http)
            async_client = openai.AsyncOpenAI(**parameters, http_client=async_http)
            model = _AnswerChatDeepSeek(
                model=configuration.model_name,
                api_key=self._api_key,
                base_url=configuration.base_url,
                temperature=configuration.temperature,
                max_tokens=configuration.max_output_tokens,
                n=1,
                timeout=configuration.timeout_seconds,
                max_retries=0,
                client=sync_client.chat.completions,
                async_client=async_client.chat.completions,
                root_client=sync_client,
                root_async_client=async_client,
                use_responses_api=False,
                stream_usage=True,
                output_version="v0",
                extra_body={
                    "thinking": {"type": "disabled"},
                    **({"response_format": {"type": "json_object"}} if request.json_output else {}),
                },
                expected_tools=frozenset(tool.name for tool in request.tools),
            )
            messages = []
            if request.instructions:
                messages.append(SystemMessage(content=request.instructions))
            for message in request.messages:
                if message.role == "user":
                    messages.append(HumanMessage(content=message.content))
                elif message.role == "tool":
                    messages.append(
                        ToolMessage(content=message.content, tool_call_id=message.tool_call_id)
                    )
                else:
                    messages.append(
                        AIMessage(
                            content=message.content,
                            tool_calls=[
                                {
                                    "id": call.id,
                                    "name": call.name,
                                    "args": json_object(call.arguments_json),
                                    "type": "tool_call",
                                }
                                for call in message.tool_calls
                            ],
                        )
                    )
            bound = (
                model.bind_tools(
                    [tool.wire() for tool in request.tools],
                    tool_choice="none" if request.force_answer else "auto",
                )
                if request.tools
                else model
            )
            # 不继承机器上的 LANGSMITH_TRACING；快照/正文/密钥不进入外部追踪。
            with tracing_context(enabled=False):
                source = bound.astream(messages, config={"callbacks": []})
                resources.push_async_callback(source.aclose)
                async for chunk in source:
                    if not isinstance(chunk.content, str) or (
                        chunk.tool_call_chunks and not request.tools
                    ):
                        raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                    text = chunk.content  # reasoning_content 等扩展字段不会进入正文。
                    if text:
                        if finish_reason is not None or not valid_text(text, MAX_OUTPUT_CHARS):
                            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                        total += len(text)
                        if total > MAX_OUTPUT_CHARS:
                            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                        parts.append(text)
                        if not request.tools:
                            for start in range(0, len(text), MAX_DELTA_CHARS):
                                yield ProviderDelta(text[start : start + MAX_DELTA_CHARS])
                    finish = chunk.response_metadata.get("finish_reason")
                    if finish is not None:
                        if finish == "length":
                            raise ProviderFailure(
                                "PROVIDER_INVALID_RESPONSE",
                                diagnostic=capture(request.purpose, "output_truncated"),
                            )
                        if finish_reason is not None or finish not in ("stop", "tool_calls"):
                            raise ProviderFailure(
                                "PROVIDER_INVALID_RESPONSE",
                                diagnostic=capture(request.purpose, "invalid_finish_reason"),
                            )
                        if finish == "tool_calls" and not request.tools:
                            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                        finish_reason = finish
            if finish_reason is None:
                raise ProviderFailure(
                    "PROVIDER_INVALID_RESPONSE",
                    diagnostic=capture(request.purpose, "missing_completion"),
                )
            if finish_reason == "stop" and not "".join(parts).strip():
                raise ProviderFailure(
                    "PROVIDER_INVALID_RESPONSE",
                    diagnostic=capture(request.purpose, "empty_model_output"),
                )
            turn = model.completed_turn("".join(parts), finish_reason)
        except openai.APITimeoutError as failure:
            raise ProviderFailure(
                "PROVIDER_TIMEOUT",
                retryable=True,
                diagnostic=capture(
                    request.purpose,
                    "provider_timeout",
                    failure,
                ),
            ) from None
        except openai.APIConnectionError as failure:
            raise ProviderFailure(
                retryable=True,
                diagnostic=capture(
                    request.purpose,
                    "provider_connection_failed",
                    failure,
                ),
            ) from None
        except TimeoutException as failure:
            raise ProviderFailure(
                "PROVIDER_TIMEOUT",
                retryable=True,
                diagnostic=capture(
                    request.purpose,
                    "transport_timeout",
                    failure,
                ),
            ) from None
        except TransportError as failure:
            raise ProviderFailure(
                retryable=True,
                diagnostic=capture(
                    request.purpose,
                    "transport_failed",
                    failure,
                ),
            ) from None
        except openai.APIStatusError as failure:
            raise ProviderFailure(
                retryable=failure.status_code in (408, 429) or failure.status_code >= 500,
                retry_after=retry_after_seconds(failure.response.headers),
                diagnostic=capture(
                    request.purpose,
                    "provider_http_error",
                    failure,
                    cause_code=f"HTTP_{failure.status_code}",
                ),
            ) from None
        except (ValueError, TypeError, KeyError) as failure:
            raise ProviderFailure(
                "PROVIDER_INVALID_RESPONSE",
                diagnostic=capture(
                    request.purpose,
                    "provider_response_invalid",
                    failure,
                ),
            ) from None
        finally:
            primary = sys.exception()
            try:
                async with asyncio.timeout(3):
                    await resources.aclose()
            except Exception as failure:
                if primary is None:
                    raise ProviderFailure(
                        diagnostic=capture(
                            request.purpose,
                            "provider_cleanup_failed",
                            failure,
                        )
                    ) from None
        yield ProviderCompleted(turn)
