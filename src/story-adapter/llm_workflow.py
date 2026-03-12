import json
import random
import re
import time

from util.llm import AIAPIError, call_ai_chat_completion, extract_first_message_content


def _extract_retry_after_seconds(error):
    message = str(error).lower()
    patterns = [
        r"retry-after[:=\s]+(\d+(?:\.\d+)?)",
        r"retry after[:=\s]+(\d+(?:\.\d+)?)",
        r"after\s+(\d+(?:\.\d+)?)\s*s",
    ]
    for pattern in patterns:
        matched = re.search(pattern, message)
        if matched:
            try:
                return max(0.0, float(matched.group(1)))
            except (TypeError, ValueError):
                return None
    return None


def _is_retryable_error(error):
    message = str(error).lower()
    retryable_signals = [
        "timeout",
        "timed out",
        "read timed out",
        "request timed out",
        "超时",
        "apitimeouterror",
        "apiconnectionerror",
        "connection",
        "429",
        "rate limit",
        "too many requests",
        "500",
        "502",
        "503",
        "504",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "temporarily unavailable",
        "overloaded",
        "try again",
        "remote disconnected",
    ]
    return any(signal in message for signal in retryable_signals)


def parse_json_payload(content):
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def call_llm_json(
    messages,
    temperature,
    max_tokens,
    base_delay=1.5,
    max_delay=60.0,
    jitter=1.0,
    max_retries=10,
    status_callback=None,
    request_name="",
):
    attempt = 0
    while True:
        try:
            if status_callback:
                status_callback({"request_name": request_name, "phase": "attempt", "attempt": attempt + 1})
            result = call_ai_chat_completion(messages=messages, temperature=temperature, max_tokens=max_tokens)
            content = extract_first_message_content(result)
            if status_callback:
                status_callback({"request_name": request_name, "phase": "success", "attempt": attempt + 1})
            return parse_json_payload(content)
        except AIAPIError as error:
            if not _is_retryable_error(error):
                if status_callback:
                    status_callback(
                        {
                            "request_name": request_name,
                            "phase": "error",
                            "attempt": attempt + 1,
                            "error": f"NON_RETRYABLE: {error}",
                        }
                    )
                raise
            if max_retries is not None and attempt >= max_retries:
                if status_callback:
                    status_callback(
                        {
                            "request_name": request_name,
                            "phase": "retry_limit_reached",
                            "attempt": attempt + 1,
                            "error": str(error),
                        }
                    )
                raise AIAPIError(f"LLM 请求重试超限({max_retries + 1}次): {error}") from error
            retry_after = _extract_retry_after_seconds(error)
            sleep_seconds = (
                retry_after
                if retry_after is not None
                else min(max_delay, base_delay * (2**attempt))
            )
            sleep_seconds += random.uniform(0, jitter)
            if status_callback:
                status_callback(
                    {
                        "request_name": request_name,
                        "phase": "retry_wait",
                        "attempt": attempt + 1,
                        "sleep_seconds": round(float(sleep_seconds), 3),
                        "error": str(error),
                    }
                )
            time.sleep(sleep_seconds)
            attempt += 1


def call_llm_text(
    messages,
    temperature,
    max_tokens,
    base_delay=1.5,
    max_delay=60.0,
    jitter=1.0,
    max_retries=10,
    status_callback=None,
    request_name="",
):
    attempt = 0
    while True:
        try:
            if status_callback:
                status_callback({"request_name": request_name, "phase": "attempt", "attempt": attempt + 1})
            result = call_ai_chat_completion(messages=messages, temperature=temperature, max_tokens=max_tokens)
            content = extract_first_message_content(result)
            if status_callback:
                status_callback({"request_name": request_name, "phase": "success", "attempt": attempt + 1})
            return content
        except AIAPIError as error:
            if not _is_retryable_error(error):
                if status_callback:
                    status_callback(
                        {
                            "request_name": request_name,
                            "phase": "error",
                            "attempt": attempt + 1,
                            "error": f"NON_RETRYABLE: {error}",
                        }
                    )
                raise
            if max_retries is not None and attempt >= max_retries:
                if status_callback:
                    status_callback(
                        {
                            "request_name": request_name,
                            "phase": "retry_limit_reached",
                            "attempt": attempt + 1,
                            "error": str(error),
                        }
                    )
                raise AIAPIError(f"LLM 请求重试超限({max_retries + 1}次): {error}") from error
            retry_after = _extract_retry_after_seconds(error)
            sleep_seconds = (
                retry_after
                if retry_after is not None
                else min(max_delay, base_delay * (2**attempt))
            )
            sleep_seconds += random.uniform(0, jitter)
            if status_callback:
                status_callback(
                    {
                        "request_name": request_name,
                        "phase": "retry_wait",
                        "attempt": attempt + 1,
                        "sleep_seconds": round(float(sleep_seconds), 3),
                        "error": str(error),
                    }
                )
            time.sleep(sleep_seconds)
            attempt += 1


