import json
import mimetypes
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ToAPIsImageError(Exception):
    pass


@dataclass(frozen=True)
class ToAPIsImageConfig:
    base_url: str
    api_key: str
    model: str = "gemini-3-pro-image-preview"
    timeout: int = 60
    max_concurrency: int = 4


_CONCURRENCY_MIN = 1
_CONCURRENCY_MAX = 4
_toapis_semaphore_lock = threading.Lock()
_toapis_semaphore_by_key: dict[tuple[str, str, str, int], threading.BoundedSemaphore] = {}


def load_toapis_image_config(prefix: str = "TOAPIS_IMAGE") -> ToAPIsImageConfig:
    base_url = os.environ.get(f"{prefix}_BASE_URL", "https://toapis.com/v1").strip().rstrip("/")
    api_key = os.environ.get(f"{prefix}_KEY", "").strip()
    model = os.environ.get(f"{prefix}_MODEL", "gemini-3-pro-image-preview").strip()
    timeout_raw = os.environ.get(f"{prefix}_TIMEOUT", "60").strip()
    max_concurrency_raw = os.environ.get(f"{prefix}_MAX_CONCURRENCY", "4").strip()

    if not api_key:
        raise ToAPIsImageError(f"缺少环境变量: {prefix}_KEY")
    if not model:
        raise ToAPIsImageError(f"缺少环境变量: {prefix}_MODEL")

    try:
        timeout = int(timeout_raw)
    except ValueError as exc:
        raise ToAPIsImageError(f"环境变量 {prefix}_TIMEOUT 不是有效整数: {timeout_raw}") from exc
    try:
        max_concurrency = int(max_concurrency_raw)
    except ValueError as exc:
        raise ToAPIsImageError(
            f"环境变量 {prefix}_MAX_CONCURRENCY 不是有效整数: {max_concurrency_raw}"
        ) from exc
    if max_concurrency < _CONCURRENCY_MIN or max_concurrency > _CONCURRENCY_MAX:
        raise ToAPIsImageError(
            f"环境变量 {prefix}_MAX_CONCURRENCY 必须在 {_CONCURRENCY_MIN}~{_CONCURRENCY_MAX} 之间: {max_concurrency}"
        )

    return ToAPIsImageConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        max_concurrency=max_concurrency,
    )


def _get_toapis_semaphore(config: ToAPIsImageConfig) -> threading.BoundedSemaphore:
    key = (config.base_url, config.api_key, config.model, config.max_concurrency)
    with _toapis_semaphore_lock:
        semaphore = _toapis_semaphore_by_key.get(key)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(value=config.max_concurrency)
            _toapis_semaphore_by_key[key] = semaphore
    return semaphore


