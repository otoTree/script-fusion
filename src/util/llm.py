from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from openai import APIConnectionError, APIError, APITimeoutError, OpenAI


class AIAPIError(Exception):
    pass


@dataclass(frozen=True)
class AIAPIConfig:
    base_url: str
    api_key: str
    model: str
    timeout: int = 900
    max_concurrency: int = 1


_CONCURRENCY_MIN = 1
_CONCURRENCY_MAX = 50
_ai_api_semaphore_lock = threading.Lock()
_ai_api_semaphore_by_key: dict[tuple[str, str, str, int], threading.BoundedSemaphore] = {}
_ai_api_interval_lock = threading.Lock()
_ai_api_last_request_at: dict[tuple[str, str, str, int], float] = {}
_ai_api_client_lock = threading.Lock()
_ai_api_client_cache: dict[tuple[str, str, int], OpenAI] = {}
_config_cache: dict[str, Any] | None = None
_config_cache_lock = threading.Lock()


def _load_config_file(config_path: str | None = None) -> dict[str, Any]:
    """加载配置文件（带缓存）"""
    global _config_cache

    with _config_cache_lock:
        if _config_cache is not None:
            return _config_cache

        if config_path is None:
            # 默认从项目根目录查找 config.json
            current_file = Path(__file__)
            project_root = current_file.parent.parent.parent
            config_path = str(project_root / "config.json")

        if not os.path.exists(config_path):
            raise AIAPIError(f"配置文件不存在: {config_path}")

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                _config_cache = json.load(f)
        except json.JSONDecodeError as exc:
            raise AIAPIError(f"配置文件格式错误: {config_path}") from exc
        except Exception as exc:
            raise AIAPIError(f"读取配置文件失败: {config_path}") from exc

        return _config_cache