def llm_extract_chapter(chapter_title, chapter_text, story_meta, temperature, max_tokens, status_callback=None):
    payload = {
        "story_title": story_meta.get("title"),
        "story_description": story_meta.get("description"),
        "chapter_title": chapter_title,
        "chapter_text": chapter_text,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是小说编辑助手。请只输出 JSON，不要输出任何额外文本。"
                "目标是对授权文本做原创改编准备。"
                "如果章节中没有可提取实体，entities 可以返回空数组。"
                "返回结构必须是："
                '{"entities":[{"name":"","type":"person|location|organization|other","aliases":[],"mentions":1}],'
                '"outline":{"opening":"","conflict":"","turning_point":"","ending":""}}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]
    result = call_llm_json(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        status_callback=status_callback,
        request_name="extract",
    )
    entities = result.get("entities", [])
    outline = result.get("outline", {})
    if not isinstance(entities, list):
        entities = []
    if not isinstance(outline, dict):
        outline = {}
    normalized_entities = []
    for item in entities:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list):
            aliases = []
        mentions = item.get("mentions", 1)
        try:
            mentions = int(mentions)
        except Exception:
            mentions = 1
        normalized_entities.append(
            {
                "name": name,
                "type": str(item.get("type", "other")).strip() or "other",
                "aliases": [str(alias).strip() for alias in aliases if str(alias).strip()],
                "mentions": max(1, mentions),
            }
        )
    outline = {
        "opening": str(outline.get("opening", "")).strip(),
        "conflict": str(outline.get("conflict", "")).strip(),
        "turning_point": str(outline.get("turning_point", "")).strip(),
        "ending": str(outline.get("ending", "")).strip(),
    }
    return {"entities": normalized_entities, "outline": outline}


def llm_merge_entities(chapter_entities, temperature, max_tokens, status_callback=None):
    merge_inputs = []
    for item in chapter_entities:
        if not isinstance(item, dict):
            continue
        mentions = item.get("mentions", 1)
        try:
            mentions = int(mentions)
        except Exception:
            mentions = 1
        merge_inputs.append(
            {
                "name": str(item.get("name", "")).strip(),
                "type": str(item.get("type", "other")).strip() or "other",
                "aliases": [str(alias).strip() for alias in item.get("aliases", []) if str(alias).strip()],
                "mentions": max(1, mentions),
                "scene": str(item.get("scene", "")).strip(),
                "chapter_file": str(item.get("chapter_file", "")).strip(),
            }
        )
    messages = [
        {
            "role": "system",
            "content": (
                "你是实体去重助手。输入只包含实体名字、别名、类型、出现次数和场景标识。"
                "请仅基于这些字段合并同一角色/地点/组织的别名，只输出 JSON。"
                "不确定时不要强行合并。"
                "输出结构："
                '{"entities":[{"canonical_name":"","type":"person|location|organization|other","aliases":[],"mentions":1}]}'
            ),
        },
        {"role": "user", "content": json.dumps({"entities": merge_inputs}, ensure_ascii=False)},
    ]
    result = call_llm_json(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        status_callback=status_callback,
        request_name="merge_entities",
    )
    entities = result.get("entities", [])
    if not isinstance(entities, list):
        raise AIAPIError("实体合并响应缺少 entities 列表")
    normalized = []
    for item in entities:
        if not isinstance(item, dict):
            continue
        canonical_name = str(item.get("canonical_name", "")).strip()
        if not canonical_name:
            continue
        aliases = item.get("aliases", [])
        if not isinstance(aliases, list):
            aliases = []
        mentions = item.get("mentions", 1)
        try:
            mentions = int(mentions)
        except Exception:
            mentions = 1
        normalized.append(
            {
                "canonical_name": canonical_name,
                "type": str(item.get("type", "other")).strip() or "other",
                "aliases": [str(alias).strip() for alias in aliases if str(alias).strip()],
                "mentions": max(1, mentions),
            }
        )
    if not normalized:
        raise AIAPIError("实体合并结果为空")
    normalized.sort(key=lambda item: (-item["mentions"], item["canonical_name"]))
    return normalized


def llm_build_name_map(entities, max_renames, temperature, max_tokens, status_callback=None):
    targets = entities[:max_renames]
    messages = [
        {
            "role": "system",
            "content": (
                "你是命名策划助手。请为输入实体生成新的专有名词，保持可读且不与原名重复。"
                "只输出 JSON："
                '{"name_map":{"原名":"新名"}}'
            ),
        },
        {"role": "user", "content": json.dumps({"entities": targets}, ensure_ascii=False)},
    ]
    result = call_llm_json(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        status_callback=status_callback,
        request_name="build_name_map",
    )
    name_map = result.get("name_map", {})
    if not isinstance(name_map, dict):
        raise AIAPIError("命名映射响应缺少 name_map")
    normalized = {}
    for source_name, target_name in name_map.items():
        source = str(source_name).strip()
        target = str(target_name).strip()
        if not source or not target:
            continue
        if source == target:
            continue
        normalized[source] = target
    if not normalized:
        raise AIAPIError("命名映射为空")
    return normalized


def llm_rewrite_chapter(
    chapter_title,
    source_text,
    outline,
    name_map,
    story_meta,
    temperature,
    max_tokens,
    status_callback=None,
):
    payload = {
        "story_title": story_meta.get("title"),
        "story_description": story_meta.get("description"),
        "chapter_title": chapter_title,
        "outline": outline,
        "name_map": name_map,
        "source_text": source_text,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是小说改编助手。对授权文本进行原创改编，保留剧情骨架但重新组织表达。"
                "改写语言必须与 source_text 原文语言一致，不要把英文改成中文，也不要把中文改成英文。"
                "必须使用 name_map 中的新名字。"
                "直接输出改写后的正文内容，不要输出 JSON，不要解释。"
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    adapted_text = call_llm_text(
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        status_callback=status_callback,
        request_name="rewrite",
    ).strip()
    if not adapted_text:
        raise AIAPIError(f"章节改写为空: {chapter_title}")
    return adapted_text
