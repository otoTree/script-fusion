import json
import re
import time

from util.llm import AIAPIError

from llm_workflow import llm_build_name_map, llm_extract_chapter, llm_merge_entities, llm_rewrite_chapter


TOKEN_PATTERN = re.compile(r"[A-Za-z]+")


def collect_story_dirs(output_dir):
    return sorted([path for path in output_dir.iterdir() if path.is_dir()])


def collect_chapter_files(story_dir):
    return sorted([path for path in story_dir.glob("*.txt") if path.is_file()])


def tokenize(text):
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


def ngram_set(tokens, n=5):
    if len(tokens) < n:
        return set()
    return {tuple(tokens[index : index + n]) for index in range(0, len(tokens) - n + 1)}


def calc_ngram_overlap_ratio(source_text, adapted_text, n=5):
    source = ngram_set(tokenize(source_text), n=n)
    if not source:
        return 0.0
    adapted = ngram_set(tokenize(adapted_text), n=n)
    overlap = source & adapted
    return round(len(overlap) / len(source), 4)


def build_story_context(story_dir):
    metadata_path = story_dir / "metadata.json"
    if not metadata_path.exists():
        return {}
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def wait_for_task_control(control):
    if not control:
        return True
    pause_event = control.get("pause_event")
    cancel_event = control.get("cancel_event")
    while pause_event and pause_event.is_set():
        if cancel_event and cancel_event.is_set():
            return False
        time.sleep(0.2)
    if cancel_event and cancel_event.is_set():
        return False
    return True


def build_task_summary(story_dir, status, chapter_count, target_dir=None, renamed_entity_count=0, chapter_reports=None, reason=None):
    reports = chapter_reports or []
    average_overlap = 0.0
    if reports:
        average_overlap = round(sum(item["ngram5_overlap_ratio"] for item in reports) / len(reports), 4)
    summary = {
        "story_folder": story_dir.name,
        "status": status,
        "chapter_count": chapter_count,
        "renamed_entity_count": renamed_entity_count,
        "target_dir": str(target_dir) if target_dir else "",
        "avg_ngram5_overlap_ratio": average_overlap,
        "llm_mode": "llm_only",
        "llm_error_count": 0,
    }
    if reason:
        summary["reason"] = reason
    return summary


def load_existing_summary(story_dir, target_dir_name):
    target_dir = story_dir / target_dir_name
    report_path = target_dir / "adapt_report.json"
    if not report_path.exists():
        return None
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return None
    if summary.get("status") != "ok":
        return None
    return summary


