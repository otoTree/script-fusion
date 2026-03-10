import argparse
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from get_chapter_content import get_chapter_content
from get_story_meta import get_wattpad_metadata, parse_cookie_string


def is_missing(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def collect_story_urls(args):
    story_urls = []
    if getattr(args, "url", None):
        story_urls.extend(args.url)
    if getattr(args, "urls_file", None):
        lines = Path(args.urls_file).read_text(encoding="utf-8").splitlines()
        story_urls.extend([line.strip() for line in lines if line.strip()])
    unique_urls = []
    for url in story_urls:
        if url not in unique_urls:
            unique_urls.append(url)
    return unique_urls


def build_story_meta_audit(story_url, metadata):
    required_story_fields = ["title", "description", "url", "image", "type", "chapters"]
    required_chapter_fields = ["title", "url"]

    missing_story_fields = [field for field in required_story_fields if is_missing(metadata.get(field))]
    chapters = metadata.get("chapters")
    if not isinstance(chapters, list):
        chapters = []

    chapter_missing_field_counts = {field: 0 for field in required_chapter_fields}
    chapter_details = []
    for index, chapter in enumerate(chapters, start=1):
        current = chapter if isinstance(chapter, dict) else {}
        missing_chapter_fields = [field for field in required_chapter_fields if is_missing(current.get(field))]
        for field in missing_chapter_fields:
            chapter_missing_field_counts[field] += 1
        if missing_chapter_fields:
            chapter_details.append(
                {
                    "index": index,
                    "chapter_id": current.get("id"),
                    "chapter_title": current.get("title"),
                    "missing_fields": missing_chapter_fields,
                }
            )

    incomplete_reasons = []
    if missing_story_fields:
        incomplete_reasons.append("story_fields_missing")
    if not chapters:
        incomplete_reasons.append("chapters_empty")
    if chapter_details:
        incomplete_reasons.append("chapter_fields_missing")

    present_story_required = len(required_story_fields) - len(missing_story_fields)
    completeness = round((present_story_required / len(required_story_fields)) * 100, 2)

    return {
        "story_url": story_url,
        "status": "ok",
        "story_title": metadata.get("title"),
        "missing_story_fields": missing_story_fields,
        "chapter_count": len(chapters),
        "chapters_with_missing_fields": len(chapter_details),
        "chapter_missing_field_counts": chapter_missing_field_counts,
        "chapter_issues": chapter_details,
        "is_incomplete": bool(incomplete_reasons),
        "incomplete_reasons": incomplete_reasons,
        "completeness_score": completeness,
        "required_story_fields": required_story_fields,
        "required_chapter_fields": required_chapter_fields,
    }


def summarize_meta_audit(results):
    summary = {
        "total_stories": len(results),
        "success_stories": 0,
        "failed_stories": 0,
        "incomplete_stories": 0,
        "total_chapters": 0,
        "stories_missing_field_counts": {},
        "chapter_missing_field_counts": {},
    }
    for result in results:
        if result.get("status") == "failed":
            summary["failed_stories"] += 1
            continue
        summary["success_stories"] += 1
        if result.get("is_incomplete"):
            summary["incomplete_stories"] += 1
        summary["total_chapters"] += result.get("chapter_count", 0)

        for field in result.get("missing_story_fields", []):
            summary["stories_missing_field_counts"][field] = summary["stories_missing_field_counts"].get(field, 0) + 1
        for field, count in result.get("chapter_missing_field_counts", {}).items():
            summary["chapter_missing_field_counts"][field] = summary["chapter_missing_field_counts"].get(field, 0) + count
    return summary


class TaskStatus(str, Enum):
    PUBLISHED = "PUBLISHED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DESTROYED = "DESTROYED"


@dataclass
class CrawlTask:
    task_id: str
    story_url: str
    status: TaskStatus = TaskStatus.PUBLISHED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    total_chapters: int = 0
    completed_chapters: int = 0
    current_chapter: str = ""
    error: str = ""
    output_dir: str = ""
    pause_event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)


