import argparse
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv

# Ensure src is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.append(str(PROJECT_ROOT / "src"))

from util.llm import AIAPIError, call_ai_chat_completion, extract_first_message_content, load_ai_api_config
from utils import load_name_map, load_names

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Load environment variables from .env file if present
load_dotenv(PROJECT_ROOT / ".env")


@dataclass
class ScriptConversionTask:
    """Represents a single script conversion task."""

    chapter_dir: Path
    input_text_path: Path
    input_json_path: Path
    output_path: Path
    status: str = "PENDING"
    error: Optional[str] = None
    duration: float = 0.0


class ScriptConverter:
    """Handles the conversion of novel text to screenplay format using LLM."""

    SYSTEM_PROMPT = """You are a professional screenwriter adapting a novel into a screenplay in Fountain format.
Your task is to convert the provided novel text into a standard Fountain script.

Follow these rules:
1. Use standard Fountain syntax.
2. Scene Headings: Start with '.' followed by the location/time (e.g., .INT. ROOM - DAY). Infer locations from the text.
3. Action: Describe actions present in the text concisely.
4. Dialogue: Format dialogue correctly with character names in uppercase.
5. Characters: Use the provided character names. If a character name is not in the list but appears in the text, use the name from the text.
6. Do not omit any plot points or dialogue, but adapt them to fit the screenplay format.
7. Output ONLY the Fountain script content. Do not include markdown code blocks (```) or introductory text.
"""

    def __init__(
        self,
        max_workers: int = 4,
        dry_run: bool = False,
        temperature: float = 0.3,
        max_tokens: int = 4000,
        force: bool = False,
    ):
        self.max_workers = max_workers
        self.dry_run = dry_run
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.force = force
        try:
            self.api_config = load_ai_api_config()
        except AIAPIError as e:
            logger.error(f"Failed to load AI API config: {e}")
            raise

    def _canonicalize_script_names(self, script_content: str, reverse_name_map: Dict[str, str]) -> str:
        normalized = script_content
        items = sorted(reverse_name_map.items(), key=lambda item: len(item[0]), reverse=True)
        for target_name, canonical_name in items:
            pattern = re.compile(rf"\b{re.escape(target_name)}\b", re.IGNORECASE)
            def _replace(match: re.Match[str]) -> str:
                word = match.group(0)
                if word.isupper():
                    return canonical_name.upper()
                if word.istitle():
                    return canonical_name
                return canonical_name
            normalized = pattern.sub(_replace, normalized)
        return normalized

    def _convert_chapter(self, task: ScriptConversionTask) -> ScriptConversionTask:
        """Process a single chapter conversion."""
        start_time = time.time()
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Would process: {task.chapter_dir.name}")
            task.status = "SKIPPED"
            return task

        if task.output_path.exists() and not self.force:
            logger.info(f"Skipping existing output: {task.output_path}")
            task.status = "SKIPPED"
            return task

        logger.info(f"Processing: {task.chapter_dir.name}")

        try:
            with open(task.input_text_path, "r", encoding="utf-8") as f:
                content = f.read()

            names = []
            name_map = {}
            if task.input_json_path.exists():
                names = load_names(task.input_json_path)
                name_map = load_name_map(task.input_json_path)
            
            names_str = ", ".join(names) if names else "None provided"
            target_to_canonical = {value: key for key, value in name_map.items() if isinstance(key, str) and isinstance(value, str)}
            map_lines = "\n".join([f"{v} -> {k}" for v, k in target_to_canonical.items()]) if target_to_canonical else "None"

            user_prompt = f"""
Character Names: {names_str}
Canonical Name Enforcement:
Use canonical names only and avoid mapped names:
{map_lines}

Novel Content:
{content}

Convert the above content to a Fountain script.
"""
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
            script_content = extract_first_message_content(response)
            
            # Clean up markdown code blocks if present
            script_content = script_content.replace("```fountain", "").replace("```", "").strip()
            if target_to_canonical:
                script_content = self._canonicalize_script_names(script_content, target_to_canonical)

            with open(task.output_path, "w", encoding="utf-8") as f:
                f.write(script_content)

            task.status = "COMPLETED"
            logger.info(f"Successfully converted: {task.chapter_dir.name}")

        except AIAPIError as e:
            task.status = "FAILED"
            task.error = str(e)
            logger.error(f"AI API Error converting {task.chapter_dir.name}: {e}")
        except Exception as e:
            task.status = "FAILED"
            task.error = str(e)
            logger.error(f"Unexpected error converting {task.chapter_dir.name}: {e}")
        
        task.duration = time.time() - start_time
        return task

    def _should_process_chapter(self, chapter_dir_name: str) -> bool:
        normalized = chapter_dir_name.lower()
        skip_keywords = ["author", "note", "playlist"]
        return not any(keyword in normalized for keyword in skip_keywords)

    def run(self, input_dir: Path):
        """Run the conversion process on all subdirectories in input_dir."""
        tasks = []
        if not input_dir.exists():
            logger.error(f"Input directory does not exist: {input_dir}")
            return

        # Scan for valid chapter directories
        for item in input_dir.iterdir():
            if item.is_dir():
                if not self._should_process_chapter(item.name):
                    logger.info(f"Skipping non-story chapter: {item.name}")
                    continue
                input_text = item / "adapted.txt"
                if input_text.exists():
                    tasks.append(
                        ScriptConversionTask(
                            chapter_dir=item,
                            input_text_path=input_text,
                            input_json_path=item / "input.json",
                            output_path=item / "script_llm.txt",
                        )
                    )

        logger.info(f"Found {len(tasks)} chapters to process.")

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_task = {
                executor.submit(self._convert_chapter, task): task for task in tasks
            }
            
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

        logger.info("Conversion finished.")
        logger.info(f"Summary: {completed_count} completed, {failed_count} failed, {skipped_count} skipped.")


def main():
    parser = argparse.ArgumentParser(description="Convert novel chapters to Fountain scripts using LLM.")
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
        "--dry-run",
        action="store_true",
        help="Perform a dry run without making API calls",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force overwrite existing output files",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.3,
        help="LLM temperature (default: 0.3)",
    )

    args = parser.parse_args()
    
    input_path = Path(args.input_dir)
    
    try:
        converter = ScriptConverter(
            max_workers=args.workers,
            dry_run=args.dry_run,
            temperature=args.temperature,
            force=args.force,
        )
        converter.run(input_path)
    except Exception as e:
        logger.critical(f"Application failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
