import json
import re
import time

from util.llm import AIAPIError

from llm_workflow import llm_build_name_map, llm_extract_chapter, llm_merge_entities, llm_rewrite_chapter


TOKEN_PATTERN = re.compile(r"[A-Za-z]+")
PAID_OR_LOCKED_PATTERNS = [
    r"\bpaid chapter\b",
    r"\bpaid story\b",
    r"\bunlock (this|the) chapter\b",
    r"\bsubscribe to read\b",
    r"\bwattpad premium\b",
    r"\bcoins?\b",
    r"付费章节",
    r"本章.*付费",
    r"解锁本章",
    r"购买后可读",
    r"会员可见",
    r"仅限会员",
]


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


def is_paid_or_locked_chapter(chapter_title, source_text):
    title = (chapter_title or "").lower()
    sample_text = (source_text or "")[:2500].lower()
    return any(re.search(pattern, title) or re.search(pattern, sample_text) for pattern in PAID_OR_LOCKED_PATTERNS)


def local_merge_entities(chapter_entities):
    merged = {}
    for item in chapter_entities:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        entity_type = str(item.get("type", "other")).strip() or "other"
        key = (name.lower(), entity_type)
        mentions = item.get("mentions", 1)
        try:
            mentions = int(mentions)
        except Exception:
            mentions = 1
        aliases = [str(alias).strip() for alias in item.get("aliases", []) if str(alias).strip()]
        current = merged.get(key)
        if current is None:
            merged[key] = {
                "canonical_name": name,
                "type": entity_type,
                "aliases": aliases,
                "mentions": max(1, mentions),
            }
            continue
        current["mentions"] += max(1, mentions)
        alias_pool = set(current["aliases"])
        alias_pool.update(aliases)
        current["aliases"] = sorted(alias_pool)
    values = list(merged.values())
    values.sort(key=lambda item: (-item["mentions"], item["canonical_name"]))
    return values


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


def persist_artifacts(
    target_dir,
    dry_run,
    summary,
    chapter_reports,
    llm_errors,
    name_map,
    merged_entities,
    chapter_outlines,
):
    if dry_run:
        return
    target_dir.mkdir(parents=True, exist_ok=True)
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
                "llm_errors": llm_errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _to_safe_dir_name(chapter_file_name):
    stem = chapter_file_name.rsplit(".", 1)[0]
    safe = re.sub(r"[^\w\-]+", "_", stem, flags=re.UNICODE).strip("_")
    return safe or "chapter"