class CrawlTaskManager:
    def __init__(
        self,
        max_workers=4,
        base_output_dir="output",
        cookie_str=None,
        request_min_interval=0.8,
        retry_max_retries=4,
        retry_base_delay=1.2,
        retry_max_delay=20.0,
        retry_jitter=0.5,
        chapter_min_delay=1.0,
        chapter_max_delay=12.0,
        chapter_delay_step=1.4,
        chapter_max_attempts=3,
    ):
        self.max_workers = max_workers
        self.base_output_dir = base_output_dir
        self.cookies = parse_cookie_string(cookie_str) if cookie_str else None
        self.request_min_interval = request_min_interval
        self.retry_max_retries = retry_max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay
        self.retry_jitter = retry_jitter
        self.chapter_min_delay = chapter_min_delay
        self.chapter_max_delay = chapter_max_delay
        self.chapter_delay_step = chapter_delay_step
        self.chapter_max_attempts = chapter_max_attempts
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.lock = threading.RLock()
        self.tasks = {}
        self.futures = {}

    def publish(self, story_url):
        task_id = str(uuid.uuid4())[:8]
        task = CrawlTask(task_id=task_id, story_url=story_url)
        with self.lock:
            self.tasks[task_id] = task
            future = self.executor.submit(self._run_task, task_id)
            self.futures[task_id] = future
        return task_id

    def pause(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            if task.status in {TaskStatus.RUNNING, TaskStatus.PUBLISHED, TaskStatus.PAUSED}:
                task.pause_event.set()
                task.status = TaskStatus.PAUSED
                task.updated_at = time.time()
                return True
        return False

    def resume(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            if task.status == TaskStatus.PAUSED:
                task.pause_event.clear()
                task.status = TaskStatus.RUNNING
                task.updated_at = time.time()
                return True
        return False

    def destroy(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.DESTROYED}:
                return False
            task.cancel_event.set()
            task.pause_event.clear()
            task.status = TaskStatus.DESTROYED
            task.updated_at = time.time()
            return True

    def get_task(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return None
            return {
                "task_id": task.task_id,
                "story_url": task.story_url,
                "status": task.status.value,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "total_chapters": task.total_chapters,
                "completed_chapters": task.completed_chapters,
                "current_chapter": task.current_chapter,
                "error": task.error,
                "output_dir": task.output_dir,
            }

    def list_tasks(self):
        with self.lock:
            task_ids = list(self.tasks.keys())
        return [self.get_task(task_id) for task_id in task_ids]

    def wait_all(self):
        while True:
            all_done = True
            with self.lock:
                task_ids = list(self.tasks.keys())
            for task_id in task_ids:
                task = self.tasks[task_id]
                if task.status in {TaskStatus.PUBLISHED, TaskStatus.RUNNING, TaskStatus.PAUSED}:
                    all_done = False
                    break
            if all_done:
                return
            time.sleep(0.5)

    def shutdown(self):
        self.executor.shutdown(wait=True)

    def _run_task(self, task_id):
        task = self.tasks[task_id]
        try:
            while task.pause_event.is_set():
                if task.cancel_event.is_set():
                    self._mark_destroyed(task)
                    return
                time.sleep(0.2)

            if task.cancel_event.is_set():
                self._mark_destroyed(task)
                return

            with self.lock:
                if task.status != TaskStatus.DESTROYED:
                    task.status = TaskStatus.RUNNING
                    task.updated_at = time.time()

            metadata = get_wattpad_metadata(
                task.story_url,
                self.cookies,
                min_interval=self.request_min_interval,
                max_retries=self.retry_max_retries,
                base_delay=self.retry_base_delay,
                max_delay=self.retry_max_delay,
                jitter=self.retry_jitter,
            )
            if task.cancel_event.is_set():
                self._mark_destroyed(task)
                return
            if not metadata:
                self._mark_failed(task, "无法获取故事元信息")
                return

            chapters = metadata.get("chapters", [])
            with self.lock:
                task.total_chapters = len(chapters)
                task.updated_at = time.time()

            story_title = metadata.get("title") or task.task_id
            story_slug = self._sanitize_filename(story_title)
            output_dir = Path(self.base_output_dir) / f"{task.task_id}_{story_slug}"
            output_dir.mkdir(parents=True, exist_ok=True)
            with self.lock:
                task.output_dir = str(output_dir)
                task.updated_at = time.time()

            metadata_path = output_dir / "metadata.json"
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

            chapter_delay = self.chapter_min_delay
            for idx, chapter in enumerate(chapters, start=1):
                if task.cancel_event.is_set():
                    self._mark_destroyed(task)
                    return
                while task.pause_event.is_set():
                    with self.lock:
                        if task.status != TaskStatus.DESTROYED:
                            task.status = TaskStatus.PAUSED
                            task.updated_at = time.time()
                    if task.cancel_event.is_set():
                        self._mark_destroyed(task)
                        return
                    time.sleep(0.2)
                with self.lock:
                    if task.status != TaskStatus.DESTROYED:
                        task.status = TaskStatus.RUNNING
                        task.current_chapter = chapter.get("title", "")
                        task.updated_at = time.time()
                self._controlled_wait(task, chapter_delay)
                content = None
                for _ in range(self.chapter_max_attempts):
                    if task.cancel_event.is_set():
                        self._mark_destroyed(task)
                        return
                    content = get_chapter_content(
                        chapter.get("url", ""),
                        self.cookies,
                        pause_event=task.pause_event,
                        cancel_event=task.cancel_event,
                        min_interval=self.request_min_interval,
                        max_retries=self.retry_max_retries,
                        base_delay=self.retry_base_delay,
                        max_delay=self.retry_max_delay,
                        jitter=self.retry_jitter,
                    )
                    if content:
                        break
                    chapter_delay = min(self.chapter_max_delay, chapter_delay * self.chapter_delay_step + 0.3)
                    self._controlled_wait(task, chapter_delay)
                if task.cancel_event.is_set():
                    self._mark_destroyed(task)
                    return
                if content:
                    chapter_title = chapter.get("title") or f"chapter_{idx}"
                    chapter_slug = self._sanitize_filename(chapter_title)
                    chapter_path = output_dir / f"{idx:04d}_{chapter_slug}.txt"
                    chapter_path.write_text(content, encoding="utf-8")
                    chapter_delay = max(self.chapter_min_delay, chapter_delay * 0.85)
                else:
                    self._mark_failed(task, f"章节抓取失败: {chapter.get('title') or chapter.get('url') or idx}")
                    return
                with self.lock:
                    task.completed_chapters = idx
                    task.updated_at = time.time()

            with self.lock:
                if task.status != TaskStatus.DESTROYED:
                    task.status = TaskStatus.COMPLETED
                    task.current_chapter = ""
                    task.updated_at = time.time()
        except Exception as exc:
            self._mark_failed(task, str(exc))

    def _mark_failed(self, task, error):
        with self.lock:
            if task.status == TaskStatus.DESTROYED:
                return
            task.status = TaskStatus.FAILED
            task.error = error
            task.updated_at = time.time()

    def _mark_destroyed(self, task):
        with self.lock:
            task.status = TaskStatus.DESTROYED
            task.updated_at = time.time()

    def _sanitize_filename(self, text):
        safe = "".join(ch for ch in text if ch not in '<>:"/\\|?*').strip()
        if not safe:
            return "untitled"
        return safe[:80]

    def _controlled_wait(self, task, seconds):
        end_at = time.time() + max(0.0, seconds)
        while time.time() < end_at:
            if task.cancel_event.is_set():
                return
            while task.pause_event.is_set():
                if task.cancel_event.is_set():
                    return
                time.sleep(0.2)
            remaining = end_at - time.time()
            if remaining <= 0:
                return
            time.sleep(min(0.2, remaining))


def print_table(tasks):
    if not tasks:
        print("没有任务")
        return
    headers = ["task_id", "status", "completed/total", "current_chapter", "output_dir"]
    print(" | ".join(headers))
    for task in tasks:
        progress = f"{task['completed_chapters']}/{task['total_chapters']}"
        print(f"{task['task_id']} | {task['status']} | {progress} | {task['current_chapter']} | {task['output_dir']}")


def run_batch(args):
    manager = CrawlTaskManager(
        max_workers=args.workers,
        base_output_dir=args.output_dir,
        cookie_str=args.cookie or os.environ.get("WATTPAD_COOKIE"),
        request_min_interval=args.request_min_interval,
        retry_max_retries=args.retry_max_retries,
        retry_base_delay=args.retry_base_delay,
        retry_max_delay=args.retry_max_delay,
        retry_jitter=args.retry_jitter,
        chapter_min_delay=args.chapter_min_delay,
        chapter_max_delay=args.chapter_max_delay,
        chapter_delay_step=args.chapter_delay_step,
        chapter_max_attempts=args.chapter_max_attempts,
    )
    story_urls = collect_story_urls(args)
    task_ids = [manager.publish(url) for url in story_urls]
    print(f"已发布任务: {task_ids}")
    while True:
        tasks = manager.list_tasks()
        print_table(tasks)
        active = [t for t in tasks if t["status"] in {TaskStatus.PUBLISHED.value, TaskStatus.RUNNING.value, TaskStatus.PAUSED.value}]
        if not active:
            break
        time.sleep(args.poll_interval)
    manager.shutdown()


def run_console(args):
    manager = CrawlTaskManager(
        max_workers=args.workers,
        base_output_dir=args.output_dir,
        cookie_str=args.cookie or os.environ.get("WATTPAD_COOKIE"),
        request_min_interval=args.request_min_interval,
        retry_max_retries=args.retry_max_retries,
        retry_base_delay=args.retry_base_delay,
        retry_max_delay=args.retry_max_delay,
        retry_jitter=args.retry_jitter,
        chapter_min_delay=args.chapter_min_delay,
        chapter_max_delay=args.chapter_max_delay,
        chapter_delay_step=args.chapter_delay_step,
        chapter_max_attempts=args.chapter_max_attempts,
    )
    print("输入命令: publish <url> | pause <task_id> | resume <task_id> | destroy <task_id> | list | wait | exit")
    try:
        while True:
            raw = input("> ").strip()
            if not raw:
                continue
            parts = raw.split(maxsplit=1)
            cmd = parts[0]
            arg = parts[1] if len(parts) > 1 else ""
            if cmd == "publish" and arg:
                task_id = manager.publish(arg)
                print(f"已发布: {task_id}")
            elif cmd == "pause" and arg:
                print("成功" if manager.pause(arg) else "失败")
            elif cmd == "resume" and arg:
                print("成功" if manager.resume(arg) else "失败")
            elif cmd == "destroy" and arg:
                print("成功" if manager.destroy(arg) else "失败")
            elif cmd == "list":
                print_table(manager.list_tasks())
            elif cmd == "wait":
                manager.wait_all()
                print("全部完成")
            elif cmd == "exit":
                break
            else:
                print("无效命令")
    finally:
        manager.shutdown()


def run_meta_audit(args):
    cookies = parse_cookie_string(args.cookie) if args.cookie else parse_cookie_string(os.environ.get("WATTPAD_COOKIE"))
    story_urls = collect_story_urls(args)
    if not story_urls:
        print("未提供任何故事 URL，请通过 --url 或 --urls-file 传入。")
        return

    results = []
    for story_url in story_urls:
        print(f"正在校对: {story_url}")
        metadata = get_wattpad_metadata(
            story_url,
            cookies,
            min_interval=args.request_min_interval,
            max_retries=args.retry_max_retries,
            base_delay=args.retry_base_delay,
            max_delay=args.retry_max_delay,
            jitter=args.retry_jitter,
        )
        if not metadata:
            results.append(
                {
                    "story_url": story_url,
                    "status": "failed",
                    "error": "无法获取故事元信息",
                }
            )
            continue
        results.append(build_story_meta_audit(story_url, metadata))

    summary = summarize_meta_audit(results)
    report = {
        "summary": summary,
        "stories": results,
    }

    print("\nMeta 校对汇总:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n各故事详情:")
    print(json.dumps(results, ensure_ascii=False, indent=2))

    if args.report_file:
        report_path = Path(args.report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入: {report_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wattpad 并发任务管理器")
    parser.add_argument("--workers", type=int, default=4, help="并发 worker 数量")
    parser.add_argument("--output-dir", default="output", help="输出目录")
    parser.add_argument("--cookie", help="Cookie 字符串")
    parser.add_argument("--request-min-interval", type=float, default=0.8, help="同任务内两次请求最小间隔秒")
    parser.add_argument("--retry-max-retries", type=int, default=4, help="单次请求最大重试次数")
    parser.add_argument("--retry-base-delay", type=float, default=1.2, help="请求重试基础等待秒")
    parser.add_argument("--retry-max-delay", type=float, default=20.0, help="请求重试最大等待秒")
    parser.add_argument("--retry-jitter", type=float, default=0.5, help="请求重试随机抖动秒")
    parser.add_argument("--chapter-min-delay", type=float, default=1.0, help="章节抓取最小间隔秒")
    parser.add_argument("--chapter-max-delay", type=float, default=12.0, help="章节抓取最大间隔秒")
    parser.add_argument("--chapter-delay-step", type=float, default=1.4, help="章节失败后的间隔放大系数")
    parser.add_argument("--chapter-max-attempts", type=int, default=3, help="单章节最大抓取尝试次数")

    subparsers = parser.add_subparsers(dest="mode", required=True)

    batch_parser = subparsers.add_parser("batch", help="批量模式")
    batch_parser.add_argument("--url", action="append", help="故事 URL，可重复传入")
    batch_parser.add_argument("--urls-file", help="每行一个故事 URL")
    batch_parser.add_argument("--poll-interval", type=float, default=2.0, help="状态轮询间隔秒")

    subparsers.add_parser("console", help="交互控制台模式")
    audit_parser = subparsers.add_parser("audit-meta", help="校对并汇总故事 meta 完整性")
    audit_parser.add_argument("--url", action="append", help="故事 URL，可重复传入")
    audit_parser.add_argument("--urls-file", help="每行一个故事 URL")
    audit_parser.add_argument("--report-file", help="输出汇总 JSON 路径")

    args = parser.parse_args()

    if args.mode == "batch":
        run_batch(args)
    elif args.mode == "console":
        run_console(args)
    elif args.mode == "audit-meta":
        run_meta_audit(args)
