import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from queue import Empty, Queue

from story_processing import process_story_dir
from util.llm import AIAPIError


class TaskStatus(str, Enum):
    PUBLISHED = "PUBLISHED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DESTROYED = "DESTROYED"


@dataclass
class AdaptTask:
    task_id: str
    story_dir: str
    story_folder: str
    status: TaskStatus = TaskStatus.PUBLISHED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    total_chapters: int = 0
    completed_chapters: int = 0
    current_chapter: str = ""
    error: str = ""
    result: dict = field(default_factory=dict)
    pause_event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    api_retry_count: int = 0
    api_retry_limit: int = 5
    request_name: str = ""
    request_phase: str = ""
    request_attempt: int = 0
    request_sleep_seconds: float = 0.0


class AdaptTaskManager:
    def __init__(
        self,
        max_workers,
        target_dir_name,
        max_renames,
        dry_run,
        force_rerun,
        analysis_temperature,
        rewrite_temperature,
        analysis_max_tokens,
        rewrite_max_tokens,
    ):
        self.target_dir_name = target_dir_name
        self.max_renames = max_renames
        self.dry_run = dry_run
        self.force_rerun = force_rerun
        self.analysis_temperature = analysis_temperature
        self.rewrite_temperature = rewrite_temperature
        self.analysis_max_tokens = analysis_max_tokens
        self.rewrite_max_tokens = rewrite_max_tokens
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.lock = threading.RLock()
        self.tasks = {}
        self.futures = {}
        self.event_queue = Queue()

    def publish(self, story_dir):
        story_path = Path(story_dir).expanduser().resolve()
        if not story_path.exists() or not story_path.is_dir():
            raise ValueError(f"故事目录不存在或不是目录: {story_path}")
        task_id = str(uuid.uuid4())[:8]
        task = AdaptTask(task_id=task_id, story_dir=str(story_path), story_folder=story_path.name)
        with self.lock:
            self.tasks[task_id] = task
            self.futures[task_id] = self.executor.submit(self._run_task, task_id)
            self._emit_task_event(task, "published")
        return task_id

    def pause(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            if task.status in {TaskStatus.PUBLISHED, TaskStatus.RUNNING, TaskStatus.PAUSED}:
                task.pause_event.set()
                task.status = TaskStatus.PAUSED
                task.updated_at = time.time()
                self._emit_task_event(task, "paused")
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
                self._emit_task_event(task, "resumed")
                return True
        return False

    def stop(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            if task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED, TaskStatus.DESTROYED}:
                return False
            task.cancel_event.set()
            task.pause_event.clear()
            task.status = TaskStatus.STOPPING
            task.updated_at = time.time()
            self._emit_task_event(task, "stopping")
            return True

    def destroy(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            if task.status == TaskStatus.DESTROYED:
                return False
            task.cancel_event.set()
            task.pause_event.clear()
            task.status = TaskStatus.DESTROYED
            task.updated_at = time.time()
            self._emit_task_event(task, "destroyed")
            return True

    def restart(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            if task.status not in {TaskStatus.STOPPED, TaskStatus.FAILED, TaskStatus.COMPLETED}:
                return False
            task.pause_event = threading.Event()
            task.cancel_event = threading.Event()
            task.status = TaskStatus.PUBLISHED
            task.updated_at = time.time()
            task.total_chapters = 0
            task.completed_chapters = 0
            task.current_chapter = ""
            task.error = ""
            task.result = {}
            task.api_retry_count = 0
            self.futures[task_id] = self.executor.submit(self._run_task, task_id)
            self._emit_task_event(task, "restarted")
            return True

    def get_task(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return None
            return {
                "task_id": task.task_id,
                "story_folder": task.story_folder,
                "story_dir": task.story_dir,
                "status": task.status.value,
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "total_chapters": task.total_chapters,
                "completed_chapters": task.completed_chapters,
                "current_chapter": task.current_chapter,
                "error": task.error,
                "result": task.result,
                "api_retry_count": task.api_retry_count,
                "api_retry_limit": task.api_retry_limit,
                "request_name": task.request_name,
                "request_phase": task.request_phase,
                "request_attempt": task.request_attempt,
                "request_sleep_seconds": task.request_sleep_seconds,
            }

    def next_event(self, timeout=None):
        try:
            return self.event_queue.get(timeout=timeout)
        except Empty:
            return None

    def list_tasks(self):
        with self.lock:
            task_ids = list(self.tasks.keys())
        return [self.get_task(task_id) for task_id in task_ids]

    def wait_all(self):
        while True:
            with self.lock:
                task_ids = list(self.tasks.keys())
                active = [
                    task_id
                    for task_id in task_ids
                    if self.tasks[task_id].status
                    in {TaskStatus.PUBLISHED, TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.STOPPING}
                ]
            if not active:
                return
            time.sleep(0.3)

    def shutdown(self, cancel_running=False):
        if cancel_running:
            with self.lock:
                for task in self.tasks.values():
                    if task.status in {TaskStatus.PUBLISHED, TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.STOPPING}:
                        task.cancel_event.set()
                        task.pause_event.clear()
                        if task.status != TaskStatus.DESTROYED:
                            task.status = TaskStatus.STOPPING
                        task.updated_at = time.time()
        self.executor.shutdown(wait=True)

    def _mark_failed(self, task, error):
        with self.lock:
            if task.status == TaskStatus.DESTROYED:
                return
            task.status = TaskStatus.FAILED
            task.error = error
            if not task.result:
                task.result = {
                    "story_folder": task.story_folder,
                    "status": "failed",
                    "chapter_count": task.total_chapters,
                    "target_dir": str(Path(task.story_dir) / self.target_dir_name),
                    "reason": error,
                }
            task.updated_at = time.time()
            self._emit_task_event(task, "failed")

    def _mark_stopped(self, task):
        with self.lock:
            if task.status == TaskStatus.DESTROYED:
                return
            task.status = TaskStatus.STOPPED
            task.updated_at = time.time()
            self._emit_task_event(task, "stopped")

    def _mark_destroyed(self, task):
        with self.lock:
            task.status = TaskStatus.DESTROYED
            task.updated_at = time.time()
            self._emit_task_event(task, "destroyed")

    def _run_task(self, task_id):
        with self.lock:
            task = self.tasks[task_id]

        for attempt in range(1, task.api_retry_limit + 1):
            with self.lock:
                if task.status == TaskStatus.DESTROYED:
                    return
                if task.cancel_event.is_set():
                    self._mark_stopped(task)
                    return
                task.status = TaskStatus.RUNNING
                task.updated_at = time.time()
                self._emit_task_event(task, "running")

            try:
                result = process_story_dir(
                    story_dir=Path(task.story_dir),
                    target_dir_name=self.target_dir_name,
                    max_renames=self.max_renames,
                    dry_run=self.dry_run,
                    force_rerun=self.force_rerun,
                    analysis_temperature=self.analysis_temperature,
                    rewrite_temperature=self.rewrite_temperature,
                    analysis_max_tokens=self.analysis_max_tokens,
                    rewrite_max_tokens=self.rewrite_max_tokens,
                    control={
                        "pause_event": task.pause_event,
                        "cancel_event": task.cancel_event,
                    },
                    progress_callback=lambda payload: self._update_progress(task_id, payload),
                )
                with self.lock:
                    task.result = result
                    task.current_chapter = ""
                    task.api_retry_count = max(0, attempt - 1)
                    task.error = ""
                    task.updated_at = time.time()
                if result.get("status") == "stopped":
                    self._mark_stopped(task)
                    return
                if result.get("status") == "destroyed":
                    self._mark_destroyed(task)
                    return
                with self.lock:
                    if task.status != TaskStatus.DESTROYED:
                        task.status = TaskStatus.COMPLETED
                        task.updated_at = time.time()
                        self._emit_task_event(task, "completed")
                return
            except Exception as exc:
                is_retryable_api_error = self._is_retryable_api_error(exc)
                if is_retryable_api_error and attempt < task.api_retry_limit:
                    with self.lock:
                        if task.status == TaskStatus.DESTROYED:
                            return
                        task.api_retry_count = attempt
                        task.status = TaskStatus.PUBLISHED
                        task.error = f"AI API 连接失败，第{attempt}次重试: {exc}"
                        task.updated_at = time.time()
                        self._emit_task_event(task, "api_retry")
                    backoff_seconds = min(2 ** (attempt - 1), 60)
                    is_cancelled = task.cancel_event.wait(backoff_seconds)
                    if is_cancelled:
                        self._mark_stopped(task)
                        return
                    continue
                if is_retryable_api_error:
                    self._mark_failed(task, f"AI API 连接失败，已重试{task.api_retry_limit}次后跳过: {exc}")
                    return
                self._mark_failed(task, str(exc))
                return

    def _is_retryable_api_error(self, exc):
        error_text = str(exc).lower()
        if "non_retryable" in error_text or "non-retryable" in error_text:
            return False
        if isinstance(exc, AIAPIError):
            return True # Assume AIAPIError is retryable or handled by llm_workflow
        cause = getattr(exc, "__cause__", None)
        if cause and cause.__class__.__name__ in {"APIConnectionError", "APITimeoutError"}:
            return True
        error_text = str(exc).lower()
        retryable_signals = [
            "connection",
            "timeout",
            "timed out",
            "连接",
            "429",
            "try again",
            "remote disconnected",
            "service unavailable",
            "bad gateway",
        ]
        return any(signal in error_text for signal in retryable_signals)

    def _update_progress(self, task_id, payload):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            total_chapters = payload.get("total_chapters")
            completed_chapters = payload.get("completed_chapters")
            current_chapter = payload.get("current_chapter")
            if isinstance(total_chapters, int):
                task.total_chapters = max(0, total_chapters)
            if isinstance(completed_chapters, int):
                task.completed_chapters = max(0, completed_chapters)
            if isinstance(current_chapter, str):
                task.current_chapter = current_chapter
            request_name = payload.get("request_name")
            request_phase = payload.get("request_phase")
            request_attempt = payload.get("request_attempt")
            request_sleep_seconds = payload.get("request_sleep_seconds")
            request_error = payload.get("request_error")
            if isinstance(request_name, str):
                task.request_name = request_name
            if isinstance(request_phase, str):
                task.request_phase = request_phase
            if isinstance(request_attempt, int):
                task.request_attempt = max(0, request_attempt)
                task.api_retry_count = max(0, request_attempt - 1)
            if isinstance(request_sleep_seconds, (int, float)):
                task.request_sleep_seconds = max(0.0, float(request_sleep_seconds))
            if isinstance(request_error, str) and request_error.strip():
                task.error = request_error.strip()
            if task.pause_event.is_set() and task.status != TaskStatus.DESTROYED:
                task.status = TaskStatus.PAUSED
            elif task.status in {TaskStatus.PUBLISHED, TaskStatus.PAUSED}:
                task.status = TaskStatus.RUNNING
            task.updated_at = time.time()
            self._emit_task_event(task, "progress")

    def _emit_task_event(self, task, event_type):
        self.event_queue.put(
            {
                "event_type": event_type,
                "event_at": time.time(),
                "task_id": task.task_id,
                "story_folder": task.story_folder,
                "story_dir": task.story_dir,
                "status": task.status.value,
                "total_chapters": task.total_chapters,
                "completed_chapters": task.completed_chapters,
                "current_chapter": task.current_chapter,
                "error": task.error,
                "api_retry_count": task.api_retry_count,
                "api_retry_limit": task.api_retry_limit,
                "request_name": task.request_name,
                "request_phase": task.request_phase,
                "request_attempt": task.request_attempt,
                "request_sleep_seconds": task.request_sleep_seconds,
            }
        )
