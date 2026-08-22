"""OpenAI Responses 和兼容 Chat Completions 接口的适配器。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI

from ..adapter import ModelAdapter
from ..config import ProviderConfig
from ..errors import ModelAdapterError
from ..types import (
    ApiStyle,
    Message,
    MessageRole,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolDefinition,
)


class OpenAICompatibleAdapter(ModelAdapter):
    """支持 OpenAI、Qwen、DeepSeek 及其他兼容端点。"""

    def __init__(
        self,
        config: ProviderConfig,
        *,
        client: Any | None = None,
    ) -> None:
        if config.api_style is ApiStyle.ANTHROPIC_MESSAGES:
            raise ValueError("Anthropic Messages requires AnthropicAdapter")
        super().__init__(config)

        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key_value(),
            "timeout": config.timeout_seconds,
            "max_retries": config.max_retries,
        }
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self._client = client or AsyncOpenAI(**client_kwargs)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        try:
            if self.config.api_style is ApiStyle.RESPONSES:
                return await self._complete_responses(request)
            return await self._complete_chat(request)
        except ModelAdapterError:
            raise
        except Exception as exc:
            raise ModelAdapterError(
                f"{self.provider} model request failed: {exc}"
            ) from exc

    async def complete_stream(
        self,
        request: ModelRequest,
        *,
        on_text_delta: Callable[[str], Awaitable[None]],
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> ModelResponse:
        """使用 Provider 原生流，同时在结束时还原完整 ``ModelResponse``。"""

        try:
            if self.config.api_style is ApiStyle.RESPONSES:
                return await self._stream_responses(request, on_text_delta)
            return await self._stream_chat(
                request, on_text_delta, on_reasoning_delta
            )
        except ModelAdapterError:
            raise
        except Exception as exc:
            raise ModelAdapterError(
                f"{self.provider} model stream failed: {exc}"
            ) from exc

    async def close(self) -> None:
        await self._client.close()

    async def _complete_responses(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model or self.default_model,
            "input": _responses_input(request.messages),
        }
        if request.tools:
            kwargs["tools"] = [_responses_tool(tool) for tool in request.tools]
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_output_tokens"] = request.max_output_tokens
        if request.extra_body:
            kwargs["extra_body"] = request.extra_body

        response = await self._client.responses.create(**kwargs)
        return _normalize_responses_response(response, self.provider)

    async def _complete_chat(
        self,
        request: ModelRequest,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model or self.default_model,
            "messages": [_chat_message(message) for message in request.messages],
        }
        if request.tools:
            kwargs["tools"] = [_chat_tool(tool) for tool in request.tools]
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_tokens"] = request.max_output_tokens
        if request.extra_body:
            kwargs["extra_body"] = request.extra_body

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        response_message = choice.message
        tool_calls = tuple(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=_parse_arguments(call.function.arguments),
            )
            for call in (response_message.tool_calls or ())
        )

        return ModelResponse(
            id=response.id,
            provider=self.provider,
            model=response.model,
            message=Message(
                role=MessageRole.ASSISTANT,
                content=response_message.content,
                tool_calls=tool_calls,
                reasoning=getattr(response_message, "reasoning_content", None),
            ),
            finish_reason=choice.finish_reason,
            usage=_chat_usage(getattr(response, "usage", None)),
            raw=_model_dump(response),
        )

    async def _stream_responses(
        self,
        request: ModelRequest,
        on_text_delta: Callable[[str], Awaitable[None]],
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model or self.default_model,
            "input": _responses_input(request.messages),
            "stream": True,
        }
        if request.tools:
            kwargs["tools"] = [_responses_tool(tool) for tool in request.tools]
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_output_tokens"] = request.max_output_tokens
        if request.extra_body:
            kwargs["extra_body"] = request.extra_body

        stream = await self._client.responses.create(**kwargs)
        final_response: Any | None = None
        async for event in stream:
            event_type = getattr(event, "type", None)
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                if delta:
                    await on_text_delta(delta)
            elif event_type == "response.completed":
                final_response = getattr(event, "response", None)

        if final_response is None:
            raise ModelAdapterError(
                f"{self.provider} response stream ended without response.completed"
            )
        return _normalize_responses_response(final_response, self.provider)

    async def _stream_chat(
        self,
        request: ModelRequest,
        on_text_delta: Callable[[str], Awaitable[None]],
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model or self.default_model,
            "messages": [_chat_message(message) for message in request.messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            kwargs["tools"] = [_chat_tool(tool) for tool in request.tools]
        if request.tool_choice is not None:
            kwargs["tool_choice"] = request.tool_choice
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_tokens"] = request.max_output_tokens
        if request.extra_body:
            kwargs["extra_body"] = request.extra_body

        stream = await self._client.chat.completions.create(**kwargs)
        response_id = ""
        response_model = request.model or self.default_model
        finish_reason: str | None = None
        usage: Any | None = None
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_parts: dict[int, dict[str, str]] = {}

        async for chunk in stream:
            response_id = getattr(chunk, "id", response_id) or response_id
            response_model = getattr(chunk, "model", response_model) or response_model
            usage = getattr(chunk, "usage", None) or usage
            choices = getattr(chunk, "choices", None) or ()
            if not choices:
                continue
            choice = choices[0]
            finish_reason = getattr(choice, "finish_reason", None) or finish_reason
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None)
            if content:
                text_parts.append(content)
                await on_text_delta(content)
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
                if on_reasoning_delta is not None:
                    await on_reasoning_delta(reasoning)
            for call in getattr(delta, "tool_calls", None) or ():
                index = int(getattr(call, "index", 0) or 0)
                part = tool_parts.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                part["id"] += getattr(call, "id", None) or ""
                function = getattr(call, "function", None)
                if function is not None:
                    part["name"] += getattr(function, "name", None) or ""
                    part["arguments"] += getattr(function, "arguments", None) or ""

        tool_calls = tuple(
            ToolCall(
                id=part["id"],
                name=part["name"],
                arguments=_parse_arguments(part["arguments"]),
            )
            for _, part in sorted(tool_parts.items())
        )
        return ModelResponse(
            id=response_id or "stream",
            provider=self.provider,
            model=response_model,
            message=Message(
                role=MessageRole.ASSISTANT,
                content="".join(text_parts) or None,
                tool_calls=tool_calls,
                reasoning="".join(reasoning_parts) or None,
            ),
            finish_reason=finish_reason or ("tool_calls" if tool_calls else "stop"),
            usage=_chat_usage(usage),
            raw=None,
        )


def _arguments_json(arguments: dict[str, Any] | str) -> str:
    if isinstance(arguments, str):
        return arguments
    return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))


def _normalize_responses_response(response: Any, provider: str) -> ModelResponse:
    tool_calls = tuple(
        ToolCall(
            id=item.call_id,
            name=item.name,
            arguments=_parse_arguments(item.arguments),
        )
        for item in response.output
        if getattr(item, "type", None) == "function_call"
    )
    return ModelResponse(
        id=response.id,
        provider=provider,
        model=response.model,
        message=Message(
            role=MessageRole.ASSISTANT,
            content=response.output_text or None,
            tool_calls=tool_calls,
            reasoning=_responses_reasoning(response),
        ),
        finish_reason=_responses_finish_reason(response, tool_calls),
        usage=_responses_usage(getattr(response, "usage", None)),
        raw=_model_dump(response),
    )


def _responses_reasoning(response: Any) -> str | None:
    """Responses API 的推理摘要（reasoning items 的 summary 拼接）。"""
    parts: list[str] = []
    for item in getattr(response, "reasoning", None) or ():
        summary = getattr(item, "summary", None)
        if summary:
            parts.append(summary)
    return "".join(parts) or None


def _parse_arguments(arguments: Any) -> dict[str, Any] | str:
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return str(arguments)
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments
    return parsed if isinstance(parsed, dict) else arguments


def _responses_input(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role is MessageRole.TOOL:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content or "",
                }
            )
            continue

        if message.content is not None:
            items.append(
                {
                    "role": message.role.value,
                    "content": message.content,
                }
            )
        for call in message.tool_calls:
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": _arguments_json(call.arguments),
                }
            )
    return items


def _chat_message(message: Message) -> dict[str, Any]:
    result: dict[str, Any] = {
        "role": message.role.value,
        "content": message.content,
    }
    if message.name is not None:
        result["name"] = message.name
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": _arguments_json(call.arguments),
                },
            }
            for call in message.tool_calls
        ]
    return result


def _responses_tool(tool: ToolDefinition) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }
    if tool.strict is not None:
        result["strict"] = tool.strict
    return result


def _chat_tool(tool: ToolDefinition) -> dict[str, Any]:
    function: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
    }
    if tool.strict is not None:
        function["strict"] = tool.strict
    return {"type": "function", "function": function}


def _responses_finish_reason(
    response: Any,
    tool_calls: tuple[ToolCall, ...],
) -> str | None:
    if tool_calls:
        return "tool_calls"
    if getattr(response, "status", None) == "incomplete":
        details = getattr(response, "incomplete_details", None)
        return getattr(details, "reason", None) or "incomplete"
    return getattr(response, "status", None) or "stop"


def _responses_usage(usage: Any | None) -> ModelUsage:
    if usage is None:
        return ModelUsage()
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cached, cache_write, cache_reported = _openai_cache_usage(
        usage,
        details_name="input_tokens_details",
    )
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=int(
            getattr(usage, "total_tokens", input_tokens + output_tokens)
            or input_tokens + output_tokens
        ),
        cached_input_tokens=cached if cache_reported else None,
        uncached_input_tokens=(
            max(0, input_tokens - cached) if cache_reported else None
        ),
        cache_read_input_tokens=cached if cache_reported else None,
        cache_write_input_tokens=cache_write,
        model_calls=1,
    )


def _chat_usage(usage: Any | None) -> ModelUsage:
    if usage is None:
        return ModelUsage()
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    cached, cache_write, cache_reported = _openai_cache_usage(
        usage,
        details_name="prompt_tokens_details",
    )
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=int(
            getattr(usage, "total_tokens", input_tokens + output_tokens)
            or input_tokens + output_tokens
        ),
        cached_input_tokens=cached if cache_reported else None,
        uncached_input_tokens=(
            _optional_int(usage, "prompt_cache_miss_tokens")
            if _optional_int(usage, "prompt_cache_miss_tokens") is not None
            else (max(0, input_tokens - cached) if cache_reported else None)
        ),
        cache_read_input_tokens=cached if cache_reported else None,
        cache_write_input_tokens=cache_write,
        model_calls=1,
    )


def _openai_cache_usage(
    usage: Any,
    *,
    details_name: str,
) -> tuple[int, int | None, bool]:
    """兼容 OpenAI/Qwen/DeepSeek 的缓存 Usage 字段。"""

    deepseek_hit = _optional_int(usage, "prompt_cache_hit_tokens")
    details = getattr(usage, details_name, None)
    nested_cached = _optional_int(details, "cached_tokens")
    direct_cached = _optional_int(usage, "cached_tokens")
    cached_value = next(
        (
            value
            for value in (deepseek_hit, nested_cached, direct_cached)
            if value is not None
        ),
        None,
    )
    cache_write = _optional_int(details, "cache_creation_input_tokens")
    return cached_value or 0, cache_write, cached_value is not None


def _optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    raw = value.get(field) if isinstance(value, dict) else getattr(value, field, None)
    if raw is None:
        return None
    return max(0, int(raw))


def _model_dump(value: Any) -> dict[str, Any] | None:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value if isinstance(value, dict) else None
