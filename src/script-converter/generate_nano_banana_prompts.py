import argparse
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.append(str(PROJECT_ROOT / "src"))

from util.llm import AIAPIError, call_ai_chat_completion, extract_first_message_content, load_ai_api_config

load_dotenv(PROJECT_ROOT / ".env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@dataclass
class ChapterContext:
    chapter: str
    text: str


@dataclass
class ChapterAnalysisTask:
    """Represents a single chapter analysis task."""
    chapter: ChapterContext
    status: str = "PENDING"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration: float = 0.0


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(value: str) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def extract_json_object(raw: str) -> Dict[str, Any]:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(text[start : end + 1])
        if isinstance(data, dict):
            return data
    raise ValueError("LLM 输出不是有效 JSON 对象")


def trim_words(text: str, max_words: int) -> str:
    words = normalize_text(text).split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])


def pick_chapter_text(chapter_dir: Path, max_words: int) -> str:
    candidates = [
        chapter_dir / "adapted.txt",
        chapter_dir / "script_llm.txt",
        chapter_dir / "storyboard.txt",
    ]
    for path in candidates:
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            compact = trim_words(raw, max_words=max_words)
            if compact:
                return compact
    return ""


def collect_chapters(rewrite_dir: Path, max_words_per_chapter: int, max_chapters: int) -> List[ChapterContext]:
    chapters: List[ChapterContext] = []
    for chapter_dir in sorted(rewrite_dir.iterdir()):
        if not chapter_dir.is_dir():
            continue
        lowered = chapter_dir.name.lower()
        if "author" in lowered or "note" in lowered:
            continue
        text = pick_chapter_text(chapter_dir, max_words=max_words_per_chapter)
        if not text:
            continue
        chapters.append(ChapterContext(chapter=chapter_dir.name, text=text))
        if max_chapters > 0 and len(chapters) >= max_chapters:
            break
    return chapters


def load_merged_profile(merged_dir: Path, top_entity_limit: int) -> Dict[str, Any]:
    merged_entities_path = merged_dir / "merged_entities.json"
    name_map_path = merged_dir / "name_map.json"
    merged_entities = load_json(merged_entities_path)
    name_map = load_json(name_map_path) if name_map_path.exists() else {}
    entities = merged_entities.get("entities", []) if isinstance(merged_entities, dict) else []
    normalized_entities: List[Dict[str, Any]] = []
    for item in entities:
        if not isinstance(item, dict):
            continue
        canonical_name = normalize_text(str(item.get("canonical_name", "")))
        if not canonical_name:
            continue
        aliases = item.get("aliases", [])
        valid_aliases = [normalize_text(str(v)) for v in aliases if normalize_text(str(v))]
        mentions = int(item.get("mentions", 0) or 0)
        entity_type = normalize_text(str(item.get("type", "unknown"))) or "unknown"
        target_name = canonical_name
        if isinstance(name_map, dict) and isinstance(name_map.get(canonical_name), str):
            target_name = normalize_text(name_map.get(canonical_name, canonical_name))
        normalized_entities.append(
            {
                "canonical_name": canonical_name,
                "target_name": target_name,
                "type": entity_type,
                "mentions": mentions,
                "aliases": valid_aliases[:8],
            }
        )
    normalized_entities.sort(key=lambda x: x["mentions"], reverse=True)
    return {"entities": normalized_entities[:top_entity_limit]}


