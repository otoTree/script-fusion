import os
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI


class AIAPIError(Exception):
    pass


@dataclass(frozen=True)
class AIAPIConfig:
    base_url: str
    api_key: str
    model: str
    timeout: int = 30


def load_ai_api_config(prefix: str = "AI_API") -> AIAPIConfig:
    base_url = os.environ.get(f"{prefix}_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get(f"{prefix}_KEY", "").strip()
    model = os.environ.get(f"{prefix}_MODEL", "").strip()
    timeout_raw = os.environ.get(f"{prefix}_TIMEOUT", "30").strip()

    if not base_url:
        raise AIAPIError(f"缺少环境变量: {prefix}_BASE_URL")
    if not api_key:
        raise AIAPIError(f"缺少环境变量: {prefix}_KEY")
    if not model:
        raise AIAPIError(f"缺少环境变量: {prefix}_MODEL")

    try:
        timeout = int(timeout_raw)
    except ValueError as exc:
        raise AIAPIError(f"环境变量 {prefix}_TIMEOUT 不是有效整数: {timeout_raw}") from exc

    return AIAPIConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
    )


def build_chat_payload(
    messages: list[dict[str, Any]],
    model: str,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if extra_payload:
        payload.update(extra_payload)
    return payload


def call_ai_chat_completion(
    messages: list[dict[str, Any]],
    config: AIAPIConfig | None = None,
    endpoint: str = "/chat/completions",
    temperature: float = 0.7,
    max_tokens: int | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if endpoint != "/chat/completions":
        raise AIAPIError(f"OpenAI SDK 仅支持 /chat/completions，当前为: {endpoint}")

    current_config = config or load_ai_api_config()
    payload = build_chat_payload(
        messages=messages,
        model=current_config.model,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_payload=extra_payload,
    )
    client = OpenAI(
        api_key=current_config.api_key,
        base_url=current_config.base_url,
        timeout=current_config.timeout,
    )

    try:
        response = client.chat.completions.create(**payload)
    except (APITimeoutError, APIConnectionError, APIError) as exc:
        raise AIAPIError(f"AI API 请求失败: {exc}") from exc

    return response.model_dump()


def extract_first_message_content(result: dict[str, Any]) -> str:
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AIAPIError("AI API 响应缺少 choices")

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise AIAPIError("AI API 响应的 choice 结构不正确")

    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise AIAPIError("AI API 响应缺少 message")

    content = message.get("content")
    if not isinstance(content, str):
        raise AIAPIError("AI API 响应缺少可用的 content")

    return content
