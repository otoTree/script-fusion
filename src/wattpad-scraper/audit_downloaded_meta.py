import argparse
import json
from pathlib import Path


def is_missing(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def collect_story_dirs(output_dir):
    return sorted([path for path in output_dir.iterdir() if path.is_dir()])


def build_story_meta_audit(story_dir, metadata, chapter_txt_count):
    required_story_fields = ["title", "description", "url", "image", "type", "chapters"]
    required_chapter_fields = ["title", "url"]
    missing_story_fields = [field for field in required_story_fields if is_missing(metadata.get(field))]

    chapters = metadata.get("chapters")
    if not isinstance(chapters, list):
        chapters = []

    chapter_missing_field_counts = {field: 0 for field in required_chapter_fields}
    chapter_issues = []
    for index, chapter in enumerate(chapters, start=1):
        current = chapter if isinstance(chapter, dict) else {}
        missing_fields = [field for field in required_chapter_fields if is_missing(current.get(field))]
        for field in missing_fields:
            chapter_missing_field_counts[field] += 1
        if missing_fields:
            chapter_issues.append(
                {
                    "index": index,
                    "chapter_id": current.get("id"),
                    "chapter_title": current.get("title"),
                    "missing_fields": missing_fields,
                }
            )

    incomplete_reasons = []
    if missing_story_fields:
        incomplete_reasons.append("story_fields_missing")
    if not chapters:
        incomplete_reasons.append("chapters_empty")
    if chapter_issues:
        incomplete_reasons.append("chapter_fields_missing")
    if chapter_txt_count != len(chapters):
        incomplete_reasons.append("chapter_file_count_mismatch")

    present_story_required = len(required_story_fields) - len(missing_story_fields)
    completeness_score = round((present_story_required / len(required_story_fields)) * 100, 2)

    return {
        "story_folder": story_dir.name,
        "status": "ok",
        "story_title": metadata.get("title"),
        "metadata_url": metadata.get("url"),
        "missing_story_fields": missing_story_fields,
        "chapter_count": len(chapters),
        "chapter_txt_count": chapter_txt_count,
        "chapters_with_missing_fields": len(chapter_issues),
        "chapter_missing_field_counts": chapter_missing_field_counts,
        "chapter_issues": chapter_issues,
        "is_incomplete": bool(incomplete_reasons),
        "incomplete_reasons": incomplete_reasons,
        "completeness_score": completeness_score,
    }


def summarize_results(results):
    summary = {
        "total_stories": len(results),
        "success_stories": 0,
        "failed_stories": 0,
        "incomplete_stories": 0,
        "total_chapters": 0,
        "total_chapter_txt_files": 0,
        "stories_missing_field_counts": {},
        "chapter_missing_field_counts": {},
        "incomplete_reason_counts": {},
    }

    for result in results:
        if result.get("status") == "failed":
            summary["failed_stories"] += 1
            continue
        summary["success_stories"] += 1
        summary["total_chapters"] += result.get("chapter_count", 0)
        summary["total_chapter_txt_files"] += result.get("chapter_txt_count", 0)
        if result.get("is_incomplete"):
            summary["incomplete_stories"] += 1

        for field in result.get("missing_story_fields", []):
            summary["stories_missing_field_counts"][field] = summary["stories_missing_field_counts"].get(field, 0) + 1
        for field, count in result.get("chapter_missing_field_counts", {}).items():
            summary["chapter_missing_field_counts"][field] = summary["chapter_missing_field_counts"].get(field, 0) + count
        for reason in result.get("incomplete_reasons", []):
            summary["incomplete_reason_counts"][reason] = summary["incomplete_reason_counts"].get(reason, 0) + 1

    return summary


def run_audit(output_dir, report_file=None):
    story_dirs = collect_story_dirs(output_dir)
    results = []

    for story_dir in story_dirs:
        metadata_path = story_dir / "metadata.json"
        chapter_txt_count = len(list(story_dir.glob("*.txt")))
        if not metadata_path.exists():
            results.append(
                {
                    "story_folder": story_dir.name,
                    "status": "failed",
                    "error": "缺少 metadata.json",
                    "chapter_txt_count": chapter_txt_count,
                }
            )
            continue

        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception as exc:
            results.append(
                {
                    "story_folder": story_dir.name,
                    "status": "failed",
                    "error": f"metadata.json 无法解析: {exc}",
                    "chapter_txt_count": chapter_txt_count,
                }
            )
            continue

        results.append(build_story_meta_audit(story_dir, metadata, chapter_txt_count))

    summary = summarize_results(results)
    report = {"summary": summary, "stories": results}

    print("Meta 校对汇总:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n不完整/失败故事:")
    focus = [item for item in results if item.get("status") == "failed" or item.get("is_incomplete")]
    print(json.dumps(focus, ensure_ascii=False, indent=2))

    if report_file:
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入: {report_file}")


def main():
    parser = argparse.ArgumentParser(description="校对已下载 Wattpad 输出目录中的 metadata 完整性")
    parser.add_argument(
        "--output-dir",
        default="/Users/hjr/Desktop/script-fusion/src/wattpad-scraper/output",
        help="已下载故事目录（其下每个子目录包含 metadata.json）",
    )
    parser.add_argument("--report-file", help="输出校对报告 JSON 路径")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    if not output_dir.exists() or not output_dir.is_dir():
        raise SystemExit(f"输出目录不存在或不是目录: {output_dir}")

    report_file = Path(args.report_file).expanduser().resolve() if args.report_file else None
    run_audit(output_dir=output_dir, report_file=report_file)


if __name__ == "__main__":
    main()
