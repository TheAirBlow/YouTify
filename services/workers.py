from __future__ import annotations

import asyncio
import contextlib

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from typing import TYPE_CHECKING
from services.youtube import YouTubeQuotaExceeded, YouTubeAPIError

if TYPE_CHECKING:
    from bot import YoutifyBot

@dataclass(slots=True)
class JobProgress:
    name: str
    status: str
    current: int
    total: int

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(100.0, max(0.0, (self.current / self.total) * 100.0))

@dataclass(slots=True)
class UserWorkerState:
    task: asyncio.Task | None = None
    stop_event: asyncio.Event | None = None
    progress: JobProgress | None = None

class UserTaskManager:
    def __init__(self, bot: YoutifyBot):
        self.bot = bot
        self.ratelimited: bool = False
        self._workers: dict[int, UserWorkerState] = {}

    def has_worker(self, user_id: int) -> bool:
        worker = self._workers.get(user_id)
        return bool(worker and worker.task and not worker.task.done())

    def get_active_job(self, user_id: int) -> JobProgress | None:
        worker = self._workers.get(user_id)
        if not worker or not worker.task or worker.task.done():
            return None
        return worker.progress

    def _set_progress(self, user_id: int, *, name: str, status: str, current: int, total: int) -> None:
        worker = self._workers.get(user_id)
        if not worker:
            return
        worker.progress = JobProgress(name=name, status=status, current=current, total=total)

    def _clear_progress(self, user_id: int) -> None:
        worker = self._workers.get(user_id)
        if worker:
            worker.progress = None

    async def stop_user(self, user_id: int) -> None:
        worker = self._workers.get(user_id)
        if not worker:
            self.bot.logger.debug("No worker to stop for user %s", user_id)
            return

        self.bot.logger.debug("Stopping worker for user %s", user_id)
        if worker.stop_event:
            worker.stop_event.set()

        if worker.task:
            worker.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker.task

        self._workers.pop(user_id, None)

    async def restart_user(self, user_id: int, *, priority_job: str | None = None) -> None:
        await self.stop_user(user_id)
        await self.start_user(user_id, priority_job=priority_job)

    async def start_user(self, user_id: int, *, priority_job: str | None = None) -> None:
        channels, playlists = self.bot.db.count_user_objects(user_id)
        if channels <= 0 and playlists <= 0:
            self.bot.logger.debug("No channels or playlists for user %s, skipping worker start", user_id)
            return

        if self.has_worker(user_id):
            self.bot.logger.debug("Worker already running for user %s", user_id)
            return

        log_msg = f"Starting worker for user {user_id} ({channels} channels, {playlists} playlists)"
        if priority_job:
            log_msg += f" [priority job: {priority_job}]"
            self.bot.db.set_worker_schedule(user_id, priority_job=priority_job)
        self.bot.logger.debug(log_msg)

        stop_event = asyncio.Event()
        task = asyncio.create_task(self._run_user(user_id, stop_event), name=f"youtify-user-{user_id}")
        self._workers[user_id] = UserWorkerState(task=task, stop_event=stop_event)

    def _progress_callback(self, user_id: int):
        def _set(name: str, status: str, current: int, total: int) -> None:
            self._set_progress(user_id, name=name, status=status, current=current, total=total)

        return _set

    @staticmethod
    def _seconds_until_youtube_quota_reset() -> float:
        pacific_tz = ZoneInfo("America/Los_Angeles")
        now_pacific = datetime.now(pacific_tz)

        midnight_pacific = (now_pacific + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        time_remaining = (midnight_pacific - now_pacific).total_seconds()
        return max(0.0, time_remaining)

    async def _wait_for_quota_reset(self, cooldown: float) -> None:
        await asyncio.sleep(cooldown)
        self.ratelimited = False
        self.bot.logger.info("YouTube API quota reset, resuming jobs.")

    def _handle_quota_exceeded(self) -> None:
        if self.ratelimited:
            return

        self.ratelimited = True
        cooldown = _seconds_until_youtube_quota_reset()
        self.bot.logger.critical("YouTube API quota exceeded, resuming in %d seconds.", int(cooldown))
        asyncio.create_task(self._wait_for_quota_reset(cooldown))

    async def _run_user(self, user_id: int, stop_event: asyncio.Event) -> None:
        playlist_interval = max(60, int(self.bot.config.scrape_playlists_interval))
        latest_interval = max(60, int(self.bot.config.check_rss_interval))
        loop = asyncio.get_running_loop()

        next_playlist_run, next_latest_run, priority_job = self.bot.db.get_worker_schedule(user_id)

        while not stop_event.is_set():
            if self.ratelimited:
                await asyncio.sleep(60)
                continue

            try:
                now = loop.time()
                due_playlist = now >= next_playlist_run
                due_latest = now >= next_latest_run

                if not due_playlist and not due_latest and priority_job != "full-refresh":
                    wait_seconds = max(1.0, min(next_playlist_run, next_latest_run) - now)
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=wait_seconds)
                    except asyncio.TimeoutError:
                        continue
                    continue

                if due_playlist or priority_job == "full-refresh":
                    try:
                        await self.bot.sync_user_playlists(user_id, progress=self._progress_callback(user_id))
                    except YouTubeQuotaExceeded:
                        self._handle_quota_exceeded()
                        continue
                    except YouTubeAPIError as e:
                        self.bot.logger.error("Unexpected YouTube API error caught: %s", e.message)
                    self._clear_progress(user_id)

                    next_playlist_run = loop.time() + playlist_interval
                    self.bot.db.set_worker_schedule(user_id, next_playlist_run=next_playlist_run)
                    if priority_job != "full-refresh":
                        continue

                if due_latest or priority_job == "full-refresh":
                    try:
                        await self.bot.scrape_latest_videos(user_id, progress=self._progress_callback(user_id))
                    except YouTubeQuotaExceeded:
                        self._handle_quota_exceeded()
                        continue
                    except YouTubeAPIError as e:
                        self.bot.logger.error("Unexpected YouTube API error caught: %s", e.message)
                    self._clear_progress(user_id)

                    next_latest_run = loop.time() + latest_interval
                    priority_job = None
                    self.bot.db.set_worker_schedule(user_id, next_latest_run=next_latest_run, priority_job=priority_job)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                self.bot.logger.exception("User worker failed for %s", user_id)
                self._clear_progress(user_id)