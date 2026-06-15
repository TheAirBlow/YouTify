from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from models import VideoRecord
from utils import utc_now

YOUTUBE_CHANNEL_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
YOUTUBE_PLAYLIST_FEED = "https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
YOUTUBE_API_SEARCH = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_API_PLAYLISTS = "https://www.googleapis.com/youtube/v3/playlists"
YOUTUBE_API_PLAYLIST_ITEMS = "https://www.googleapis.com/youtube/v3/playlistItems"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

@dataclass(slots=True)
class Playlist:
    can_access: bool
    source: str
    title: str | None = None

class YouTubeService:
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def _is_at_or_newer(left: str, right: str) -> bool:
        try:
            return datetime.fromisoformat(left) >= datetime.fromisoformat(right)
        except ValueError:
            return left >= right

    @staticmethod
    def _is_newer(left: str, right: st) -> bool:
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

    def _auth_headers(self, user_id: int | None = None) -> tuple[dict[str, str], str]:
        if user_id is not None:
            record = self.bot.db.get_auth_record(user_id)
            creds = self._make_credentials(record)
            if creds:
                try:
                    if creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                except Exception:
                    pass
                return {"Authorization": f"Bearer {creds.token}"}, "user-auth"
        api_key = self._api_key()
        if api_key:
            return {}, "api-key"
        return {}, "none"

    async def fetch_playlist(self, user_id: int, playlist_id: str) -> Playlist:
        params = {
            "part": "snippet",
            "id": playlist_id,
            "maxResults": 1
        }

        headers, source = self._auth_headers(user_id)
        if source == "api-key":
            api_key = self._api_key()
            if not api_key:
                return Playlist(False, source)
            params["key"] = api_key

        async with self.bot.session.get(YOUTUBE_API_PLAYLISTS, params=params, headers=headers, proxy=self.bot.config.proxy) as response:
            if response.status != 200:
                return Playlist(False, source)
            try:
                data = await response.json()
            except (ValueError, json.JSONDecodeError):
                return Playlist(False, source)

        items = data.get("items", [])
        if not items:
            return Playlist(False, source)
        snippet = items[0].get("snippet", {})
        title = snippet.get("title") or playlist_id

        return Playlist(True, source, title=title)

    async def fetch_playlist_items(self, user_id: int, playlist_id: str) -> AsyncIterator[VideoRecord]:
        headers, source = self._auth_headers(user_id)
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
                    self.bot.logger.warning("HTTP %d when fetching playlist %s page %d", response.status, playlist_id, page_count)
                    return

                try:
                    data = await response.json()
                except (ValueError, json.JSONDecodeError) as e:
                    self.bot.logger.warning("JSON parse error on playlist %s page %d: %s", playlist_id, page_count, e)
                    return

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
                yield VideoRecord(
                    video_id=video_id,
                    title=snippet.get("title") or "Untitled",
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    channel_id=channel_id,
                    channel_title=channel_title,
                    published_at=published_at,
                    added_at=added_at,
                    thumbnail_url=f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                )

            next_token = data.get("nextPageToken")
            if not next_token:
                self.bot.logger.debug("Reached end of playlist %s on page %s", playlist_id, page_count)
                break

        self.bot.logger.debug("Completed fetching playlist %s: %d videos from %d pages", playlist_id, total_videos, page_count)

    async def fetch_latest_channel_videos(
        self,
        channel_id: str,
        after: str | None = None,
    ) -> tuple[str, AsyncIterator[VideoRecord]] | None:
        if after is None:
            return await self.fetch_channel_api(channel_id)

        rss_result = await self.fetch_channel_feed(channel_id, after)
        if rss_result is None:
            return await self.fetch_channel_api(channel_id, after)

        async def _iterator(videos: list[VideoRecord]) -> AsyncIterator[VideoRecord]:
            for video in videos:
                yield video

        title, all_new, videos = rss_result
        if all_new:
            api_result = await self.fetch_channel_api(channel_id, after)
            return api_result or (title, _iterator(videos))

        return title, _iterator(videos)

    async def fetch_channel_feed(
        self,
        channel_id: str,
        after: str | None = None
    ) -> tuple[str, bool, list[VideoRecord]] | None:
        url = YOUTUBE_CHANNEL_FEED.format(channel_id=channel_id)
        async with self.bot.session.get(url, proxy=self.bot.config.proxy) as response:
            if response.status != 200:
                return None
            try:
                xml = await response.text()
            except Exception:
                return None
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return None

        title: str = root.findtext("atom:title", default="Unknown title", namespaces=NS)
        videos: list[VideoRecord] = []

        entries = root.findall("atom:entry", NS)
        for entry in entries:
            video_id = entry.findtext("yt:videoId", default="", namespaces=NS)
            if not video_id:
                continue

            published_at = entry.findtext("atom:published", default=utc_now(), namespaces=NS) or utc_now()
            if after is not None and not self._is_at_or_newer(published_at, after):
                break

            channel_id = entry.findtext("yt:channelId", default="", namespaces=NS)
            if not channel_id:
                continue

            channel_title = entry.findtext("atom:author/atom:name", default="Unknown author", namespaces=NS)
            link = entry.find("atom:link", NS)

            videos.append(
                VideoRecord(
                    video_id=video_id,
                    title=entry.findtext("atom:title", default="Untitled", namespaces=NS) or "Untitled",
                    url=link.attrib.get("href") if link is not None else f"https://www.youtube.com/watch?v={video_id}",
                    channel_id=channel_id,
                    channel_title=channel_title,
                    published_at=published_at,
                    thumbnail_url=f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                )
            )

        return title, len(entries) == len(videos), videos

    async def fetch_channel_api(
        self,
        channel_id: str,
        after: str | None = None
    ) -> tuple[str, AsyncIterator[VideoRecord]] | None:
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "type": "video",
            "order": "date",
            "maxResults": 50,
        }

        api_key = self._api_key()
        if api_key:
            params["key"] = api_key

        async with self.bot.session.get(YOUTUBE_API_SEARCH, params=params, proxy=self.bot.config.proxy) as response:
            if response.status != 200:
                return None
            try:
                first_page_data = await response.json()
            except (ValueError, json.JSONDecodeError):
                return None

        items = first_page_data.get("items", [])
        channel_title = "Unknown"
        if items:
            channel_title = items[0].get("snippet", {}).get("channelTitle") or channel_title

        async def _iterator() -> AsyncIterator[VideoRecord]:
            data = first_page_data
            while True:
                page_items = data.get("items", [])

                for item in page_items:
                    snippet = item.get("snippet", {})
                    video_id = item.get("id", {}).get("videoId")
                    if not video_id:
                        continue

                    added_at = snippet.get("publishedAt") or utc_now()

                    if after is not None and not self._is_at_or_newer(added_at, after):
                        return

                    yield VideoRecord(
                        video_id=video_id,
                        title=snippet.get("title") or "Untitled",
                        url=f"https://www.youtube.com/watch?v={video_id}",
                        channel_id=channel_id,
                        channel_title=snippet.get("channelTitle") or channel_title,
                        added_at=added_at,
                        published_at=added_at,  # Syncing both fields just in case
                        thumbnail_url=f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
                    )

                next_token = data.get("nextPageToken")
                if not next_token:
                    break

                params["pageToken"] = next_token
                async with self.bot.session.get(YOUTUBE_API_SEARCH, params=params, proxy=self.bot.config.proxy) as resp:
                    if resp.status != 200:
                        break
                    try:
                        data = await resp.json()
                    except (ValueError, json.JSONDecodeError):
                        break

        return channel_title, _iterator()