def load_ai_api_config(prefix: str = "ai_api", config_path: str | None = None) -> AIAPIConfig:
    """从 config.json 加载 AI API 配置"""
    config = _load_config_file(config_path)

    if prefix not in config:
        raise AIAPIError(f"配置文件中缺少 '{prefix}' 配置项")

    api_config = config[prefix]

    base_url = api_config.get("base_url", "").strip().rstrip("/")
    api_key = api_config.get("api_key", "").strip()
    model = api_config.get("model", "").strip()
    timeout = api_config.get("timeout", 300)
    max_concurrency = api_config.get("max_concurrency", 1)

    if not base_url:
        raise AIAPIError(f"配置项 '{prefix}.base_url' 不能为空")
    if not api_key:
        raise AIAPIError(f"配置项 '{prefix}.api_key' 不能为空")
    if not model:
        raise AIAPIError(f"配置项 '{prefix}.model' 不能为空")

    if not isinstance(timeout, int):
        raise AIAPIError(f"配置项 '{prefix}.timeout' 必须是整数")
    if not isinstance(max_concurrency, int):
        raise AIAPIError(f"配置项 '{prefix}.max_concurrency' 必须是整数")

    if max_concurrency < _CONCURRENCY_MIN or max_concurrency > _CONCURRENCY_MAX:
        raise AIAPIError(
            f"配置项 '{prefix}.max_concurrency' 必须在 {_CONCURRENCY_MIN}~{_CONCURRENCY_MAX} 之间: {max_concurrency}"
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


def _get_ai_api_interval_seconds(config: AIAPIConfig | None = None, prefix: str = "ai_api") -> float:
    """从 config.json 获取 API 调用最小间隔（毫秒）"""
    config_data = _load_config_file()

    if prefix not in config_data:
        return 1.0  # 默认 1 毫秒

    api_config = config_data[prefix]
    interval_ms = api_config.get("min_interval_ms", 1)

    if not isinstance(interval_ms, (int, float)):
        raise AIAPIError(f"配置项 '{prefix}.min_interval_ms' 必须是数字")
    if interval_ms < 0:
        raise AIAPIError(f"配置项 '{prefix}.min_interval_ms' 不能小于 0")

    return float(interval_ms) 


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
    min_interval_seconds = _get_ai_api_interval_seconds(current_config)

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


def call_ai_image_generation(
    prompt: str,
    config: AIAPIConfig | None = None,
    n: int = 1,
    quality: str = "standard",
    size: str = "1024x1024",
    response_format: str = "url",
) -> dict[str, Any]:
    """
    调用 AI 图像生成 API

    Args:
        prompt: 图像描述文本
        config: API 配置，如果为 None 则从 config.json 加载
        n: 生成图片数量 (1-10)
        quality: 图片质量 ("standard" 或 "hd")
        size: 图片尺寸 (如 "1024x1024")
        response_format: 返回格式 ("url" 或 "b64_json")

    Returns:
        API 响应字典
    """
    current_config = config or load_ai_api_config()
    client = _get_ai_api_client(current_config)
    semaphore = _get_ai_api_semaphore(current_config)
    min_interval_seconds = _get_ai_api_interval_seconds(current_config)

    # 构建聊天消息格式的图像生成请求
    messages = [
        {
            "role": "user",
            "content": prompt
        }
    ]

    semaphore.acquire()
    try:
        _wait_for_ai_api_interval(current_config, min_interval_seconds=min_interval_seconds)
        try:
            # 使用聊天完成端点进行图像生成
            # 注意：不传递图像生成专用参数，因为某些 API 的 chat completions 端点不支持
            response = client.chat.completions.create(
                model=current_config.model,
                messages=messages
            )
        except (APITimeoutError, APIConnectionError, APIError) as exc:
            raise AIAPIError(f"AI 图像生成请求失败: {exc}") from exc
    finally:
        semaphore.release()

    return response.model_dump()


def call_ai_image_edit(
    prompt: str,
    image_paths: list[str],
    config: AIAPIConfig | None = None,
    quality: str = "standard",
    size: str = "1024x1024",
) -> dict[str, Any]:
    """
    调用 AI 图像编辑 API

    Args:
        prompt: 编辑描述文本
        image_paths: 要编辑的图片文件路径列表
        config: API 配置，如果为 None 则从 config.json 加载
        quality: 图片质量 ("standard" 或 "high")
        size: 图片尺寸 (如 "1024x1024")

    Returns:
        API 响应字典
    """
    current_config = config or load_ai_api_config()
    semaphore = _get_ai_api_semaphore(current_config)
    min_interval_seconds = _get_ai_api_interval_seconds(current_config)

    semaphore.acquire()
    try:
        _wait_for_ai_api_interval(current_config, min_interval_seconds=min_interval_seconds)
        try:
            files = [("image[]", open(path, "rb")) for path in image_paths]
            data = {
                "model": current_config.model,
                "prompt": prompt,
                "quality": quality,
                "size": size,
            }
            headers = {"Authorization": f"Bearer {current_config.api_key}"}

            response = requests.post(
                f"{current_config.base_url}/images/edits",
                headers=headers,
                data=data,
                files=files,
                timeout=current_config.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise AIAPIError(f"AI 图像编辑请求失败: {exc}") from exc
        finally:
            for _, file in files:
                file.close()
    finally:
        semaphore.release()


def call_ai_video_generation(
    prompt: str,
    config: AIAPIConfig | None = None,
    duration: int = 8,
    size: str = "9:16",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    调用 AI 视频生成 API（异步）

    Args:
        prompt: 视频描述文本
        config: API 配置，如果为 None 则从 config.json 加载
        duration: 视频时长（秒，如 4/8）
        size: 视频比例（如 "16:9", "9:16"）
        metadata: 可选元数据，支持 multi_shot、element_list 等字段

    Returns:
        包含 task_id 的响应字典
    """
    current_config = config or load_ai_api_config()
    semaphore = _get_ai_api_semaphore(current_config)
    min_interval_seconds = _get_ai_api_interval_seconds(current_config)

    semaphore.acquire()
    try:
        _wait_for_ai_api_interval(current_config, min_interval_seconds=min_interval_seconds)
        try:
            payload: dict[str, Any] = {
                "model": current_config.model,
                "prompt": prompt,
                "duration": duration,
                "size": size,
            }
            if metadata is not None:
                payload["metadata"] = metadata

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {current_config.api_key}",
            }

            response = requests.post(
                f"{current_config.base_url}/video/generations",
                headers=headers,
                json=payload,
                timeout=current_config.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise AIAPIError(f"AI 视频生成请求失败: {exc}") from exc
    finally:
        semaphore.release()


def get_ai_video_status(
    video_id: str,
    config: AIAPIConfig | None = None,
) -> dict[str, Any]:
    """
    查询视频生成任务状态

    Args:
        video_id: 视频任务 ID
        config: API 配置，如果为 None 则从 config.json 加载

    Returns:
        包含状态、进度、URL 等信息的响应字典
    """
    current_config = config or load_ai_api_config()

    try:
        headers = {"Authorization": f"Bearer {current_config.api_key}"}
        response = requests.get(
            f"{current_config.base_url}/videos/{video_id}",
            headers=headers,
            timeout=current_config.timeout,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise AIAPIError(f"查询视频状态失败: {exc}") from exc


def download_ai_video(
    video_id: str,
    output_path: str,
    config: AIAPIConfig | None = None,
    variant: str = "mp4",
) -> None:
    """
    下载已完成的视频

    Args:
        video_id: 视频任务 ID
        output_path: 输出文件路径
        config: API 配置，如果为 None 则从 config.json 加载
        variant: 下载资源类型（默认 "mp4"）
    """
    current_config = config or load_ai_api_config()

    try:
        headers = {"Authorization": f"Bearer {current_config.api_key}"}
        params = {"variant": variant}
        response = requests.get(
            f"{current_config.base_url}/videos/{video_id}/content",
            headers=headers,
            params=params,
            timeout=current_config.timeout,
            stream=True,
        )
        response.raise_for_status()

        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
    except requests.RequestException as exc:
        raise AIAPIError(f"下载视频失败: {exc}") from exc


def wait_for_video_completion(
    video_id: str,
    config: AIAPIConfig | None = None,
    poll_interval: int = 5,
    max_wait_time: int = 600,
) -> dict[str, Any]:
    """
    等待视频生成完成（轮询）

    Args:
        video_id: 视频任务 ID
        config: API 配置，如果为 None 则从 config.json 加载
        poll_interval: 轮询间隔（秒）
        max_wait_time: 最大等待时间（秒）

    Returns:
        完成后的视频状态信息

    Raises:
        AIAPIError: 超时或生成失败
    """
    start_time = time.time()

    while True:
        status_info = get_ai_video_status(video_id, config)
        status = status_info.get("status")

        if status == "completed":
            return status_info
        elif status == "failed":
            raise AIAPIError(f"视频生成失败: {status_info}")

        elapsed = time.time() - start_time
        if elapsed > max_wait_time:
            raise AIAPIError(f"视频生成超时（{max_wait_time}秒）")

        time.sleep(poll_interval)
