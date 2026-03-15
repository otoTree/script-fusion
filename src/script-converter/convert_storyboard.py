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
from utils import Shot, clean_text_for_storyboard

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

load_dotenv(PROJECT_ROOT / ".env")


@dataclass
class StoryboardTask:
    chapter_dir: Path
    input_text_path: Path
    output_path: Path
    status: str = "PENDING"
    error: Optional[str] = None
    duration: float = 0.0


class StoryboardConverter:
    SYSTEM_PROMPT = """You are a professional storyboard writer.
Convert chapter text into storyboard shots.

Output format:
- Use markdown only.
- Do not use JSON.
- For each shot, strictly use this structure:

### SHOT <number>
SCENE_HEADING: <text>
CONTENT:
<cinematic visual description + dialogue, VO/O.S./V.O. allowed>
ENTITIES: <comma-separated canonical names>
LOCATIONS: <comma-separated canonical location names>

Rules:
1. Output language must be English only.
2. Every shot duration target is 15 seconds.
3. Keep sufficient density per shot, roughly 35-70 English words.
4. content must be filmable and specific, not summary narration.
5. entities and locations must use canonical names only (name_map keys).
6. Keep chronological order and continuous shot numbering.
"""
    MIN_WORDS_PER_SHOT = 35

    def __init__(
        self,
        max_workers: int = 4,
        force: bool = False,
        dry_run: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 4000,
        source_filename: str = "script_llm.txt",
    ):
        self.max_workers = max_workers
        self.force = force
        self.dry_run = dry_run
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.source_filename = source_filename
        self.target_to_canonical: Dict[str, str] = {}
        self.target_lower_to_canonical: Dict[str, str] = {}
        self.canonical_to_type: Dict[str, str] = {}
        self.canonical_lower_to_name: Dict[str, str] = {}
        self.metadata_loaded = False
        try:
            self.api_config = load_ai_api_config()
        except AIAPIError as e:
            logger.error(f"Failed to load AI API config: {e}")
            raise

    def _load_storyboard_metadata(self, adapted_dir: Path):
        if self.metadata_loaded:
            return
        entity_merge_path = adapted_dir / "entity_merge.json"
        name_map_path = adapted_dir / "name_map.json"
        if not entity_merge_path.exists() or not name_map_path.exists():
            logger.warning(f"Metadata files not found in {adapted_dir}")
            return
        with open(name_map_path, "r", encoding="utf-8") as f:
            name_map = json.load(f)
        with open(entity_merge_path, "r", encoding="utf-8") as f:
            merge_data = json.load(f)
        canonical_types: Dict[str, str] = {}
        for ent in merge_data.get("entities", []):
            name = ent.get("canonical_name")
            if isinstance(name, str) and name.strip():
                canonical_types[name] = ent.get("type", "unknown")
        for canonical, target in name_map.items():
            if not isinstance(canonical, str) or not canonical.strip():
                continue
            canonical = canonical.strip()
            self.canonical_to_type[canonical] = canonical_types.get(canonical, "unknown")
            self.canonical_lower_to_name[canonical.lower()] = canonical
            if isinstance(target, str) and target.strip():
                target = target.strip()
                self.target_to_canonical[target] = canonical
                self.target_lower_to_canonical[target.lower()] = canonical
        self.metadata_loaded = True
        logger.info(f"Loaded {len(self.canonical_to_type)} canonical entities for storyboard.")

    def _parse_markdown_shots(self, content: str) -> List[Dict[str, Any]]:
        text = content.strip()
        fenced = re.search(r"```(?:markdown|md)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        header_matches = list(re.finditer(r"(?m)^###\s*SHOT\s+\d+\s*$", text))
        if not header_matches:
            raise ValueError("Invalid markdown payload from LLM: no SHOT headers found")
        blocks: List[str] = []
        for i, match in enumerate(header_matches):
            start = match.end()
            end = header_matches[i + 1].start() if i + 1 < len(header_matches) else len(text)
            blocks.append(text[start:end].strip())
        shots: List[Dict[str, Any]] = []
        for block in blocks:
            scene_match = re.search(r"(?m)^SCENE_HEADING:\s*(.+)$", block)
            entities_match = re.search(r"(?m)^ENTITIES:\s*(.*)$", block)
            locations_match = re.search(r"(?m)^LOCATIONS:\s*(.*)$", block)
            content_match = re.search(
                r"(?ms)^CONTENT:\s*\n(.*?)(?=^\s*ENTITIES:|^\s*LOCATIONS:|\Z)",
                block,
            )
            if not scene_match or not content_match:
                continue
            scene_heading = scene_match.group(1).strip()
            shot_content = content_match.group(1).strip()
            entities_raw = entities_match.group(1).strip() if entities_match else ""
            locations_raw = locations_match.group(1).strip() if locations_match else ""
            entities = [item.strip() for item in entities_raw.split(",") if item.strip()]
            locations = [item.strip() for item in locations_raw.split(",") if item.strip()]
            shots.append(
                {
                    "scene_heading": scene_heading,
                    "content": shot_content,
                    "entities": entities,
                    "locations": locations,
                }
            )
        if not shots:
            raise ValueError("Invalid markdown payload from LLM: no valid shot blocks parsed")
        return shots

    def _normalize_name(self, name: str) -> Optional[str]:
        if not isinstance(name, str):
            return None
        clean = name.strip()
        if not clean:
            return None
        if clean in self.canonical_to_type:
            return clean
        lower = clean.lower()
        if lower in self.canonical_lower_to_name:
            return self.canonical_lower_to_name[lower]
        if clean in self.target_to_canonical:
            return self.target_to_canonical[clean]
        if lower in self.target_lower_to_canonical:
            return self.target_lower_to_canonical[lower]
        return None

    def _normalize_shots(self, raw_shots: List[Dict[str, Any]]) -> List[Shot]:
        normalized: List[Shot] = []
        next_id = 1
        for item in raw_shots:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            scene_heading = str(item.get("scene_heading", "SCENE")).strip() or "SCENE"
            raw_entities = item.get("entities", [])
            raw_locations = item.get("locations", [])
            entities: List[str] = []
            locations: List[str] = []
            if isinstance(raw_entities, list):
                for name in raw_entities:
                    mapped = self._normalize_name(str(name))
                    if mapped and mapped not in entities:
                        entities.append(mapped)
            if isinstance(raw_locations, list):
                for name in raw_locations:
                    mapped = self._normalize_name(str(name))
                    if mapped and self.canonical_to_type.get(mapped) == "location" and mapped not in locations:
                        locations.append(mapped)
            if not locations:
                for name in entities:
                    if self.canonical_to_type.get(name) == "location" and name not in locations:
                        locations.append(name)
            normalized.append(
                Shot(
                    id=next_id,
                    duration=15.0,
                    content=content,
                    scene_heading=scene_heading,
                    entities=sorted(entities),
                    locations=sorted(locations),
                )
            )
            next_id += 1
        return normalized

    def _word_count(self, text: str) -> int:
        return len(re.findall(r"\S+", text))

    def _rebalance_shot_density(self, shots: List[Shot]) -> List[Shot]:
        if not shots:
            return shots
        balanced: List[Shot] = []
        i = 0
        while i < len(shots):
            current = shots[i]
            if self._word_count(current.content) >= self.MIN_WORDS_PER_SHOT:
                balanced.append(current)
                i += 1
                continue
            if i + 1 < len(shots):
                nxt = shots[i + 1]
                merged = Shot(
                    id=0,
                    duration=15.0,
                    content=(current.content + " " + nxt.content).strip(),
                    scene_heading=current.scene_heading or nxt.scene_heading,
                    entities=sorted(list(set(current.entities + nxt.entities))),
                    locations=sorted(list(set(current.locations + nxt.locations))),
                )
                balanced.append(merged)
                i += 2
                continue
            if balanced:
                prev = balanced.pop()
                merged_last = Shot(
                    id=0,
                    duration=15.0,
                    content=(prev.content + " " + current.content).strip(),
                    scene_heading=prev.scene_heading or current.scene_heading,
                    entities=sorted(list(set(prev.entities + current.entities))),
                    locations=sorted(list(set(prev.locations + current.locations))),
                )
                balanced.append(merged_last)
            else:
                balanced.append(current)
            i += 1
        for idx, shot in enumerate(balanced, start=1):
            shot.id = idx
            shot.duration = 15.0
        return balanced

    def _generate_storyboard_with_llm(self, chapter_name: str, source_text: str) -> str:
        canonical_names = sorted(self.canonical_to_type.keys())
        user_prompt = (
            f"CHAPTER: {chapter_name}\n\n"
            f"CANONICAL_NAMES:\n{', '.join(canonical_names)}\n\n"
            "SOURCE_TEXT:\n"
            f"{source_text}\n\n"
            "Generate storyboard shots using the required markdown shot format."
        )
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        response = call_ai_chat_completion(
            messages,
            config=self.api_config,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return extract_first_message_content(response)

    def _format_shots_as_txt(self, shots: List[Shot]) -> str:
        blocks: List[str] = []
        for shot in shots:
            entities = ", ".join(shot.entities)
            locations = ", ".join(shot.locations)
            block = (
                f"### SHOT {shot.id}\n"
                f"DURATION: {shot.duration:.1f}\n"
                f"SCENE_HEADING: {shot.scene_heading}\n"
                f"CONTENT:\n{shot.content}\n"
                f"ENTITIES: {entities}\n"
                f"LOCATIONS: {locations}"
            )
            blocks.append(block)
        return "\n\n".join(blocks).strip() + "\n"

    def _process_chapter(self, task: StoryboardTask) -> StoryboardTask:
        start_time = time.time()
        if self.dry_run:
            logger.info(f"[DRY RUN] Would process: {task.chapter_dir.name}")
            task.status = "SKIPPED"
            return task
        if task.output_path.exists() and not self.force:
            task.status = "SKIPPED"
            return task
        try:
            logger.info(f"Processing chapter: {task.chapter_dir.name} | input={task.input_text_path}")
            with open(task.input_text_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            clean_text = clean_text_for_storyboard(raw_text)
            logger.info(f"Calling LLM for chapter: {task.chapter_dir.name}")
            llm_output = self._generate_storyboard_with_llm(task.chapter_dir.name, clean_text)
            try:
                raw_shots = self._parse_markdown_shots(llm_output)
                shots = self._normalize_shots(raw_shots)
                shots = self._rebalance_shot_density(shots)
                output_text = self._format_shots_as_txt(shots)
            except ValueError as parse_error:
                logger.warning(
                    f"Parse failed for {task.chapter_dir.name}, fallback to raw LLM output: {parse_error}"
                )
                output_text = llm_output.strip() + "\n"
            with open(task.output_path, "w", encoding="utf-8") as f:
                f.write(output_text)
            task.status = "COMPLETED"
            logger.info(f"Generated storyboard: {task.chapter_dir.name}")
        except Exception as e:
            task.status = "FAILED"
            task.error = str(e)
            logger.error(f"Failed to process {task.chapter_dir.name}: {e}")
        task.duration = time.time() - start_time
        return task

    def run(self, input_dir: Path):
        tasks: List[StoryboardTask] = []
        if not input_dir.exists():
            logger.error(f"Input directory does not exist: {input_dir}")
            return
        adapted_dir = input_dir.parent
        self._load_storyboard_metadata(adapted_dir)
        for item in input_dir.iterdir():
            if item.is_dir():
                input_text = item / self.source_filename
                if input_text.exists():
                    tasks.append(
                        StoryboardTask(
                            chapter_dir=item,
                            input_text_path=input_text,
                            output_path=item / "storyboard.txt",
                        )
                    )
        logger.info(f"Found {len(tasks)} chapters to process.")
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_task = {executor.submit(self._process_chapter, task): task for task in tasks}
            completed_count = 0
            failed_count = 0
            skipped_count = 0
            for future in as_completed(future_to_task):
                task = future.result()
                if task.status == "COMPLETED":
                    completed_count += 1
                elif task.status == "FAILED":
                    failed_count += 1
                elif task.status == "SKIPPED":
                    skipped_count += 1
        logger.info(
            f"Storyboard generation finished. {completed_count} completed, {failed_count} failed, {skipped_count} skipped."
        )


def main():
    parser = argparse.ArgumentParser(description="Generate Storyboards from adapted text with LLM.")
    parser.add_argument(
        "--input-dir",
        type=str,
        default="/Users/hjr/Desktop/script-fusion/output/1fc071a6_- BITE ME , ᶻᵒᵐᵇⁱᵉˢ⁴ - $ - Wattpad/adapted/rewrite",
        help="Input directory containing chapter subdirectories",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of concurrent workers (default: 4)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite existing output files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without calling LLM",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.2,
        help="LLM temperature (default: 0.2)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=4000,
        help="LLM max tokens (default: 4000)",
    )
    parser.add_argument(
        "--source-filename",
        type=str,
        default="script_llm.txt",
        help="Source filename inside each chapter directory (default: script_llm.txt)",
    )
    args = parser.parse_args()
    input_path = Path(args.input_dir)
    try:
        converter = StoryboardConverter(
            max_workers=args.workers,
            force=args.force,
            dry_run=args.dry_run,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            source_filename=args.source_filename,
        )
        converter.run(input_path)
    except Exception as e:
        logger.critical(f"Application failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
