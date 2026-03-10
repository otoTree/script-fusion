import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_SRC = Path(__file__).resolve().parents[1]
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from story_processing import collect_story_dirs
from task_manager import AdaptTaskManager, TaskStatus


DEFAULT_OUTPUT_DIR = "/Users/hjr/Desktop/script-fusion/src/wattpad-scraper/output"


def run_batch(args, output_dir):
    story_dirs = collect_story_dirs(output_dir)
    if args.story_folder:
        chosen = set(args.story_folder)
        story_dirs = [story_dir for story_dir in story_dirs if story_dir.name in chosen]
    manager = AdaptTaskManager(
        max_workers=args.workers,
        target_dir_name=args.target_dir_name,
        max_renames=args.max_renames,
        dry_run=args.dry_run,
        force_rerun=args.force,
        analysis_temperature=args.analysis_temperature,
        rewrite_temperature=args.rewrite_temperature,
        analysis_max_tokens=args.analysis_max_tokens,
        rewrite_max_tokens=args.rewrite_max_tokens,
    )
    task_ids = [manager.publish(story_dir=story_dir) for story_dir in story_dirs]
    try:
        while True:
            tasks = manager.list_tasks()
            active = [
                item
                for item in tasks
                if item["status"]
                in {
                    TaskStatus.PUBLISHED.value,
                    TaskStatus.RUNNING.value,
                    TaskStatus.PAUSED.value,
                    TaskStatus.STOPPING.value,
                }
            ]
            if not active:
                break
            time.sleep(max(0.2, args.poll_interval))
        task_map = {item["task_id"]: item for item in manager.list_tasks()}
    finally:
        manager.shutdown(cancel_running=False)

    results = []
    for task_id in task_ids:
        task = task_map.get(task_id) or {}
        result = task.get("result")
        if isinstance(result, dict) and result:
            results.append(result)
            continue
        task_status = task.get("status", TaskStatus.FAILED.value).lower()
        fallback = {
            "story_folder": task.get("story_folder"),
            "status": task_status,
            "chapter_count": task.get("total_chapters", 0),
            "reason": task.get("error", ""),
        }
        results.append(fallback)

    report = {
        "summary": {
            "total_stories": len(results),
            "ok_stories": len([item for item in results if item.get("status") == "ok"]),
            "skipped_stories": len([item for item in results if item.get("status") == "skipped"]),
            "stopped_stories": len([item for item in results if item.get("status") == "stopped"]),
            "failed_stories": len([item for item in results if item.get("status") == "failed"]),
            "destroyed_stories": len([item for item in results if item.get("status") == "destroyed"]),
        },
        "stories": results,
    }

    if not args.dry_run:
        report_path = output_dir / "adapt_batch_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入批处理报告: {report_path}")

    print(json.dumps(report, ensure_ascii=False, indent=2))


def run_console(args, output_dir):
    manager = AdaptTaskManager(
        max_workers=args.workers,
        target_dir_name=args.target_dir_name,
        max_renames=args.max_renames,
        dry_run=args.dry_run,
        force_rerun=args.force,
        analysis_temperature=args.analysis_temperature,
        rewrite_temperature=args.rewrite_temperature,
        analysis_max_tokens=args.analysis_max_tokens,
        rewrite_max_tokens=args.rewrite_max_tokens,
    )
    try:
        for story_dir in collect_story_dirs(output_dir):
            if args.story_folder and story_dir.name not in set(args.story_folder):
                continue
            manager.publish(story_dir=story_dir)
        print("输入命令: publish <story_folder> | pause <task_id> | resume <task_id> | stop <task_id> | destroy <task_id> | restart <task_id> | list | wait | exit")
        while True:
            raw = input("> ").strip()
            if not raw:
                continue
            parts = raw.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""
            if cmd == "publish" and arg:
                story_dir = output_dir / arg
                try:
                    task_id = manager.publish(story_dir=story_dir)
                    print(f"已发布: {task_id}")
                except Exception as exc:
                    print(f"发布失败: {exc}")
            elif cmd == "pause" and arg:
                print("成功" if manager.pause(arg) else "失败")
            elif cmd == "resume" and arg:
                print("成功" if manager.resume(arg) else "失败")
            elif cmd == "stop" and arg:
                print("成功" if manager.stop(arg) else "失败")
            elif cmd == "destroy" and arg:
                print("成功" if manager.destroy(arg) else "失败")
            elif cmd == "restart" and arg:
                print("成功" if manager.restart(arg) else "失败")
            elif cmd == "list":
                print(json.dumps(manager.list_tasks(), ensure_ascii=False, indent=2))
            elif cmd == "wait":
                manager.wait_all()
                print("全部任务已结束")
            elif cmd == "exit":
                break
            else:
                print("无效命令")
    finally:
        manager.shutdown(cancel_running=True)


def main():
    parser = argparse.ArgumentParser(description="对已下载章节执行原创改编流程（LLM 提取 + 合并 + 改写 + 报告）")
    parser.add_argument(
        "--mode",
        choices=["batch", "console"],
        default="batch",
        help="batch 自动并发执行；console 支持生命周期控制",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="抓取输出目录（其下每个子目录是一部作品）",
    )
    parser.add_argument(
        "--target-dir-name",
        default="adapted",
        help="每部作品内用于放置改编结果的目录名",
    )
    parser.add_argument(
        "--story-folder",
        action="append",
        help="只处理指定作品目录名，可重复传入",
    )
    parser.add_argument(
        "--max-renames",
        type=int,
        default=30,
        help="每部作品最多重命名实体数量",
    )
    parser.add_argument(
        "--analysis-temperature",
        type=float,
        default=0.3,
        help="LLM 提取与合并阶段温度",
    )
    parser.add_argument(
        "--rewrite-temperature",
        type=float,
        default=0.7,
        help="LLM 改写阶段温度",
    )
    parser.add_argument(
        "--analysis-max-tokens",
        type=int,
        default=1800,
        help="LLM 提取与合并阶段最大 tokens",
    )
    parser.add_argument(
        "--rewrite-max-tokens",
        type=int,
        default=3200,
        help="LLM 改写阶段最大 tokens",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印结果，不写入文件",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略已完成结果并强制重跑",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="并发 worker 数量",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="批量模式状态轮询间隔秒",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()
    if not output_dir.exists() or not output_dir.is_dir():
        raise SystemExit(f"输出目录不存在或不是目录: {output_dir}")

    if args.mode == "batch":
        run_batch(args=args, output_dir=output_dir)
    else:
        run_console(args=args, output_dir=output_dir)


if __name__ == "__main__":
    main()
