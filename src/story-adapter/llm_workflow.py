import json
import re

from util.llm import AIAPIError, call_ai_chat_completion, extract_first_message_content


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


def call_llm_json(messages, temperature, max_tokens):
    result = call_ai_chat_completion(messages=messages, temperature=temperature, max_tokens=max_tokens)
    content = extract_first_message_content(result)
    return parse_json_payload(content)


def llm_extract_chapter(chapter_title, chapter_text, story_meta, temperature, max_tokens):
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
    result = call_llm_json(messages=messages, temperature=temperature, max_tokens=max_tokens)
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


def llm_merge_entities(chapter_entities, temperature, max_tokens):
    messages = [
        {
            "role": "system",
            "content": (
                "你是实体去重助手。请合并同一角色/地点/组织的别名，只输出 JSON。"
                "输出结构："
                '{"entities":[{"canonical_name":"","type":"person|location|organization|other","aliases":[],"mentions":1}]}'
            ),
        },
        {"role": "user", "content": json.dumps({"entities": chapter_entities}, ensure_ascii=False)},
    ]
    result = call_llm_json(messages=messages, temperature=temperature, max_tokens=max_tokens)
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


def llm_build_name_map(entities, max_renames, temperature, max_tokens):
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
    result = call_llm_json(messages=messages, temperature=temperature, max_tokens=max_tokens)
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


def llm_rewrite_chapter(chapter_title, source_text, outline, name_map, story_meta, temperature, max_tokens):
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
                "必须使用 name_map 中的新名字。"
                "输出 JSON："
                '{"adapted_text":""}'
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    result = call_llm_json(messages=messages, temperature=temperature, max_tokens=max_tokens)
    adapted_text = str(result.get("adapted_text", "")).strip()
    if not adapted_text:
        raise AIAPIError(f"章节改写为空: {chapter_title}")
    return adapted_text