def _request(
    method: str,
    path: str,
    config: ToAPIsImageConfig,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    current_headers = {"Authorization": f"Bearer {config.api_key}"}
    if headers:
        current_headers.update(headers)

    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{config.base_url}{normalized_path}"
    request = Request(url=url, data=data, method=method.upper(), headers=current_headers)
    semaphore = _get_toapis_semaphore(config)

    semaphore.acquire()
    try:
        try:
            with urlopen(request, timeout=config.timeout) as response:
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise ToAPIsImageError(f"ToAPIs 请求失败: status={exc.code}, body={error_body}") from exc
        except URLError as exc:
            raise ToAPIsImageError(f"ToAPIs 连接失败: {exc}") from exc
    finally:
        semaphore.release()

    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ToAPIsImageError(f"ToAPIs 响应不是有效 JSON: {body}") from exc

    if isinstance(result, dict) and isinstance(result.get("error"), dict):
        error_message = result["error"].get("message", "未知错误")
        raise ToAPIsImageError(f"ToAPIs 返回错误: {error_message}")

    return result


def _request_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    config: ToAPIsImageConfig,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if payload is not None else None
    return _request(method=method, path=path, config=config, data=data, headers=headers)


def _normalize_image_urls(image_urls: list[str | dict[str, str]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in image_urls:
        if isinstance(item, str):
            normalized.append({"url": item})
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ToAPIsImageError("image_urls 中的对象缺少有效 url 字段")
        normalized.append({"url": url})
    return normalized


def create_image_task(
    prompt: str,
    config: ToAPIsImageConfig | None = None,
    model: str | None = None,
    size: str = "16:9",
    n: int = 1,
    metadata: dict[str, Any] | None = None,
    image_urls: list[str | dict[str, str]] | None = None,
) -> dict[str, Any]:
    current_config = config or load_toapis_image_config()
    payload: dict[str, Any] = {
        "model": model or current_config.model,
        "prompt": prompt,
        "size": size,
        "n": n,
    }
    if metadata:
        payload["metadata"] = metadata
    if image_urls:
        payload["image_urls"] = _normalize_image_urls(image_urls)
    return _request_json(
        method="POST",
        path="/images/generations",
        payload=payload,
        config=current_config,
    )


def create_text_to_image_task(
    prompt: str,
    config: ToAPIsImageConfig | None = None,
    model: str | None = None,
    size: str = "16:9",
    n: int = 1,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return create_image_task(
        prompt=prompt,
        config=config,
        model=model,
        size=size,
        n=n,
        metadata=metadata,
    )


def create_image_to_image_task(
    prompt: str,
    image_urls: list[str | dict[str, str]],
    config: ToAPIsImageConfig | None = None,
    model: str | None = None,
    size: str = "16:9",
    n: int = 1,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return create_image_task(
        prompt=prompt,
        config=config,
        model=model,
        size=size,
        n=n,
        metadata=metadata,
        image_urls=image_urls,
    )


def get_image_task_status(task_id: str, config: ToAPIsImageConfig | None = None) -> dict[str, Any]:
    current_config = config or load_toapis_image_config()
    return _request_json(
        method="GET",
        path=f"/images/generations/{task_id}",
        payload=None,
        config=current_config,
    )


def wait_for_image_task(
    task_id: str,
    config: ToAPIsImageConfig | None = None,
    max_attempts: int = 60,
    interval: float = 3,
) -> dict[str, Any]:
    current_config = config or load_toapis_image_config()

    for _ in range(max_attempts):
        result = get_image_task_status(task_id=task_id, config=current_config)
        status = result.get("status")
        if status == "completed":
            return result
        if status == "failed":
            raise ToAPIsImageError(f"图像任务失败: {result}")
        time.sleep(interval)

    raise ToAPIsImageError(f"图像任务超时: task_id={task_id}")


def extract_task_id(task: dict[str, Any]) -> str:
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ToAPIsImageError(f"响应中缺少有效任务 ID: {task}")
    return task_id


def extract_image_urls(result: dict[str, Any]) -> list[str]:
    candidate_urls: list[str] = []

    top_url = result.get("url")
    if isinstance(top_url, str):
        candidate_urls.append(top_url)

    data = result.get("data")
    if isinstance(data, dict):
        data_url = data.get("url")
        if isinstance(data_url, str):
            candidate_urls.append(data_url)

        images = data.get("images")
        if isinstance(images, list):
            for item in images:
                if isinstance(item, dict):
                    image_url = item.get("url")
                    if isinstance(image_url, str):
                        candidate_urls.append(image_url)
                elif isinstance(item, str):
                    candidate_urls.append(item)

    if not candidate_urls:
        raise ToAPIsImageError(f"结果中未找到图片 URL: {result}")

    unique_urls: list[str] = []
    for item in candidate_urls:
        if item not in unique_urls:
            unique_urls.append(item)
    return unique_urls


def _build_multipart_form(file_name: str, file_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    boundary = f"----ToAPIsBoundary{uuid.uuid4().hex}"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = head + file_bytes + tail
    return body, f"multipart/form-data; boundary={boundary}"


def upload_image(file_path: str | Path, config: ToAPIsImageConfig | None = None) -> dict[str, Any]:
    current_config = config or load_toapis_image_config()
    target_path = Path(file_path)
    if not target_path.exists() or not target_path.is_file():
        raise ToAPIsImageError(f"图片文件不存在: {target_path}")

    file_bytes = target_path.read_bytes()
    mime_type = mimetypes.guess_type(target_path.name)[0] or "application/octet-stream"
    body, content_type = _build_multipart_form(
        file_name=target_path.name,
        file_bytes=file_bytes,
        mime_type=mime_type,
    )
    return _request(
        method="POST",
        path="/uploads/images",
        config=current_config,
        data=body,
        headers={"Content-Type": content_type},
    )


def upload_and_create_image_to_image_task(
    prompt: str,
    file_path: str | Path,
    config: ToAPIsImageConfig | None = None,
    model: str | None = None,
    size: str = "16:9",
    n: int = 1,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_config = config or load_toapis_image_config()
    upload_result = upload_image(file_path=file_path, config=current_config)
    uploaded = upload_result.get("data")
    if not isinstance(uploaded, dict) or not isinstance(uploaded.get("url"), str):
        raise ToAPIsImageError(f"上传响应中缺少可用 URL: {upload_result}")
    return create_image_to_image_task(
        prompt=prompt,
        image_urls=[uploaded["url"]],
        config=current_config,
        model=model,
        size=size,
        n=n,
        metadata=metadata,
    )