def process_story_dir(
    story_dir,
    target_dir_name,
    max_renames,
    dry_run,
    analysis_temperature,
    rewrite_temperature,
    analysis_max_tokens,
    rewrite_max_tokens,
    force_rerun=False,
    control=None,
    progress_callback=None,
):
    chapter_files = collect_chapter_files(story_dir)
    if not chapter_files:
        return {
            "story_folder": story_dir.name,
            "status": "skipped",
            "reason": "no_chapter_files",
            "chapter_count": 0,
        }

    existing_summary = None
    if not force_rerun:
        existing_summary = load_existing_summary(story_dir=story_dir, target_dir_name=target_dir_name)
    if existing_summary:
        if progress_callback:
            progress_callback(
                {
                    "total_chapters": len(chapter_files),
                    "completed_chapters": len(chapter_files),
                    "current_chapter": "",
                }
            )
        return {
            "story_folder": story_dir.name,
            "status": "skipped",
            "chapter_count": int(existing_summary.get("chapter_count") or len(chapter_files)),
            "renamed_entity_count": int(existing_summary.get("renamed_entity_count") or 0),
            "target_dir": str(story_dir / target_dir_name),
            "avg_ngram5_overlap_ratio": float(existing_summary.get("avg_ngram5_overlap_ratio") or 0.0),
            "llm_mode": str(existing_summary.get("llm_mode") or "llm_only"),
            "llm_error_count": int(existing_summary.get("llm_error_count") or 0),
            "reason": "already_completed",
        }

    if progress_callback:
        progress_callback({"total_chapters": len(chapter_files), "completed_chapters": 0, "current_chapter": ""})

    story_meta = build_story_context(story_dir=story_dir)
    chapter_records = []
    all_chapter_entities = []

    for chapter_file in chapter_files:
        if not wait_for_task_control(control):
            return build_task_summary(
                story_dir=story_dir,
                status="stopped",
                chapter_count=len(chapter_files),
                reason="task_cancelled_before_extract",
            )
        source_text = chapter_file.read_text(encoding="utf-8", errors="ignore")
        chapter_title = chapter_file.stem
        llm_result = llm_extract_chapter(
            chapter_title=chapter_title,
            chapter_text=source_text,
            story_meta=story_meta,
            temperature=analysis_temperature,
            max_tokens=analysis_max_tokens,
        )
        entities = llm_result.get("entities", [])
        outline = llm_result.get("outline", {})
        if not entities:
            raise AIAPIError(f"章节未提取到实体: {chapter_file.name}")
        if not any(outline.values()):
            raise AIAPIError(f"章节骨架为空: {chapter_file.name}")

        chapter_records.append({"chapter_file": chapter_file, "chapter_title": chapter_title, "outline": outline})
        all_chapter_entities.extend(entities)

    merged_entities = llm_merge_entities(
        chapter_entities=all_chapter_entities,
        temperature=analysis_temperature,
        max_tokens=analysis_max_tokens,
    )
    name_map = llm_build_name_map(
        entities=merged_entities,
        max_renames=max_renames,
        temperature=analysis_temperature,
        max_tokens=analysis_max_tokens,
    )

    target_dir = story_dir / target_dir_name
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    chapter_outlines = []
    chapter_reports = []

    for index, record in enumerate(chapter_records, start=1):
        if not wait_for_task_control(control):
            return build_task_summary(
                story_dir=story_dir,
                status="stopped",
                chapter_count=len(chapter_files),
                target_dir=target_dir,
                renamed_entity_count=len(name_map),
                chapter_reports=chapter_reports,
                reason="task_cancelled_before_rewrite",
            )
        chapter_file = record["chapter_file"]
        source_text = chapter_file.read_text(encoding="utf-8", errors="ignore")
        chapter_title = record["chapter_title"]
        outline = record["outline"]
        if progress_callback:
            progress_callback(
                {
                    "total_chapters": len(chapter_files),
                    "completed_chapters": index - 1,
                    "current_chapter": chapter_file.name,
                }
            )
        adapted_text = llm_rewrite_chapter(
            chapter_title=chapter_title,
            source_text=source_text,
            outline=outline,
            name_map=name_map,
            story_meta=story_meta,
            temperature=rewrite_temperature,
            max_tokens=rewrite_max_tokens,
        )
        overlap_ratio = calc_ngram_overlap_ratio(source_text, adapted_text, n=5)
        chapter_outlines.append({"chapter_file": chapter_file.name, "outline": outline})
        chapter_reports.append(
            {
                "chapter_file": chapter_file.name,
                "source_char_count": len(source_text),
                "adapted_char_count": len(adapted_text),
                "ngram5_overlap_ratio": overlap_ratio,
            }
        )
        if not dry_run:
            (target_dir / chapter_file.name).write_text(adapted_text, encoding="utf-8")
        if progress_callback:
            progress_callback(
                {
                    "total_chapters": len(chapter_files),
                    "completed_chapters": index,
                    "current_chapter": chapter_file.name,
                }
            )

    summary = build_task_summary(
        story_dir=story_dir,
        status="ok",
        chapter_count=len(chapter_files),
        target_dir=target_dir,
        renamed_entity_count=len(name_map),
        chapter_reports=chapter_reports,
    )

    if not dry_run:
        (target_dir / "name_map.json").write_text(json.dumps(name_map, ensure_ascii=False, indent=2), encoding="utf-8")
        (target_dir / "chapter_outline.json").write_text(
            json.dumps(chapter_outlines, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target_dir / "entity_merge.json").write_text(
            json.dumps({"entities": merged_entities}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target_dir / "adapt_report.json").write_text(
            json.dumps(
                {
                    "summary": summary,
                    "chapters": chapter_reports,
                    "llm_errors": [],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return summary
