from __future__ import annotations

import asyncio
import contextlib
import os
import logging
from datetime import datetime
from collections.abc import Callable
from typing import Any
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands

from config import BotConfig
from models import VideoRecord
from schema import Database
from services.auth import GoogleAuthService
from services.youtube import YouTubeService
from services.presence import PresenceRotator
from services.workers import UserTaskManager
from embeds import error_embed
from utils import async_batched, utc_now

ProgressCallback = Callable[[str, str, int, int], None]

class YoutifyBot(commands.Bot):
    def __init__(self, config: BotConfig):
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(command_prefix="", intents=intents, proxy=config.proxy)

        self.config = config
        self.logger = logging.getLogger("youtify")
        self.db = Database(config.db_path)
        self.session: Any = None
        self.youtube = YouTubeService(self)
        self.workers = UserTaskManager(self)
        self.presence = PresenceRotator(self)
        self.auth_service = GoogleAuthService(self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self.config.whitelist:
            return True

        if self.config.owner is not None and interaction.user.id == self.config.owner:
            return True

        if self.db.is_whitelisted(interaction.user.id):
            return True

        await interaction.response.send_message(
            embed=error_embed(
                "Access denied",
                "You are not whitelisted. Please contact the bot owner.",
            ),
            ephemeral=True,
        )
        return False

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession(trust_env=bool(self.config.proxy))
        self.tree.interaction_check = self.interaction_check

        await self.load_extension("cogs.channel")
        await self.load_extension("cogs.playlist")
        await self.load_extension("cogs.catchup")
        await self.load_extension("cogs.notify")
        await self.load_extension("cogs.reset")
        await self.load_extension("cogs.stats")
        await self.load_extension("cogs.refresh")
        await self.load_extension("cogs.auth")
        if self.config.whitelist:
            await self.load_extension("cogs.whitelist")

        await self.auth_service.start()
        self.presence.start()
        if self.config.guild_id is not None:
            guild = discord.Object(id=int(self.config.guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

        for user in self.db.list_users():
            await self.workers.start_user(user.user_id)

    async def on_ready(self) -> None:
        self.logger.info("Logged in as %s", self.user)
        await self.presence.refresh_once()

    async def close(self) -> None:
        try:
            with contextlib.suppress(Exception):
                await self.presence.stop()

            with contextlib.suppress(Exception):
                await self.auth_service.close()

            for user in list(self.workers._workers):
                with contextlib.suppress(Exception):
                    await self.workers.stop_user(user)

            if self.session:
                with contextlib.suppress(Exception):
                    await self.session.close()
        finally:
            with contextlib.suppress(Exception):
                self.db.close()
            await super().close()

    @staticmethod
    def _build_video_notification(video: VideoRecord) -> str:
        dt = datetime.fromisoformat(video.published_at)
        unix_ts = int(dt.timestamp())
        return f"{video.url}\n<t:{unix_ts}:F>, <t:{unix_ts}:R>"

    async def send_video_notification(self, user_id: int, video: VideoRecord) -> bool:
        settings = self.db.get_user_settings(user_id)
        if not settings or (not settings.notify_channel_id and not settings.notify_dms):
            self.logger.debug("No notification target configured for user_id=%s", user_id)
            return False

        content = self._build_video_notification(video)
        try:
            if settings.notify_dms:
                user = await self.fetch_user(user_id)
                await user.send(content=content)
                self.logger.debug("Sent DM notification for user_id=%s, video_id=%s", user_id, video.video_id)
                return True
            elif settings.notify_channel_id:
                channel = self.get_channel(settings.notify_channel_id)
                if channel is None:
                    self.logger.warning("Notification channel not found: channel_id=%s", settings.notify_channel_id)
                    return False
                await channel.send(content=content)
                self.logger.debug("Sent channel notification for user_id=%s, channel_id=%s, video_id=%s",
                                user_id, settings.notify_channel_id, video.video_id)
                return True
        except discord.DiscordException as e:
            self.logger.error(f"Failed to send notification for user_id={user_id}, video_id={video.video_id}: {e}")
            return False
        return False

    async def sync_user_playlists(self, user_id: int, progress: ProgressCallback | None = None) -> None:
        total = self.db.count_playlists(user_id)
        if total == 0:
            self.logger.debug("No playlists found for user_id=%s", user_id)
            return

        self.logger.debug("Syncing %d playlist(s) for user_id=%s (concurrency_limit=%d)", total, user_id, self.config.concurrency_limit)
        settings = self.db.get_user_settings(user_id)
        playlists = self.db.list_playlists(user_id)
        catchup = settings and settings.catchup_enabled
        cutoff = utc_now()

        semaphore = asyncio.Semaphore(self.config.concurrency_limit)
        completed_count = 0

        async def sync_playlist(playlist) -> None:
            nonlocal completed_count
            async with semaphore:
                metadata = await self.youtube.fetch_playlist(user_id, playlist.playlist_id)
                if not metadata.can_access:
                    self.logger.debug("Unable to access playlist %s by user %s", playlist.playlist_id, user_id)
                else:
                    if metadata.title and metadata.title != playlist.title:
                        self.logger.debug("Playlist title updated for user_id=%s: %s (was %s)", user_id, metadata.title, playlist.title)
                        self.db.upsert_playlist(user_id, playlist.playlist_id, metadata.title, is_private=playlist.is_private)

                    feed = self.youtube.fetch_playlist_items(user_id, playlist.playlist_id)

                    async for batch in async_batched(feed, 100):
                        channels: dict[str, str] = {}
                        last_seen: dict[str, str] = {}
                        for video in batch:
                            channels[video.channel_id] = video.channel_title
                            if catchup and (video.channel_id not in last_seen or self.youtube._is_at_or_newer(video.added_at, last_seen[video.channel_id])):
                                last_seen[video.channel_id] = video.added_at
                                continue
                        self.db.upsert_playlist_channels(user_id, playlist.playlist_id, channels, last_seen if catchup else None)

                    self.db.remove_orphaned_playlist_channels(user_id, playlist.playlist_id, cutoff)
                    self.db.set_playlist_synced(user_id, playlist.playlist_id, utc_now())

                completed_count += 1
                if progress:
                    progress("playlist sync", f"Syncing {playlist.title}", completed_count - 1, total)

        tasks = [sync_playlist(playlist) for playlist in playlists]
        await asyncio.gather(*tasks)

    async def scrape_latest_videos(self, user_id: int, progress: ProgressCallback | None = None) -> None:
        total = self.db.count_channels(user_id, "tracked")
        if total == 0:
            self.logger.debug("No tracked channels found for user_id=%s", user_id)
            return

        self.logger.debug("Scraping %d channel(s) for user_id=%s (concurrency_limit=%d)", total, user_id, self.config.concurrency_limit)
        channels = self.db.list_channels(user_id, "tracked")

        semaphore = asyncio.Semaphore(self.config.concurrency_limit)
        completed_count = 0

        async def scrape_channel(channel) -> None:
            nonlocal completed_count
            async with semaphore:
                if channel.last_seen_ts is None:
                    channel.last_seen_ts = utc_now()
                    self.db.upsert_channel(user_id, channel.channel_id, channel.title, last_seen_ts=channel.last_seen_ts)
                    self.logger.debug("Skipping channel %s (%s) since last seen is null", channel.title, channel.channel_id)
                else:
                    result = await self.youtube.fetch_latest_channel_videos(
                        channel.channel_id,
                        after=channel.last_seen_ts,
                    )

                    if result is None:
                        self.logger.debug("Failed to fetch channel videos for %s", channel.channel_id)
                    else:
                        title, videos = result

                        async for video in videos:
                            if self.youtube._is_newer(video.published_at, channel.last_seen_ts):
                                self.logger.debug(
                                    "Found a new video '%s' (%s) by %s (%s) for user %s",
                                    video.title,
                                    video.url,
                                    channel.title,
                                    channel.channel_id,
                                    user_id
                                )

                                await self.send_video_notification(user_id, video)
                            if self.youtube._is_newer(video.published_at, channel.last_seen_ts):
                                self.db.upsert_channel(user_id, channel.channel_id, title, last_seen_ts=video.published_at)

                        self.logger.debug("Processed videos from channel %s", channel.channel_id)

                completed_count += 1
                if progress:
                    progress("latest videos", f"Scraped {channel.title}", completed_count - 1, total)

        tasks = [scrape_channel(channel) for channel in channels]
        await asyncio.gather(*tasks)

def load_bot(config_path: str | Path = "config.json") -> YoutifyBot:
    config = BotConfig.load(config_path)
    if not config.bot_token:
        raise RuntimeError("config.json must contain bot_token")
    if config.proxy:
        os.environ["HTTP_PROXY"] = config.proxy
        os.environ["HTTPS_PROXY"] = config.proxy
        os.environ["ALL_PROXY"] = config.proxy
    return YoutifyBot(config)