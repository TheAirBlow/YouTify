from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from models import Video
from utils import utc_now, CHANNEL_ID_RE

INNERTUBE_BROWSE_URL = "https://www.youtube.com/youtubei/v1/browse"
INNERTUBE_RESOLVE_URL = "https://www.youtube.com/youtubei/v1/navigation/resolve_url"
YOUTUBE_API_PLAYLISTS = "https://www.googleapis.com/youtube/v3/playlists"
YOUTUBE_API_PLAYLIST_ITEMS = "https://www.googleapis.com/youtube/v3/playlistItems"

WEB_CLIENT_VERSION = "2.20260114.08.00"
WEB_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": WEB_CLIENT_VERSION,
        "hl": "en",
        "gl": "US",
    }
}
WEB_HEADERS = {
    "X-YouTube-Client-Name": "1",
    "X-YouTube-Client-Version": WEB_CLIENT_VERSION,
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
}

# "N unit(s) ago" (optionally prefixed with "Streamed"/"Premiered") -> best-effort absolute timestamp.
_RELATIVE_TIME_RE = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago")
_SECONDS_PER_UNIT = {
    "second": 1, "minute": 60, "hour": 3600, "day": 86400,
    "week": 604800, "month": 2629800, "year": 31557600,
}

def _nav(obj: Any, *path: str | int) -> Any:
    cur = obj
    for key in path:
        if isinstance(key, int):
            if not isinstance(cur, list) or key < 0 or key >= len(cur):
                return None
            cur = cur[key]
        else:
            if not isinstance(cur, dict) or key not in cur:
                return None
            cur = cur[key]
    return cur

class YouTubeAPIError(Exception):
    def __init__(self, status: int, reason: str, message: str, source: str = "api"):
        self.status = status
        self.reason = reason
        self.message = message
        self.source = source
        super().__init__(f"YouTube API Error {status} [{reason}]: {message}")

    @classmethod
    async def from_response(cls, response: aiohttp.ClientResponse, fallback_message: str = "API request failed") -> YouTubeAPIError:
        status = response.status
        reason = "unknown_reason"
        message = fallback_message

        try:
            error_data = await response.json()
            if "error" in error_data:
                err_detail = error_data["error"]
                message = err_detail.get("message", message)
                if "errors" in err_detail and len(err_detail["errors"]) > 0:
                    reason = err_detail["errors"][0].get("reason", reason)
        except Exception:
            pass

        if reason in ("quotaExceeded", "dailyLimitExceeded"):
            return YouTubeQuotaExceeded(status, reason, message)

        return cls(status, reason, message)

class YouTubeQuotaExceeded(YouTubeAPIError):
    pass

@dataclass(slots=True)
class Playlist:
    can_access: bool
    source: str
    title: str | None = None
    first_page: dict | None = None

