from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


class S3Error(Exception):
    pass


@dataclass(frozen=True)
class S3Config:
    bucket: str
    region: str
    access_key: str
    secret_key: str
    endpoint_url: str | None = None
    prefix: str = ""


_s3_config_cache: dict[str, Any] | None = None
_s3_config_cache_lock = threading.Lock()
_s3_client_cache: dict[tuple, Any] = {}
_s3_client_lock = threading.Lock()


def _load_config_file(config_path: str | None = None) -> dict[str, Any]:
    global _s3_config_cache
    with _s3_config_cache_lock:
        if _s3_config_cache is not None:
            return _s3_config_cache
        if config_path is None:
            current_file = Path(__file__)
            project_root = current_file.parent.parent.parent
            config_path = str(project_root / "config.json")
        if not os.path.exists(config_path):
            raise S3Error(f"配置文件不存在: {config_path}")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                _s3_config_cache = json.load(f)
        except json.JSONDecodeError as exc:
            raise S3Error(f"配置文件格式错误: {config_path}") from exc
        return _s3_config_cache


def load_s3_config(prefix: str = "s3", config_path: str | None = None) -> S3Config:
    """从 config.json 加载 S3 配置"""
    config = _load_config_file(config_path)
    if prefix not in config:
        raise S3Error(f"配置文件中缺少 '{prefix}' 配置项")
    s3 = config[prefix]
    bucket = s3.get("bucket", "").strip()
    region = s3.get("region", "").strip()
    access_key = s3.get("access_key", "").strip()
    secret_key = s3.get("secret_key", "").strip()
    endpoint_url = s3.get("endpoint_url") or None
    key_prefix = s3.get("prefix", "").strip()
    if not bucket:
        raise S3Error(f"配置项 '{prefix}.bucket' 不能为空")
    if not region:
        raise S3Error(f"配置项 '{prefix}.region' 不能为空")
    if not access_key:
        raise S3Error(f"配置项 '{prefix}.access_key' 不能为空")
    if not secret_key:
        raise S3Error(f"配置项 '{prefix}.secret_key' 不能为空")
    return S3Config(
        bucket=bucket,
        region=region,
        access_key=access_key,
        secret_key=secret_key,
        endpoint_url=endpoint_url,
        prefix=key_prefix,
    )


def _get_s3_client(config: S3Config) -> Any:
    try:
        import boto3
    except ImportError as exc:
        raise S3Error("缺少依赖 boto3，请运行: uv add boto3") from exc

    key = (config.bucket, config.region, config.access_key, config.endpoint_url)
    with _s3_client_lock:
        client = _s3_client_cache.get(key)
        if client is None:
            kwargs: dict[str, Any] = {
                "region_name": config.region,
                "aws_access_key_id": config.access_key,
                "aws_secret_access_key": config.secret_key,
            }
            if config.endpoint_url:
                kwargs["endpoint_url"] = config.endpoint_url
            client = boto3.client("s3", **kwargs)
            _s3_client_cache[key] = client
    return client


def _build_key(s3_key: str, config: S3Config) -> str:
    if config.prefix:
        return f"{config.prefix.rstrip('/')}/{s3_key.lstrip('/')}"
    return s3_key


def upload_file(
    local_path: str,
    s3_key: str,
    config: S3Config | None = None,
    content_type: str | None = None,
    acl: str | None = None,
    extra_args: dict[str, Any] | None = None,
) -> str:
    """
    上传本地文件到 S3

    Args:
        local_path: 本地文件路径
        s3_key: S3 对象键（不含 prefix）
        config: S3 配置，为 None 时从 config.json 加载
        content_type: 文件 MIME 类型，如 "image/png"
        acl: 访问控制，如 "public-read" 或 "private"
        extra_args: 传递给 boto3 upload_file 的额外参数

    Returns:
        上传后的 S3 对象键（含 prefix）
    """
    if not os.path.exists(local_path):
        raise S3Error(f"文件不存在: {local_path}")

    current_config = config or load_s3_config()
    client = _get_s3_client(current_config)
    full_key = _build_key(s3_key, current_config)

    args: dict[str, Any] = extra_args.copy() if extra_args else {}
    if content_type:
        args["ContentType"] = content_type
    if acl:
        args["ACL"] = acl

    try:
        client.upload_file(local_path, current_config.bucket, full_key, ExtraArgs=args or None)
    except Exception as exc:
        raise S3Error(f"上传文件失败 ({local_path} -> s3://{current_config.bucket}/{full_key}): {exc}") from exc

    return full_key


def upload_bytes(
    data: bytes | BinaryIO,
    s3_key: str,
    config: S3Config | None = None,
    content_type: str | None = None,
    acl: str | None = None,
    extra_args: dict[str, Any] | None = None,
) -> str:
    """
    上传字节数据或文件对象到 S3

    Args:
        data: bytes 或 file-like 对象
        s3_key: S3 对象键（不含 prefix）
        config: S3 配置，为 None 时从 config.json 加载
        content_type: 文件 MIME 类型
        acl: 访问控制
        extra_args: 传递给 boto3 put_object 的额外参数

    Returns:
        上传后的 S3 对象键（含 prefix）
    """
    current_config = config or load_s3_config()
    client = _get_s3_client(current_config)
    full_key = _build_key(s3_key, current_config)

    kwargs: dict[str, Any] = {
        "Bucket": current_config.bucket,
        "Key": full_key,
        "Body": data,
    }
    if content_type:
        kwargs["ContentType"] = content_type
    if acl:
        kwargs["ACL"] = acl
    if extra_args:
        kwargs.update(extra_args)

    try:
        client.put_object(**kwargs)
    except Exception as exc:
        raise S3Error(f"上传数据失败 (s3://{current_config.bucket}/{full_key}): {exc}") from exc

    return full_key


def get_public_url(s3_key: str, config: S3Config | None = None) -> str:
    """
    获取 S3 对象的公开访问 URL（适用于 public-read 对象）

    Args:
        s3_key: S3 对象键（不含 prefix）
        config: S3 配置，为 None 时从 config.json 加载

    Returns:
        公开访问 URL
    """
    current_config = config or load_s3_config()
    full_key = _build_key(s3_key, current_config)

    if current_config.endpoint_url:
        base = current_config.endpoint_url.rstrip("/")
        return f"{base}/{current_config.bucket}/{full_key}"

    return f"https://{current_config.bucket}.s3.{current_config.region}.amazonaws.com/{full_key}"


def get_presigned_url(
    s3_key: str,
    config: S3Config | None = None,
    expires_in: int = 3600,
) -> str:
    """
    生成 S3 对象的预签名 URL（适用于私有对象临时访问）

    Args:
        s3_key: S3 对象键（不含 prefix）
        config: S3 配置，为 None 时从 config.json 加载
        expires_in: URL 有效期（秒），默认 3600

    Returns:
        预签名 URL
    """
    current_config = config or load_s3_config()
    client = _get_s3_client(current_config)
    full_key = _build_key(s3_key, current_config)

    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": current_config.bucket, "Key": full_key},
            ExpiresIn=expires_in,
        )
    except Exception as exc:
        raise S3Error(f"生成预签名 URL 失败: {exc}") from exc

    return url