def llm_chapter_analysis(
    api_config: Any,
    chapter: ChapterContext,
    merged_profile: Dict[str, Any],
    max_tokens: int,
    temperature: float,
    max_retries: int = 3,
) -> Dict[str, Any]:
    system_prompt = """你是影视世界观分析师。你将读取章节正文并输出 JSON，不要输出任何额外文本。
JSON 字段要求：
{
  "chapter": "章节名",
  "summary": "80~150字剧情摘要",
  "turning_points": ["最多6条关键转折"],
  "character_states": [{"name":"规范名","state":"当前状态","goal":"显性目标","tension":"冲突"}],
  "locations": ["地点规范名或稳定描述"],
  "visual_motifs": ["最多6条可视化意象"],
  "style_signals": ["镜头气质/光影/情绪关键词，最多8条"]
}
规则：
1) 优先使用规范名（canonical_name）；
2) 只保留对视觉提示词有价值的信息；
3) 输出必须是合法 JSON 对象。"""
    user_prompt = json.dumps(
        {
            "chapter": chapter.chapter,
            "entities_profile": merged_profile.get("entities", []),
            "chapter_text": chapter.text,
        },
        ensure_ascii=False,
    )

    last_error = None
    last_content = None

    for attempt in range(max_retries):
        try:
            response = call_ai_chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                config=api_config,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = extract_first_message_content(response)
            last_content = content
            return extract_json_object(content)
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            logger.warning(f"章节分析 JSON 解析失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
        except AIAPIError as e:
            raise AIAPIError(f"章节分析 API 调用失败: {e}") from e

    # 重试失败后保存原始响应
    if last_content:
        error_file = PROJECT_ROOT / "output" / f"error_chapter_{chapter.chapter}_{int(time.time())}.txt"
        error_file.parent.mkdir(parents=True, exist_ok=True)
        error_file.write_text(last_content, encoding="utf-8")
        logger.error(f"章节分析重试 {max_retries} 次均失败，已保存原始响应到: {error_file}")

    raise AIAPIError(f"章节分析失败 (重试 {max_retries} 次): {last_error}") from last_error


def llm_character_prompt_synthesis(
    api_config: Any,
    merged_profile: Dict[str, Any],
    chapter_analyses: List[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Generate character prompts only."""
    system_prompt = """You are a senior character designer for nano banana.
Output ONLY a JSON object with character prompts. No explanatory text.

**CRITICAL RULES**:
1. Character prompts MUST generate turnaround sheets (one image with front, side, back views)
2. ALL outputs MUST be in ENGLISH ONLY
3. ALL prompts MUST be PHOTOREALISTIC style, like real world, NOT cartoon/anime/comic/illustrated style

JSON Structure:
{
  "global_style_prompt": "Unified photorealistic style prompt in English, must include: photorealistic, realistic, highly detailed, like real world",
  "continuity_rules": ["Cross-chapter continuity rules in English"],
  "character_prompt_library": [
    {
      "canonical_name": "",
      "target_name": "",
      "role_tier": "lead|supporting|minor",
      "story_function": "Description in English",
      "appearance_core": ["Stable appearance points in English, photorealistic style"],
      "turnaround_prompt": "Character turnaround prompt in English, MUST include: character turnaround sheet, three views (front view, side view, back view), T-pose, full body, white background, character design, reference sheet, photorealistic, realistic, highly detailed",
      "negative_prompt": "Things to avoid in English, MUST include: cartoon, anime, comic, illustrated, stylized, drawing, painting"
    }
  ]
}
Rules:
1) Generate prompts for ALL characters in the entities_profile that have type='person'
2) MUST use the exact canonical_name and target_name from entities_profile, do NOT create new names
3) Use turnaround_prompt (singular) for one image with three views
4) Keep descriptions concise, comma-separated English phrases
5) Maintain consistent appearance
6) Output MUST be valid JSON
7) ALL TEXT MUST BE IN ENGLISH
8) ALL prompts MUST be PHOTOREALISTIC, like real world, NOT cartoon/anime/comic style"""

    synthesis_input = {
        "entities_profile": merged_profile.get("entities", []),
        "chapter_analyses": chapter_analyses,
    }

    last_error = None
    last_content = None

    for attempt in range(max_retries):
        try:
            response = call_ai_chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(synthesis_input, ensure_ascii=False)},
                ],
                config=api_config,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = extract_first_message_content(response)
            last_content = content
            return extract_json_object(content)
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            logger.warning(f"角色综合 JSON 解析失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
        except AIAPIError as e:
            raise AIAPIError(f"角色综合 API 调用失败: {e}") from e

    if last_content:
        error_file = PROJECT_ROOT / "output" / f"error_character_synthesis_{int(time.time())}.txt"
        error_file.parent.mkdir(parents=True, exist_ok=True)
        error_file.write_text(last_content, encoding="utf-8")
        logger.error(f"角色综合重试 {max_retries} 次均失败，已保存原始响应到: {error_file}")

    raise AIAPIError(f"角色综合失败 (重试 {max_retries} 次): {last_error}") from last_error


def llm_scene_prompt_synthesis(
    api_config: Any,
    merged_profile: Dict[str, Any],
    chapter_analyses: List[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Generate scene prompts only."""
    system_prompt = """You are a senior environment concept artist for nano banana.
Output ONLY a JSON object with scene prompts. No explanatory text.

**CRITICAL RULES**:
1. Scene prompts MUST NOT contain any people, only environment, architecture, lighting, atmosphere
2. ALL outputs MUST be in ENGLISH ONLY
3. ALL prompts MUST be PHOTOREALISTIC style, like real world, NOT cartoon/anime/comic/illustrated style

JSON Structure:
{
  "scene_prompt_library": [
    {
      "scene_id": "S01",
      "title": "Scene template name in English",
      "when_to_use": "Applicable story stage in English",
      "locations": ["Location names in English"],
      "mood": "Mood in English",
      "camera": "Camera language in English",
      "lighting": "Lighting design in English",
      "prompt": "Pure environment description in English, no people, environment concept art, no people, no characters, photorealistic, realistic, highly detailed",
      "negative_prompt": "people, characters, humans, person, man, woman, child, cartoon, anime, comic, illustrated, stylized, drawing, painting"
    }
  ]
}
Rules:
1) Generate scene templates for ALL locations in chapter_analyses that have type='location'
2) MUST use the exact location names from chapter_analyses, do NOT create new location names
3) Scene prompts MUST NOT contain people, only environment
4) Keep descriptions concise, comma-separated English phrases
5) Output MUST be valid JSON
6) ALL TEXT MUST BE IN ENGLISH
7) ALL prompts MUST be PHOTOREALISTIC, like real world, NOT cartoon/anime/comic style"""

    synthesis_input = {
        "entities_profile": merged_profile.get("entities", []),
        "chapter_analyses": chapter_analyses,
    }

    last_error = None
    last_content = None

    for attempt in range(max_retries):
        try:
            response = call_ai_chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(synthesis_input, ensure_ascii=False)},
                ],
                config=api_config,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = extract_first_message_content(response)
            last_content = content
            return extract_json_object(content)
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            logger.warning(f"场景综合 JSON 解析失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
        except AIAPIError as e:
            raise AIAPIError(f"场景综合 API 调用失败: {e}") from e

    if last_content:
        error_file = PROJECT_ROOT / "output" / f"error_scene_synthesis_{int(time.time())}.txt"
        error_file.parent.mkdir(parents=True, exist_ok=True)
        error_file.write_text(last_content, encoding="utf-8")
        logger.error(f"场景综合重试 {max_retries} 次均失败，已保存原始响应到: {error_file}")

    raise AIAPIError(f"场景综合失败 (重试 {max_retries} 次): {last_error}") from last_error


def llm_global_prompt_synthesis(
    api_config: Any,
    merged_profile: Dict[str, Any],
    chapter_analyses: List[Dict[str, Any]],
    max_tokens: int,
    temperature: float,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """Try to generate all prompts in one call, fallback to split generation if needed."""
    system_prompt = """You are a senior storyboard art director responsible for generating globally unified character and scene prompts for nano banana.
You must output a JSON object without any explanatory text.
Goal: Create unified settings and reusable prompt libraries across chapters, not split by chapter.

**CRITICAL RULES**:
1. Character prompts MUST generate turnaround sheets (one image containing front, side, back views)
2. Scene prompts MUST NOT contain any people, only describe environment, architecture, lighting, atmosphere
3. ALL prompts and outputs MUST be in ENGLISH ONLY
4. ALL prompts MUST be PHOTOREALISTIC style, like real world, NOT cartoon/anime/comic/illustrated style

JSON Structure:
{
  "model": "nano banana",
  "global_style_prompt": "Unified photorealistic style prompt in English, must include: photorealistic, realistic, highly detailed, like real world",
  "continuity_rules": ["Cross-chapter continuity rules in English"],
  "character_prompt_library": [
    {
      "canonical_name": "",
      "target_name": "",
      "role_tier": "lead|supporting|minor",
      "story_function": "Description in English",
      "appearance_core": ["Stable appearance points in English, photorealistic style"],
      "turnaround_prompt": "Character turnaround prompt in English, MUST include: character turnaround sheet, three views (front view, side view, back view), T-pose, full body, white background, character design, reference sheet, photorealistic, realistic, highly detailed",
      "negative_prompt": "Things to avoid in English, MUST include: cartoon, anime, comic, illustrated, stylized, drawing, painting"
    }
  ],
  "scene_prompt_library": [
    {
      "scene_id": "S01",
      "title": "Scene template name in English",
      "when_to_use": "Applicable story stage in English",
      "locations": ["Location names in English"],
      "mood": "Mood in English",
      "camera": "Camera language in English",
      "lighting": "Lighting design in English",
      "prompt": "Pure environment description in English, no people, environment concept art, no people, no characters, photorealistic, realistic, highly detailed",
      "negative_prompt": "people, characters, humans, person, man, woman, child, cartoon, anime, comic, illustrated, stylized, drawing, painting"
    }
  ]
}
Rules:
1) Character library MUST include ALL characters in entities_profile that have type='person'
2) MUST use the exact canonical_name and target_name from entities_profile, do NOT create new names
3) Character prompts MUST use turnaround_prompt (singular) to generate one image with three views
4) Scene library MUST include ALL locations from entities_profile that have type='location'
5) MUST use the exact location names from entities_profile, do NOT create new location names
6) Scene prompts MUST NOT contain people, only environment
7) Keep descriptions concise and focused, use comma-separated English phrases
8) Maintain consistent character appearance, no drift across chapters
9) Output MUST be valid JSON
10) ALL TEXT MUST BE IN ENGLISH
11) ALL prompts MUST be PHOTOREALISTIC, like real world, NOT cartoon/anime/comic style"""

    synthesis_input = {
        "entities_profile": merged_profile.get("entities", []),
        "chapter_analyses": chapter_analyses,
    }

    last_error = None
    last_content = None

    # Try unified generation first
    for attempt in range(max_retries):
        try:
            response = call_ai_chat_completion(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(synthesis_input, ensure_ascii=False)},
                ],
                config=api_config,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = extract_first_message_content(response)
            last_content = content
            result = extract_json_object(content)
            logger.info("成功一次性生成完整提示词")
            return result
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            logger.warning(f"全局综合 JSON 解析失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
        except AIAPIError as e:
            raise AIAPIError(f"全局综合 API 调用失败: {e}") from e

    # If unified generation failed, try split generation
    logger.warning("一次性生成失败，尝试分批生成（角色 + 场景）")

    try:
        # Generate character prompts
        logger.info("第 1/2 步：生成角色提示词库")
        character_result = llm_character_prompt_synthesis(
            api_config=api_config,
            merged_profile=merged_profile,
            chapter_analyses=chapter_analyses,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # Generate scene prompts
        logger.info("第 2/2 步：生成场景提示词库")
        scene_result = llm_scene_prompt_synthesis(
            api_config=api_config,
            merged_profile=merged_profile,
            chapter_analyses=chapter_analyses,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # Merge results
        merged_result = {
            "model": "nano banana",
            "global_style_prompt": character_result.get("global_style_prompt", ""),
            "continuity_rules": character_result.get("continuity_rules", []),
            "character_prompt_library": character_result.get("character_prompt_library", []),
            "scene_prompt_library": scene_result.get("scene_prompt_library", []),
        }

        logger.info("成功分批生成并合并提示词")
        return merged_result

    except AIAPIError as e:
        # If split generation also failed, save the last error
        if last_content:
            error_file = PROJECT_ROOT / "output" / f"error_global_synthesis_{int(time.time())}.txt"
            error_file.parent.mkdir(parents=True, exist_ok=True)
            error_file.write_text(last_content, encoding="utf-8")
            logger.error(f"全局综合重试 {max_retries} 次均失败，已保存原始响应到: {error_file}")
        raise AIAPIError(f"全局综合失败 (包括分批生成): {e}") from e


def _process_chapter_analysis(
    task: ChapterAnalysisTask,
    api_config: Any,
    merged_profile: Dict[str, Any],
    max_tokens: int,
    temperature: float,
) -> ChapterAnalysisTask:
    """Process a single chapter analysis task."""
    start_time = time.time()

    logger.info(f"Processing chapter: {task.chapter.chapter}")

    try:
        result = llm_chapter_analysis(
            api_config=api_config,
            chapter=task.chapter,
            merged_profile=merged_profile,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        task.result = result
        task.status = "COMPLETED"
        logger.info(f"Successfully analyzed: {task.chapter.chapter}")
    except AIAPIError as e:
        task.status = "FAILED"
        task.error = str(e)
        logger.error(f"AI API Error analyzing {task.chapter.chapter}: {e}")
    except Exception as e:
        task.status = "FAILED"
        task.error = str(e)
        logger.error(f"Unexpected error analyzing {task.chapter.chapter}: {e}")

    task.duration = time.time() - start_time
    return task


def run(
    rewrite_dir: Path,
    merged_dir: Path,
    output_path: Path,
    chapter_analysis_path: Optional[Path],
    max_chapters: int,
    max_words_per_chapter: int,
    top_entity_limit: int,
    chapter_max_tokens: int,
    synthesis_max_tokens: int,
    temperature: float,
    max_workers: int = 4,
    force: bool = False,
) -> None:
    merged_profile = load_merged_profile(merged_dir=merged_dir, top_entity_limit=top_entity_limit)
    api_config = load_ai_api_config()

    # Check if chapter analysis already exists and can be reused
    chapter_analyses: List[Dict[str, Any]] = []
    skip_chapter_analysis = False

    if not force and chapter_analysis_path and chapter_analysis_path.exists():
        try:
            logger.info(f"发现已有章节分析结果: {chapter_analysis_path}")
            existing_data = load_json(chapter_analysis_path)
            if isinstance(existing_data, dict) and "chapters" in existing_data:
                chapter_analyses = existing_data["chapters"]
                if chapter_analyses:
                    skip_chapter_analysis = True
                    logger.info(f"加载了 {len(chapter_analyses)} 个已完成的章节分析，跳过章节分析阶段")
                    logger.info("提示：使用 --force 参数可强制重新分析所有章节")
        except Exception as e:
            logger.warning(f"加载已有章节分析失败: {e}，将重新分析")
            skip_chapter_analysis = False

    # Perform chapter analysis if needed
    if not skip_chapter_analysis:
        chapters = collect_chapters(
            rewrite_dir=rewrite_dir,
            max_words_per_chapter=max_words_per_chapter,
            max_chapters=max_chapters,
        )
        if not chapters:
            raise RuntimeError("未找到可用章节文本")

        # Create tasks for concurrent processing
        tasks = [ChapterAnalysisTask(chapter=chapter) for chapter in chapters]
        logger.info(f"Found {len(tasks)} chapters to analyze.")

        # Process chapters concurrently
        chapter_analyses = []
        completed_count = 0
        failed_count = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {
                executor.submit(
                    _process_chapter_analysis,
                    task,
                    api_config,
                    merged_profile,
                    chapter_max_tokens,
                    temperature,
                ): task
                for task in tasks
            }

            for future in as_completed(future_to_task):
                task = future.result()
                if task.status == "COMPLETED":
                    completed_count += 1
                    chapter_analyses.append(task.result)
                elif task.status == "FAILED":
                    failed_count += 1
                    logger.warning(f"Skipping failed chapter: {task.chapter.chapter}")

        logger.info(f"Chapter analysis finished: {completed_count} completed, {failed_count} failed.")

        if not chapter_analyses:
            raise RuntimeError("所有章节分析均失败，无法继续")

        if chapter_analysis_path is not None:
            chapter_analysis_path.write_text(
                json.dumps({"chapters": chapter_analyses}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info(f"已写入章节分析: {chapter_analysis_path}")
    final_payload = llm_global_prompt_synthesis(
        api_config=api_config,
        merged_profile=merged_profile,
        chapter_analyses=chapter_analyses,
        max_tokens=synthesis_max_tokens,
        temperature=temperature,
    )
    if "model" not in final_payload:
        final_payload["model"] = "nano banana"

    # Extract chapter names from chapter_analyses
    chapter_names = [analysis.get("chapter", "unknown") for analysis in chapter_analyses]

    final_payload["meta"] = {
        "source_mode": "global_cross_chapter_llm",
        "chapters_used": chapter_names,
        "merged_dir": str(merged_dir),
    }
    output_path.write_text(json.dumps(final_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"已写入全局提示词: {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Use LLM to generate global nano banana prompts across chapters.")
    parser.add_argument(
        "--rewrite-dir",
        type=str,
        default="/Users/hjr/Desktop/script-fusion/output/1fc071a6_- BITE ME , ᶻᵒᵐᵇⁱᵉˢ⁴ - $ - Wattpad/adapted/rewrite",
    )
    parser.add_argument(
        "--merged-dir",
        type=str,
        default="/Users/hjr/Desktop/script-fusion/output/1fc071a6_- BITE ME , ᶻᵒᵐᵇⁱᵉˢ⁴ - $ - Wattpad/adapted/extract/_merged",
    )
    parser.add_argument(
        "--output-path",
        type=str,
        default="/Users/hjr/Desktop/script-fusion/output/1fc071a6_- BITE ME , ᶻᵒᵐᵇⁱᵉˢ⁴ - $ - Wattpad/adapted/rewrite/nano_banana_prompts_global_llm.json",
    )
    parser.add_argument(
        "--chapter-analysis-path",
        type=str,
        default="",
    )
    parser.add_argument("--max-chapters", type=int, default=0)
    parser.add_argument("--max-words-per-chapter", type=int, default=1000)
    parser.add_argument("--top-entity-limit", type=int, default=80)
    parser.add_argument("--chapter-max-tokens", type=int, default=1800)
    parser.add_argument("--synthesis-max-tokens", type=int, default=32768)
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of concurrent workers for chapter analysis (default: 4)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-run all tasks, ignoring existing results",
    )
    args = parser.parse_args()
    chapter_analysis_path = Path(args.chapter_analysis_path) if args.chapter_analysis_path else None

    try:
        run(
            rewrite_dir=Path(args.rewrite_dir),
            merged_dir=Path(args.merged_dir),
            output_path=Path(args.output_path),
            chapter_analysis_path=chapter_analysis_path,
            max_chapters=args.max_chapters,
            max_words_per_chapter=args.max_words_per_chapter,
            top_entity_limit=args.top_entity_limit,
            chapter_max_tokens=args.chapter_max_tokens,
            synthesis_max_tokens=args.synthesis_max_tokens,
            temperature=args.temperature,
            max_workers=args.workers,
            force=args.force,
        )
    except Exception as e:
        logger.critical(f"Application failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