class YouTubeService:
    REFRESH_MAX_ATTEMPTS = 5
    REFRESH_BASE_DELAY = 1.0
    UPLOADS_MAX_PAGES = 3
    PRIVATE_PLAYLIST_IDS = {"LL", "LM", "WL"}

    def __init__(self, bot):
        self.bot = bot
        self._refresh_locks: dict[int, asyncio.Lock] = {}

    def _refresh_lock(self, user_id: int) -> asyncio.Lock:
        lock = self._refresh_locks.get(user_id)
        if lock is None:
            lock = self._refresh_locks[user_id] = asyncio.Lock()
        return lock

    @staticmethod
    def _is_at_or_newer(left: str, right: str) -> bool:
        try:
            return datetime.fromisoformat(left) >= datetime.fromisoformat(right)
        except ValueError:
            return left >= right

    @staticmethod
    def _is_newer(left: str, right: str) -> bool:
        try:
            return datetime.fromisoformat(left) > datetime.fromisoformat(right)
        except ValueError:
            return left > right

    @staticmethod
    def _make_credentials(record) -> Any:
        if not record:
            return None
        try:
            creds = Credentials.from_authorized_user_info(json.loads(record.credential_json))
            return creds
        except (json.JSONDecodeError, ValueError, KeyError):
            return None

    def _api_key(self) -> str | None:
        key = getattr(self.bot.config, "youtube_api_key", None)
        return key or None

    async def _auth_headers(self, user_id: int | None = None) -> tuple[dict[str, str], str]:
        if user_id is not None:
            record = self.bot.db.get_auth_record(user_id)
            creds = self._make_credentials(record)
            if creds:
                if creds.expired and creds.refresh_token:
                    async with self._refresh_lock(user_id):
                        record = self.bot.db.get_auth_record(user_id)
                        creds = self._make_credentials(record) or creds
                        if creds.expired and creds.refresh_token:
                            last_error: Exception | None = None
                            for attempt in range(self.REFRESH_MAX_ATTEMPTS):
                                try:
                                    await asyncio.to_thread(creds.refresh, Request())
                                    self.bot.db.set_auth_record(user_id, creds.to_json())
                                    last_error = None
                                    break
                                except Exception as e:
                                    last_error = e
                                    if attempt < self.REFRESH_MAX_ATTEMPTS - 1:
                                        await asyncio.sleep(self.REFRESH_BASE_DELAY * (2 ** attempt))
                            if last_error is not None:
                                self.bot.logger.warning(
                                    "Failed to refresh Google credentials for user %s after %d attempts: %s",
                                    user_id, self.REFRESH_MAX_ATTEMPTS, last_error,
                                )
                                raise YouTubeAPIError(401, "refresh_failed", "Failed to refresh Google credentials")
                return {"Authorization": f"Bearer {creds.token}"}, "user-auth"
        api_key = self._api_key()
        if api_key:
            return {}, "api-key"
        return {}, "none"

    @staticmethod
    def uploads_playlist_id(channel_id: str) -> str:
        return f"UU{channel_id[2:]}" if channel_id.startswith("UC") else channel_id

    @staticmethod
    def _parse_relative_time(text: str | None) -> str:
        if text:
            match = _RELATIVE_TIME_RE.search(text.lower())
            if match:
                amount = int(match.group(1))
                unit = match.group(2)
                delta = timedelta(seconds=amount * _SECONDS_PER_UNIT[unit])
                return (datetime.now(timezone.utc) - delta).isoformat()
        return utc_now()

    async def _innertube_call(self, url: str, body: dict) -> dict:
        payload = {"context": WEB_CONTEXT, **body}
        headers = {**WEB_HEADERS, "Content-Type": "application/json"}
        async with self.bot.session.post(
            url, params={"prettyPrint": "false"}, json=payload,
            headers=headers, proxy=self.bot.config.proxy
        ) as response:
            if response.status != 200:
                raise YouTubeAPIError(response.status, "unknown_error", "InnerTube request failed", source="innertube")
            try:
                return await response.json()
            except (ValueError, json.JSONDecodeError):
                raise YouTubeAPIError(response.status, "json_parsing_error", "Failed to parse InnerTube response JSON", source="innertube")

    async def _browse_playlist(self, playlist_id: str, continuation: str | None = None) -> dict:
        body = {"continuation": continuation} if continuation else {"browseId": f"VL{playlist_id}"}
        return await self._innertube_call(INNERTUBE_BROWSE_URL, body)

    @staticmethod
    def _parse_browse_page(data: dict, is_continuation: bool) -> tuple[str | None, list[dict], str | None]:
        if is_continuation:
            title = None
            items: list[dict] = []
            for action in data.get("onResponseReceivedActions") or []:
                appended = _nav(action, "appendContinuationItemsAction", "continuationItems")
                if appended:
                    items = appended
                    break
        else:
            title = _nav(data, "metadata", "playlistMetadataRenderer", "title")
            items = _nav(
                data, "contents", "twoColumnBrowseResultsRenderer", "tabs", 0, "tabRenderer",
                "content", "sectionListRenderer", "contents", 0, "itemSectionRenderer", "contents",
            ) or []

        lockups: list[dict] = []
        next_continuation: str | None = None
        for item in items:
            if "continuationItemRenderer" in item:
                next_continuation = _nav(
                    item, "continuationItemRenderer", "continuationEndpoint", "continuationCommand", "token"
                )
                continue
            lockup = item.get("lockupViewModel")
            if lockup:
                lockups.append(lockup)

        return title, lockups, next_continuation

    @staticmethod
    def _parse_lockup_item(lockup: dict) -> tuple[str, str, str | None, str | None, str | None] | None:
        video_id = lockup.get("contentId")
        meta = _nav(lockup, "metadata", "lockupMetadataViewModel")
        title = _nav(meta, "title", "content")
        if not video_id or not title:
            return None

        rows = _nav(meta, "metadata", "contentMetadataViewModel", "metadataRows") or []
        channel_name = None
        channel_id = None
        if rows:
            parts = rows[0].get("metadataParts") or []
            if parts:
                channel_name = _nav(parts[0], "text", "content")
                channel_id = _nav(parts[0], "text", "commandRuns", 0, "onTap", "innertubeCommand", "browseEndpoint", "browseId")
        if not channel_id:
            channel_id = _nav(
                meta, "image", "decoratedAvatarViewModel",
                "rendererContext", "commandContext", "onTap", "innertubeCommand", "browseEndpoint", "browseId",
            )

        relative_time_text = None
        for row in reversed(rows):
            parts = row.get("metadataParts") or []
            if parts and "accessibilityLabel" in parts[-1]:
                relative_time_text = _nav(parts[-1], "text", "content")
                break

        return video_id, title, channel_id, channel_name, relative_time_text

    async def resolve_channel(self, raw_input: str) -> tuple[str, str] | None:
        value = raw_input.strip()
        try:
            if CHANNEL_ID_RE.match(value):
                channel_id = value
            else:
                url = value if value.startswith("http") else f"https://www.youtube.com/@{value.lstrip('@')}"
                data = await self._innertube_call(INNERTUBE_RESOLVE_URL, {"url": url})
                channel_id = _nav(data, "endpoint", "browseEndpoint", "browseId")
                if not channel_id:
                    return None

            data = await self._browse_playlist(self.uploads_playlist_id(channel_id))
        except YouTubeAPIError:
            return None

        title, items, _ = self._parse_browse_page(data, is_continuation=False)
        if not title and not items:
            return None
        if title and title.startswith("Uploads from "):
            title = title[len("Uploads from "):]

        return channel_id, title or channel_id

    async def fetch_channel_uploads(self, channel_id: str, after: str) -> tuple[str | None, list[Video]]:
        playlist_id = self.uploads_playlist_id(channel_id)
        channel_title: str | None = None
        videos: list[Video] = []
        continuation: str | None = None

        for _ in range(self.UPLOADS_MAX_PAGES):
            data = await self._browse_playlist(playlist_id, continuation)
            title, lockups, next_continuation = self._parse_browse_page(data, continuation is not None)
            if title:
                channel_title = title[len("Uploads from "):] if title.startswith("Uploads from ") else title

            stop = False
            for lockup in lockups:
                parsed = self._parse_lockup_item(lockup)
                if parsed is None:
                    continue
                video_id, video_title, video_channel_id, video_channel_name, relative_time = parsed
                published_at = self._parse_relative_time(relative_time)
                if not self._is_at_or_newer(published_at, after):
                    stop = True
                    break
                videos.append(Video(
                    video_id=video_id,
                    title=video_title,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    channel_id=video_channel_id or channel_id,
                    channel_title=video_channel_name or channel_title or "Unknown Channel",
                    published_at=published_at,
                ))

            continuation = next_continuation
            if stop or not continuation:
                break

        return channel_title, videos

    async def _collect_innertube_playlist_items(self, playlist_id: str, first_page: dict | None = None) -> list[Video]:
        videos: list[Video] = []
        continuation: str | None = None

        while True:
            if continuation is None and first_page is not None:
                data = first_page
            else:
                data = await self._browse_playlist(playlist_id, continuation)
            _, lockups, next_continuation = self._parse_browse_page(data, continuation is not None)
            if continuation is None and not lockups and next_continuation is None:
                raise YouTubeAPIError(200, "empty_playlist", "Playlist has no accessible items", source="innertube")

            for lockup in lockups:
                parsed = self._parse_lockup_item(lockup)
                if parsed is None:
                    continue
                video_id, title, channel_id, channel_name, relative_time = parsed
                if not channel_id:
                    continue
                published_at = self._parse_relative_time(relative_time)
                videos.append(Video(
                    video_id=video_id,
                    title=title,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    channel_id=channel_id,
                    channel_title=channel_name or "Unknown Channel",
                    published_at=published_at,
                    added_at=published_at,
                ))

            continuation = next_continuation
            if not continuation:
                break

        return videos

    async def fetch_playlist(self, user_id: int, playlist_id: str) -> Playlist:
        if playlist_id not in self.PRIVATE_PLAYLIST_IDS:
            try:
                data = await self._browse_playlist(playlist_id)
                title, items, _ = self._parse_browse_page(data, is_continuation=False)
                if title or items:
                    return Playlist(True, "innertube", title=title or playlist_id, first_page=data)
            except YouTubeAPIError:
                pass

        return await self._data_api_fetch_playlist(user_id, playlist_id)

    async def fetch_playlist_items(
        self, user_id: int, playlist_id: str, catchup: bool = False, first_page: dict | None = None
    ) -> AsyncIterator[Video]:
        if not catchup and playlist_id not in self.PRIVATE_PLAYLIST_IDS:
            try:
                videos = await self._collect_innertube_playlist_items(playlist_id, first_page)
            except YouTubeAPIError:
                videos = None
            if videos is not None:
                for video in videos:
                    yield video
                return

        async for video in self._data_api_fetch_playlist_items(user_id, playlist_id):
            yield video

    async def _data_api_fetch_playlist(self, user_id: int, playlist_id: str) -> Playlist:
        params = {
            "part": "snippet",
            "id": playlist_id,
            "maxResults": 1
        }

        headers, source = await self._auth_headers(user_id)
        if source == "api-key":
            api_key = self._api_key()
            if not api_key:
                return Playlist(False, source)
            params["key"] = api_key

        async with self.bot.session.get(YOUTUBE_API_PLAYLISTS, params=params, headers=headers, proxy=self.bot.config.proxy) as response:
            if response.status != 200:
                raise await YouTubeAPIError.from_response(response, f"Failed to fetch playlist {playlist_id}")
            try:
                data = await response.json()
            except (ValueError, json.JSONDecodeError):
                raise YouTubeAPIError(response.status, "json_parsing_error", "Failed to parse the response JSON")

        items = data.get("items", [])
        if not items:
            return Playlist(False, source)
        snippet = items[0].get("snippet", {})
        title = snippet.get("title") or playlist_id

        return Playlist(True, source, title=title)

    async def _data_api_fetch_playlist_items(self, user_id: int, playlist_id: str) -> AsyncIterator[Video]:
        headers, source = await self._auth_headers(user_id)
        if source == "none":
            return

        self.bot.logger.debug("Fetching playlist %s for user_id=%s using %s auth", playlist_id, user_id, source)

        params = {
            "part": "snippet,contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50
        }

        api_key = self._api_key()
        if source == "api-key" and api_key:
            params["key"] = api_key

        next_token: str | None = None
        total_videos = 0
        page_count = 0

        while True:
            if next_token:
                params["pageToken"] = next_token
            else:
                params.pop("pageToken", None)
            page_count += 1

            self.bot.logger.debug("Fetching page %d of playlist %s", page_count, playlist_id)
            async with self.bot.session.get(YOUTUBE_API_PLAYLIST_ITEMS, params=params, headers=headers, proxy=self.bot.config.proxy) as response:
                if response.status != 200:
                    raise await YouTubeAPIError.from_response(response, f"Failed to fetch playlist {playlist_id} on page {page_count}")
                try:
                    data = await response.json()
                except (ValueError, json.JSONDecodeError):
                    raise YouTubeAPIError(response.status, "json_parsing_error", "Failed to parse the response JSON")

            items = data.get("items", [])
            total_videos += len(items)
            self.bot.logger.debug("Page %d has %d items", page_count, len(items))

            for item in items:
                snippet = item.get("snippet", {})
                details = item.get("contentDetails", {})
                resource = snippet.get("resourceId", {})
                video_id = resource.get("videoId")
                if not video_id:
                    continue
                channel_id = snippet.get("videoOwnerChannelId")
                if not channel_id:
                    continue
                channel_title = snippet.get("videoOwnerChannelTitle")
                published_at = details.get("videoPublishedAt")
                added_at = snippet.get("publishedAt")
                yield Video(
                    video_id=video_id,
                    title=snippet.get("title") or "Untitled",
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    channel_id=channel_id,
                    channel_title=channel_title,
                    published_at=published_at,
                    added_at=added_at
                )

            next_token = data.get("nextPageToken")
            if not next_token:
                self.bot.logger.debug("Reached end of playlist %s on page %s", playlist_id, page_count)
                break

        self.bot.logger.debug("Completed fetching playlist %s: %d videos from %d pages", playlist_id, total_videos, page_count)