def persist_extract_artifact(
    target_dir,
    dry_run,
    chapter_file_name,
    chapter_title,
    source_text,
    extract_payload=None,
    error_text=None,
):
    if dry_run:
        return
    chapter_dir = target_dir / "extract" / _to_safe_dir_name(chapter_file_name)
    chapter_dir.mkdir(parents=True, exist_ok=True)
    (chapter_dir / "source.txt").write_text(source_text, encoding="utf-8")
    (chapter_dir / "meta.json").write_text(
        json.dumps(
            {
                "chapter_file": chapter_file_name,
                "chapter_title": chapter_title,
                "status": "error" if error_text else "ok",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if extract_payload is not None:
        (chapter_dir / "extract.json").write_text(
            json.dumps(extract_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if error_text:
        (chapter_dir / "error.json").write_text(
            json.dumps({"error": error_text}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def persist_merge_artifact(target_dir, dry_run, merged_entities, name_map, llm_errors):
    if dry_run:
        return
    merge_dir = target_dir / "extract" / "_merged"
    merge_dir.mkdir(parents=True, exist_ok=True)
    (merge_dir / "merged_entities.json").write_text(
        json.dumps({"entities": merged_entities}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (merge_dir / "name_map.json").write_text(
        json.dumps(name_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    merge_errors = [item for item in llm_errors if item.get("stage") in {"merge_entities", "build_name_map"}]
    (merge_dir / "errors.json").write_text(
        json.dumps(merge_errors, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    merge_status = "merged" if merged_entities else "skipped"
    merge_reason = "" if merged_entities else "no_extract_entities"
    (merge_dir / "summary.json").write_text(
        json.dumps(
            {
                "status": merge_status,
                "merged_entity_count": len(merged_entities),
                "name_map_count": len(name_map),
                "reason": merge_reason,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def persist_rewrite_artifact(
    target_dir,
    dry_run,
    chapter_file_name,
    chapter_title,
    outline,
    name_map,
    source_text,
    adapted_text=None,
    overlap_ratio=None,
    error_text=None,
):
    if dry_run:
        return
    chapter_dir = target_dir / "rewrite" / _to_safe_dir_name(chapter_file_name)
    chapter_dir.mkdir(parents=True, exist_ok=True)
    (chapter_dir / "input.json").write_text(
        json.dumps(
            {
                "chapter_file": chapter_file_name,
                "chapter_title": chapter_title,
                "outline": outline,
                "name_map": name_map,
                "source_char_count": len(source_text),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if adapted_text is not None:
        (chapter_dir / "adapted.txt").write_text(adapted_text, encoding="utf-8")
        (chapter_dir / "report.json").write_text(
            json.dumps(
                {
                    "chapter_file": chapter_file_name,
                    "status": "ok",
                    "source_char_count": len(source_text),
                    "adapted_char_count": len(adapted_text),
                    "ngram5_overlap_ratio": overlap_ratio if overlap_ratio is not None else 0.0,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if error_text:
        (chapter_dir / "error.json").write_text(
            json.dumps({"chapter_file": chapter_file_name, "error": error_text}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def persist_running_checkpoint(
    story_dir,
    chapter_files,
    target_dir,
    dry_run,
    name_map,
    chapter_reports,
    llm_errors,
    merged_entities,
    chapter_outlines,
    reason,
):
    summary = build_task_summary(
        story_dir=story_dir,
        status="running",
        chapter_count=len(chapter_files),
        target_dir=target_dir,
        renamed_entity_count=len(name_map),
        chapter_reports=chapter_reports,
        reason=reason,
    )
    summary["llm_error_count"] = len(llm_errors)
    persist_artifacts(
        target_dir=target_dir,
        dry_run=dry_run,
        summary=summary,
        chapter_reports=chapter_reports,
        llm_errors=llm_errors,
        name_map=name_map,
        merged_entities=merged_entities,
        chapter_outlines=chapter_outlines,
    )


def persist_runtime_state(target_dir, dry_run, stage, current_chapter, detail):
    if dry_run:
        return
    (target_dir / "runtime_state.json").write_text(
        json.dumps(
            {
                "stage": stage,
                "current_chapter": current_chapter,
                "detail": detail,
                "updated_at": int(time.time()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_extract_cache(chapter_files, target_dir):
    chapter_records = []
    all_chapter_entities = []
    llm_errors = []
    for chapter_file in chapter_files:
        chapter_dir = target_dir / "extract" / _to_safe_dir_name(chapter_file.name)
        extract_path = chapter_dir / "extract.json"
        error_path = chapter_dir / "error.json"
        if not extract_path.exists() and not error_path.exists():
            return None
        if error_path.exists():
            try:
                error_payload = json.loads(error_path.read_text(encoding="utf-8"))
                error_text = str(error_payload.get("error", "")).strip() or "extract_error_from_cache"
            except Exception:
                error_text = "extract_error_from_cache"
            llm_errors.append({"chapter_file": chapter_file.name, "stage": "extract", "error": error_text})
        if not extract_path.exists():
            continue
        try:
            extract_payload = json.loads(extract_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        entities = extract_payload.get("entities", [])
        outline = extract_payload.get("outline", {})
        if not isinstance(entities, list):
            entities = []
        if not isinstance(outline, dict):
            outline = {}
        if extract_payload.get("skip_reason"):
            llm_errors.append(
                {
                    "chapter_file": chapter_file.name,
                    "stage": "extract",
                    "error": str(extract_payload.get("skip_reason")),
                }
            )
            continue
        if not any(outline.values()):
            llm_errors.append({"chapter_file": chapter_file.name, "stage": "extract", "error": "empty_outline"})
            continue
        chapter_records.append(
            {
                "chapter_file": chapter_file,
                "chapter_title": chapter_file.stem,
                "outline": {
                    "opening": str(outline.get("opening", "")).strip(),
                    "conflict": str(outline.get("conflict", "")).strip(),
                    "turning_point": str(outline.get("turning_point", "")).strip(),
                    "ending": str(outline.get("ending", "")).strip(),
                },
            }
        )
        all_chapter_entities.extend(
            [
                {
                    "name": str(item.get("name", "")).strip(),
                    "type": str(item.get("type", "other")).strip() or "other",
                    "aliases": [str(alias).strip() for alias in item.get("aliases", []) if str(alias).strip()],
                    "mentions": item.get("mentions", 1),
                    "scene": chapter_file.stem,
                    "chapter_file": chapter_file.name,
                }
                for item in entities
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            ]
        )
    return {
        "chapter_records": chapter_records,
        "all_chapter_entities": all_chapter_entities,
        "llm_errors": llm_errors,
    }


def load_merged_cache(target_dir):
    merge_dir = target_dir / "extract" / "_merged"
    summary_path = merge_dir / "summary.json"
    entities_path = merge_dir / "merged_entities.json"
    name_map_path = merge_dir / "name_map.json"
    if not summary_path.exists() or not entities_path.exists() or not name_map_path.exists():
        return None
    try:
        summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
        entities_payload = json.loads(entities_path.read_text(encoding="utf-8"))
        name_map_payload = json.loads(name_map_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(summary_payload, dict):
        return None
    if summary_payload.get("status") != "merged":
        return None
    merged_entities = entities_payload.get("entities", [])
    if not isinstance(merged_entities, list) or not isinstance(name_map_payload, dict):
        return None
    return {
        "merged_entities": merged_entities,
        "name_map": name_map_payload,
    }


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

    target_dir = story_dir / target_dir_name
    if not dry_run:
        (target_dir / "extract").mkdir(parents=True, exist_ok=True)
        (target_dir / "rewrite").mkdir(parents=True, exist_ok=True)
        (target_dir / "main").mkdir(parents=True, exist_ok=True)
    story_meta = build_story_context(story_dir=story_dir)
    chapter_records = []
    all_chapter_entities = []
    chapter_outlines = []
    chapter_reports = []
    llm_errors = []
    merged_entities = []
    name_map = {}

    reset_request_payload = {
        "request_name": "-",
        "request_phase": "-",
        "request_attempt": 0,
        "request_sleep_seconds": 0.0,
        "request_error": "",
    }

    def emit_llm_request_status(stage_name, chapter_name, completed_chapters, payload):
        request_name = str(payload.get("request_name", "")).strip()
        phase = str(payload.get("phase", "")).strip()
        attempt = payload.get("attempt")
        sleep_seconds = payload.get("sleep_seconds")
        request_error = str(payload.get("error", "")).strip()
        detail_parts = [part for part in [request_name, phase] if part]
        if isinstance(attempt, int):
            detail_parts.append(f"attempt={attempt}")
        if isinstance(sleep_seconds, (int, float)):
            detail_parts.append(f"sleep={sleep_seconds}")
        if request_error:
            detail_parts.append(f"error={request_error[:200]}")
        persist_runtime_state(
            target_dir=target_dir,
            dry_run=dry_run,
            stage=stage_name,
            current_chapter=chapter_name,
            detail=" | ".join(detail_parts) if detail_parts else "llm_request",
        )
        if progress_callback:
            progress_callback(
                {
                    "total_chapters": len(chapter_files),
                    "completed_chapters": max(0, int(completed_chapters)),
                    "current_chapter": chapter_name,
                    "request_name": request_name,
                    "request_phase": phase,
                    "request_attempt": attempt if isinstance(attempt, int) else 0,
                    "request_sleep_seconds": float(sleep_seconds) if isinstance(sleep_seconds, (int, float)) else 0.0,
                    "request_error": request_error,
                }
            )

    try:
        cached_extract = None
        if not force_rerun:
            cached_extract = load_extract_cache(chapter_files=chapter_files, target_dir=target_dir)
        if cached_extract is not None:
            persist_runtime_state(
                target_dir=target_dir,
                dry_run=dry_run,
                stage="extract_cached",
                current_chapter="",
                detail="extract_loaded_from_cache",
            )
            chapter_records = cached_extract["chapter_records"]
            all_chapter_entities = cached_extract["all_chapter_entities"]
            llm_errors.extend(cached_extract["llm_errors"])
            persist_running_checkpoint(
                story_dir=story_dir,
                chapter_files=chapter_files,
                target_dir=target_dir,
                dry_run=dry_run,
                name_map=name_map,
                chapter_reports=chapter_reports,
                llm_errors=llm_errors,
                merged_entities=merged_entities,
                chapter_outlines=chapter_outlines,
                reason="extract_loaded_from_cache",
            )
        else:
            persist_runtime_state(
                target_dir=target_dir,
                dry_run=dry_run,
                stage="extracting",
                current_chapter="",
                detail="extract_start",
            )
            for chapter_file in chapter_files:
                if not wait_for_task_control(control):
                    summary = build_task_summary(
                        story_dir=story_dir,
                        status="stopped",
                        chapter_count=len(chapter_files),
                        target_dir=target_dir,
                        reason="task_cancelled_before_extract",
                    )
                    summary["llm_error_count"] = len(llm_errors)
                    persist_artifacts(
                        target_dir=target_dir,
                        dry_run=dry_run,
                        summary=summary,
                        chapter_reports=chapter_reports,
                        llm_errors=llm_errors,
                        name_map=name_map,
                        merged_entities=merged_entities,
                        chapter_outlines=chapter_outlines,
                    )
                    return summary

                source_text = chapter_file.read_text(encoding="utf-8", errors="ignore")
                chapter_title = chapter_file.stem
                persist_runtime_state(
                    target_dir=target_dir,
                    dry_run=dry_run,
                    stage="extracting",
                    current_chapter=chapter_file.name,
                    detail="extract_processing",
                )
                if is_paid_or_locked_chapter(chapter_title=chapter_title, source_text=source_text):
                    skip_reason = "paid_or_locked_chapter_skipped"
                    llm_errors.append({"chapter_file": chapter_file.name, "stage": "extract", "error": skip_reason})
                    persist_extract_artifact(
                        target_dir=target_dir,
                        dry_run=dry_run,
                        chapter_file_name=chapter_file.name,
                        chapter_title=chapter_title,
                        source_text=source_text,
                        extract_payload={"skip_reason": skip_reason},
                        error_text=skip_reason,
                    )
                    persist_running_checkpoint(
                        story_dir=story_dir,
                        chapter_files=chapter_files,
                        target_dir=target_dir,
                        dry_run=dry_run,
                        name_map=name_map,
                        chapter_reports=chapter_reports,
                        llm_errors=llm_errors,
                        merged_entities=merged_entities,
                        chapter_outlines=chapter_outlines,
                        reason=f"extract_skip_paid:{chapter_file.name}",
                    )
                    continue
                try:
                    llm_result = llm_extract_chapter(
                        chapter_title=chapter_title,
                        chapter_text=source_text,
                        story_meta=story_meta,
                        temperature=analysis_temperature,
                        max_tokens=analysis_max_tokens,
                        status_callback=lambda payload: emit_llm_request_status(
                            stage_name="extracting",
                            chapter_name=chapter_file.name,
                            completed_chapters=0,
                            payload=payload,
                        ),
                    )
                except AIAPIError as error:
                    llm_errors.append({"chapter_file": chapter_file.name, "stage": "extract", "error": str(error)})
                    persist_extract_artifact(
                        target_dir=target_dir,
                        dry_run=dry_run,
                        chapter_file_name=chapter_file.name,
                        chapter_title=chapter_title,
                        source_text=source_text,
                        error_text=str(error),
                    )
                    persist_running_checkpoint(
                        story_dir=story_dir,
                        chapter_files=chapter_files,
                        target_dir=target_dir,
                        dry_run=dry_run,
                        name_map=name_map,
                        chapter_reports=chapter_reports,
                        llm_errors=llm_errors,
                        merged_entities=merged_entities,
                        chapter_outlines=chapter_outlines,
                        reason=f"extract_error:{chapter_file.name}",
                    )
                    continue

                entities = llm_result.get("entities", [])
                outline = llm_result.get("outline", {})
                persist_extract_artifact(
                    target_dir=target_dir,
                    dry_run=dry_run,
                    chapter_file_name=chapter_file.name,
                    chapter_title=chapter_title,
                    source_text=source_text,
                    extract_payload={
                        "entities": entities,
                        "outline": outline,
                    },
                )
                if not any(outline.values()):
                    llm_errors.append({"chapter_file": chapter_file.name, "stage": "extract", "error": "empty_outline"})
                    persist_running_checkpoint(
                        story_dir=story_dir,
                        chapter_files=chapter_files,
                        target_dir=target_dir,
                        dry_run=dry_run,
                        name_map=name_map,
                        chapter_reports=chapter_reports,
                        llm_errors=llm_errors,
                        merged_entities=merged_entities,
                        chapter_outlines=chapter_outlines,
                        reason=f"extract_empty_outline:{chapter_file.name}",
                    )
                    continue

                chapter_records.append({"chapter_file": chapter_file, "chapter_title": chapter_title, "outline": outline})
                all_chapter_entities.extend(
                    [
                        {
                            "name": str(item.get("name", "")).strip(),
                            "type": str(item.get("type", "other")).strip() or "other",
                            "aliases": [str(alias).strip() for alias in item.get("aliases", []) if str(alias).strip()],
                            "mentions": item.get("mentions", 1),
                            "scene": chapter_title,
                            "chapter_file": chapter_file.name,
                        }
                        for item in entities
                        if isinstance(item, dict) and str(item.get("name", "")).strip()
                    ]
                )
                persist_running_checkpoint(
                    story_dir=story_dir,
                    chapter_files=chapter_files,
                    target_dir=target_dir,
                    dry_run=dry_run,
                    name_map=name_map,
                    chapter_reports=chapter_reports,
                    llm_errors=llm_errors,
                    merged_entities=merged_entities,
                    chapter_outlines=chapter_outlines,
                    reason=f"extract_ok:{chapter_file.name}",
                )

        cached_merged = None
        if not force_rerun:
            cached_merged = load_merged_cache(target_dir=target_dir)
        if cached_merged is not None:
            merged_entities = cached_merged["merged_entities"]
            name_map = cached_merged["name_map"]
            persist_runtime_state(
                target_dir=target_dir,
                dry_run=dry_run,
                stage="merge_cached",
                current_chapter="",
                detail=f"merged:{len(merged_entities)} name_map:{len(name_map)}",
            )
            persist_running_checkpoint(
                story_dir=story_dir,
                chapter_files=chapter_files,
                target_dir=target_dir,
                dry_run=dry_run,
                name_map=name_map,
                chapter_reports=chapter_reports,
                llm_errors=llm_errors,
                merged_entities=merged_entities,
                chapter_outlines=chapter_outlines,
                reason="merge_loaded_from_cache",
            )
        else:
            persist_runtime_state(
                target_dir=target_dir,
                dry_run=dry_run,
                stage="merging",
                current_chapter="",
                detail="merge_start",
            )
            if progress_callback:
                progress_callback(
                    {
                        "total_chapters": len(chapter_files),
                        "completed_chapters": len(chapter_files),
                        "current_chapter": "__MERGE_ENTITIES__",
                    }
                )
            if all_chapter_entities:
                try:
                    merged_entities = llm_merge_entities(
                        chapter_entities=all_chapter_entities,
                        temperature=analysis_temperature,
                        max_tokens=analysis_max_tokens,
                        status_callback=lambda payload: emit_llm_request_status(
                            stage_name="merging",
                            chapter_name="__MERGE_ENTITIES__",
                            completed_chapters=len(chapter_files),
                            payload=payload,
                        ),
                    )
                except AIAPIError as error:
                    merged_entities = local_merge_entities(all_chapter_entities)
                    llm_errors.append(
                        {
                            "chapter_file": "",
                            "stage": "merge_entities",
                            "error": f"llm_merge_fallback_to_local:{error}",
                        }
                    )
                persist_running_checkpoint(
                    story_dir=story_dir,
                    chapter_files=chapter_files,
                    target_dir=target_dir,
                    dry_run=dry_run,
                    name_map=name_map,
                    chapter_reports=chapter_reports,
                    llm_errors=llm_errors,
                    merged_entities=merged_entities,
                    chapter_outlines=chapter_outlines,
                    reason="merge_entities_done",
                )
                persist_runtime_state(
                    target_dir=target_dir,
                    dry_run=dry_run,
                    stage="merging",
                    current_chapter="",
                    detail=f"merge_entities_done:{len(merged_entities)}",
                )

                if merged_entities:
                    if progress_callback:
                        progress_callback(
                            {
                                "total_chapters": len(chapter_files),
                                "completed_chapters": len(chapter_files),
                                "current_chapter": "__BUILD_NAME_MAP__",
                            }
                        )
                    try:
                        name_map = llm_build_name_map(
                            entities=merged_entities,
                            max_renames=max_renames,
                            temperature=analysis_temperature,
                            max_tokens=analysis_max_tokens,
                            status_callback=lambda payload: emit_llm_request_status(
                                stage_name="merging",
                                chapter_name="__BUILD_NAME_MAP__",
                                completed_chapters=len(chapter_files),
                                payload=payload,
                            ),
                        )
                    except AIAPIError as error:
                        name_map = {}
                        llm_errors.append(
                            {
                                "chapter_file": "",
                                "stage": "build_name_map",
                                "error": f"llm_name_map_failed_keep_empty:{error}",
                            }
                        )
                    persist_running_checkpoint(
                        story_dir=story_dir,
                        chapter_files=chapter_files,
                        target_dir=target_dir,
                        dry_run=dry_run,
                        name_map=name_map,
                        chapter_reports=chapter_reports,
                        llm_errors=llm_errors,
                        merged_entities=merged_entities,
                        chapter_outlines=chapter_outlines,
                        reason="build_name_map_done",
                    )
                    persist_runtime_state(
                        target_dir=target_dir,
                        dry_run=dry_run,
                        stage="merging",
                        current_chapter="",
                        detail=f"build_name_map_done:{len(name_map)}",
                    )
        persist_merge_artifact(
            target_dir=target_dir,
            dry_run=dry_run,
            merged_entities=merged_entities,
            name_map=name_map,
            llm_errors=llm_errors,
        )
        persist_runtime_state(
            target_dir=target_dir,
            dry_run=dry_run,
            stage="rewrite_ready",
            current_chapter="",
            detail=f"records:{len(chapter_records)}",
        )

        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)

        if progress_callback:
            progress_callback(
                {
                    "total_chapters": len(chapter_files),
                    "completed_chapters": len(chapter_files),
                    "current_chapter": "__REWRITE_START__",
                    **reset_request_payload,
                }
            )
        for index, record in enumerate(chapter_records, start=1):
            if not wait_for_task_control(control):
                summary = build_task_summary(
                    story_dir=story_dir,
                    status="stopped",
                    chapter_count=len(chapter_files),
                    target_dir=target_dir,
                    renamed_entity_count=len(name_map),
                    chapter_reports=chapter_reports,
                    reason="task_cancelled_before_rewrite",
                )
                summary["llm_error_count"] = len(llm_errors)
                persist_artifacts(
                    target_dir=target_dir,
                    dry_run=dry_run,
                    summary=summary,
                    chapter_reports=chapter_reports,
                    llm_errors=llm_errors,
                    name_map=name_map,
                    merged_entities=merged_entities,
                    chapter_outlines=chapter_outlines,
                )
                return summary

            chapter_file = record["chapter_file"]
            source_text = chapter_file.read_text(encoding="utf-8", errors="ignore")
            chapter_title = record["chapter_title"]
            outline = record["outline"]
            persist_runtime_state(
                target_dir=target_dir,
                dry_run=dry_run,
                stage="rewriting",
                current_chapter=chapter_file.name,
                detail=f"{index}/{len(chapter_records)}",
            )
            if progress_callback:
                progress_callback(
                    {
                        "total_chapters": len(chapter_files),
                        "completed_chapters": index - 1,
                        "current_chapter": chapter_file.name,
                        **reset_request_payload,
                    }
                )
            persist_rewrite_artifact(
                target_dir=target_dir,
                dry_run=dry_run,
                chapter_file_name=chapter_file.name,
                chapter_title=chapter_title,
                outline=outline,
                name_map=name_map,
                source_text=source_text,
            )

            try:
                adapted_text = llm_rewrite_chapter(
                    chapter_title=chapter_title,
                    source_text=source_text,
                    outline=outline,
                    name_map=name_map,
                    story_meta=story_meta,
                    temperature=rewrite_temperature,
                    max_tokens=rewrite_max_tokens,
                    status_callback=lambda payload: emit_llm_request_status(
                        stage_name="rewriting",
                        chapter_name=chapter_file.name,
                        completed_chapters=index - 1,
                        payload=payload,
                    ),
                )
            except AIAPIError as error:
                llm_errors.append({"chapter_file": chapter_file.name, "stage": "rewrite", "error": str(error)})
                persist_rewrite_artifact(
                    target_dir=target_dir,
                    dry_run=dry_run,
                    chapter_file_name=chapter_file.name,
                    chapter_title=chapter_title,
                    outline=outline,
                    name_map=name_map,
                    source_text=source_text,
                    error_text=str(error),
                )
                persist_running_checkpoint(
                    story_dir=story_dir,
                    chapter_files=chapter_files,
                    target_dir=target_dir,
                    dry_run=dry_run,
                    name_map=name_map,
                    chapter_reports=chapter_reports,
                    llm_errors=llm_errors,
                    merged_entities=merged_entities,
                    chapter_outlines=chapter_outlines,
                    reason=f"rewrite_error:{chapter_file.name}",
                )
                if progress_callback:
                    progress_callback(
                        {
                            "total_chapters": len(chapter_files),
                            "completed_chapters": index,
                            "current_chapter": chapter_file.name,
                            **reset_request_payload,
                        }
                    )
                continue

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
                (target_dir / "main" / chapter_file.name).write_text(adapted_text, encoding="utf-8")
            persist_rewrite_artifact(
                target_dir=target_dir,
                dry_run=dry_run,
                chapter_file_name=chapter_file.name,
                chapter_title=chapter_title,
                outline=outline,
                name_map=name_map,
                source_text=source_text,
                adapted_text=adapted_text,
                overlap_ratio=overlap_ratio,
            )
            persist_running_checkpoint(
                story_dir=story_dir,
                chapter_files=chapter_files,
                target_dir=target_dir,
                dry_run=dry_run,
                name_map=name_map,
                chapter_reports=chapter_reports,
                llm_errors=llm_errors,
                merged_entities=merged_entities,
                chapter_outlines=chapter_outlines,
                reason=f"rewrite_ok:{chapter_file.name}",
            )
            if progress_callback:
                progress_callback(
                    {
                        "total_chapters": len(chapter_files),
                        "completed_chapters": index,
                        "current_chapter": chapter_file.name,
                        **reset_request_payload,
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
        summary["llm_error_count"] = len(llm_errors)
        persist_artifacts(
            target_dir=target_dir,
            dry_run=dry_run,
            summary=summary,
            chapter_reports=chapter_reports,
            llm_errors=llm_errors,
            name_map=name_map,
            merged_entities=merged_entities,
            chapter_outlines=chapter_outlines,
        )
        persist_runtime_state(
            target_dir=target_dir,
            dry_run=dry_run,
            stage="completed",
            current_chapter="",
            detail="ok",
        )
        return summary
    except Exception as exc:
        summary = build_task_summary(
            story_dir=story_dir,
            status="failed",
            chapter_count=len(chapter_files),
            target_dir=target_dir,
            renamed_entity_count=len(name_map),
            chapter_reports=chapter_reports,
            reason=str(exc),
        )
        summary["llm_error_count"] = len(llm_errors)
        persist_artifacts(
            target_dir=target_dir,
            dry_run=dry_run,
            summary=summary,
            chapter_reports=chapter_reports,
            llm_errors=llm_errors,
            name_map=name_map,
            merged_entities=merged_entities,
            chapter_outlines=chapter_outlines,
        )
        persist_runtime_state(
            target_dir=target_dir,
            dry_run=dry_run,
            stage="failed",
            current_chapter="",
            detail=str(exc),
        )
        raise
