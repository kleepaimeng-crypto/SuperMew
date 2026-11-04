"""DeepSeek 的 LangChain Adapter；只输出公开正文，不拥有业务持久化或重试。"""

import asyncio
import math
import sys
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx2
import openai
from httpx2 import TimeoutException, TransportError
from langchain_core.messages import AIMessage, HumanMessage
from langchain_deepseek import ChatDeepSeek
from langsmith import tracing_context
from pydantic import SecretStr

from minimew.execution.provider import (
    MAX_DELTA_CHARS,
    MAX_OUTPUT_CHARS,
    ProviderCompleted,
    ProviderDelta,
    ProviderFailure,
    valid_text,
)


class _AnswerChatDeepSeek(ChatDeepSeek):
    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        # LangChain 默认只取 choices[0]，并会忽略 delta=None。普通聊天应明确拒绝畸形流。
        choices = chunk.get("choices")
        if not isinstance(choices, list) or len(choices) > 1 or chunk.get("error"):
            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
        if choices:
            choice = choices[0]
            delta = choice.get("delta")
            if (
                choice.get("index") != 0
                or not isinstance(delta, dict)
                or delta.get("role") not in (None, "assistant")
                or delta.get("tool_calls")
                or delta.get("function_call")
                or (delta.get("content") is not None and not isinstance(delta["content"], str))
            ):
                raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
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
        parts, total, stopped = [], 0, False
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
                extra_body={"thinking": {"type": "disabled"}},
            )
            messages = [
                HumanMessage(content=m.content)
                if m.role == "user"
                else AIMessage(content=m.content)
                for m in request.messages
            ]
            # 不继承机器上的 LANGSMITH_TRACING；快照/正文/密钥不进入外部追踪。
            with tracing_context(enabled=False):
                source = model.astream(messages, config={"callbacks": []})
                resources.push_async_callback(source.aclose)
                async for chunk in source:
                    if not isinstance(chunk.content, str) or chunk.tool_call_chunks:
                        raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                    text = chunk.content  # reasoning_content 等扩展字段不会进入正文。
                    if text:
                        if stopped or not valid_text(text, MAX_OUTPUT_CHARS):
                            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                        total += len(text)
                        if total > MAX_OUTPUT_CHARS:
                            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                        parts.append(text)
                        for start in range(0, len(text), MAX_DELTA_CHARS):
                            yield ProviderDelta(text[start : start + MAX_DELTA_CHARS])
                    finish = chunk.response_metadata.get("finish_reason")
                    if finish is not None:
                        if stopped or finish != "stop":
                            raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
                        stopped = True
            if not stopped or not valid_text("".join(parts), MAX_OUTPUT_CHARS, nonblank=True):
                raise ProviderFailure("PROVIDER_INVALID_RESPONSE")
        except openai.APITimeoutError:
            raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True) from None
        except openai.APIConnectionError:
            raise ProviderFailure(retryable=True) from None
        except TimeoutException:
            raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True) from None
        except TransportError:
            raise ProviderFailure(retryable=True) from None
        except openai.APIStatusError as failure:
            raise ProviderFailure(
                retryable=failure.status_code in (408, 429) or failure.status_code >= 500,
                retry_after=retry_after_seconds(failure.response.headers),
            ) from None
        except (ValueError, TypeError, KeyError):
            raise ProviderFailure("PROVIDER_INVALID_RESPONSE") from None
        finally:
            primary = sys.exception()
            try:
                async with asyncio.timeout(3):
                    await resources.aclose()
            except Exception:
                if primary is None:
                    raise ProviderFailure() from None
        yield ProviderCompleted("".join(parts))
