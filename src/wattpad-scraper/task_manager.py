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
    def __init__(self, max_workers=4, base_output_dir="output", cookie_str=None):
        self.max_workers = max_workers
        self.base_output_dir = base_output_dir
        self.cookies = parse_cookie_string(cookie_str) if cookie_str else None
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

            metadata = get_wattpad_metadata(task.story_url, self.cookies)
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
                content = get_chapter_content(
                    chapter.get("url", ""),
                    self.cookies,
                    pause_event=task.pause_event,
                    cancel_event=task.cancel_event,
                )
                if task.cancel_event.is_set():
                    self._mark_destroyed(task)
                    return
                if content:
                    chapter_title = chapter.get("title") or f"chapter_{idx}"
                    chapter_slug = self._sanitize_filename(chapter_title)
                    chapter_path = output_dir / f"{idx:04d}_{chapter_slug}.txt"
                    chapter_path.write_text(content, encoding="utf-8")
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
    )
    story_urls = []
    if args.url:
        story_urls.extend(args.url)
    if args.urls_file:
        lines = Path(args.urls_file).read_text(encoding="utf-8").splitlines()
        story_urls.extend([line.strip() for line in lines if line.strip()])
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wattpad 并发任务管理器")
    parser.add_argument("--workers", type=int, default=4, help="并发 worker 数量")
    parser.add_argument("--output-dir", default="output", help="输出目录")
    parser.add_argument("--cookie", help="Cookie 字符串")

    subparsers = parser.add_subparsers(dest="mode", required=True)

    batch_parser = subparsers.add_parser("batch", help="批量模式")
    batch_parser.add_argument("--url", action="append", help="故事 URL，可重复传入")
    batch_parser.add_argument("--urls-file", help="每行一个故事 URL")
    batch_parser.add_argument("--poll-interval", type=float, default=2.0, help="状态轮询间隔秒")

    subparsers.add_parser("console", help="交互控制台模式")

    args = parser.parse_args()

    if args.mode == "batch":
        run_batch(args)
    elif args.mode == "console":
        run_console(args)
