from __future__ import annotations

import os
import threading
import time
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
    max_concurrency: int = 4


_CONCURRENCY_MIN = 1
_CONCURRENCY_MAX = 4
_ai_api_semaphore_lock = threading.Lock()
_ai_api_semaphore_by_key: dict[tuple[str, str, str, int], threading.BoundedSemaphore] = {}
_ai_api_interval_lock = threading.Lock()
_ai_api_last_request_at: dict[tuple[str, str, str, int], float] = {}
_ai_api_client_lock = threading.Lock()
_ai_api_client_cache: dict[tuple[str, str, int], OpenAI] = {}


def load_ai_api_config(prefix: str = "AI_API") -> AIAPIConfig:
    base_url = os.environ.get(f"{prefix}_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get(f"{prefix}_KEY", "").strip()
    model = os.environ.get(f"{prefix}_MODEL", "").strip()
    timeout_raw = os.environ.get(f"{prefix}_TIMEOUT", "60").strip()
    max_concurrency_raw = os.environ.get(f"{prefix}_MAX_CONCURRENCY", "4").strip()

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
    try:
        max_concurrency = int(max_concurrency_raw)
    except ValueError as exc:
        raise AIAPIError(
            f"环境变量 {prefix}_MAX_CONCURRENCY 不是有效整数: {max_concurrency_raw}"
        ) from exc
    if max_concurrency < _CONCURRENCY_MIN or max_concurrency > _CONCURRENCY_MAX:
        raise AIAPIError(
            f"环境变量 {prefix}_MAX_CONCURRENCY 必须在 {_CONCURRENCY_MIN}~{_CONCURRENCY_MAX} 之间: {max_concurrency}"
        )

    return AIAPIConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        max_concurrency=max_concurrency,
    )


def _get_ai_api_semaphore(config: AIAPIConfig) -> threading.BoundedSemaphore:
    key = (config.base_url, config.api_key, config.model, config.max_concurrency)
    with _ai_api_semaphore_lock:
        semaphore = _ai_api_semaphore_by_key.get(key)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(value=config.max_concurrency)
            _ai_api_semaphore_by_key[key] = semaphore
    return semaphore


def _get_ai_api_client(config: AIAPIConfig) -> OpenAI:
    key = (config.base_url, config.api_key, config.timeout)
    with _ai_api_client_lock:
        client = _ai_api_client_cache.get(key)
        if client is None:
            client = OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=config.timeout,
            )
            _ai_api_client_cache[key] = client
    return client


def _get_ai_api_interval_seconds(prefix: str = "AI_API") -> float:
    value = os.environ.get(f"{prefix}_MIN_INTERVAL_MS", "1").strip()
    try:
        interval_ms = float(value)
    except ValueError as exc:
        raise AIAPIError(f"环境变量 {prefix}_MIN_INTERVAL_MS 不是有效数字: {value}") from exc
    if interval_ms < 0:
        raise AIAPIError(f"环境变量 {prefix}_MIN_INTERVAL_MS 不能小于 0: {value}")
    return interval_ms 


def _wait_for_ai_api_interval(config: AIAPIConfig, min_interval_seconds: float):
    if min_interval_seconds <= 0:
        return
    key = (config.base_url, config.api_key, config.model, config.max_concurrency)
    with _ai_api_interval_lock:
        now = time.monotonic()
        last = _ai_api_last_request_at.get(key, 0.0)
        elapsed = now - last
        if elapsed < min_interval_seconds:
            time.sleep(min_interval_seconds - elapsed)
            now = time.monotonic()
        _ai_api_last_request_at[key] = now


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
    client = _get_ai_api_client(current_config)
    semaphore = _get_ai_api_semaphore(current_config)
    min_interval_seconds = _get_ai_api_interval_seconds()

    semaphore.acquire()
    try:
        _wait_for_ai_api_interval(current_config, min_interval_seconds=min_interval_seconds)
        try:
            response = client.chat.completions.create(**payload)
        except (APITimeoutError, APIConnectionError, APIError) as exc:
            raise AIAPIError(f"AI API 请求失败: {exc}") from exc
    finally:
        semaphore.release()

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
