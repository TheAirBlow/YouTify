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
from models import Video, Channel
from schema import Database
from services.auth import GoogleAuthService
from services.youtube import YouTubeService, YouTubeAPIError
from services.presence import PresenceRotator
from services.workers import UserTaskManager
from ui.embeds import error_embed
from utils import async_batched, utc_now

ProgressCallback = Callable[[str, str, int, int], None]

class YoutifyBot(commands.Bot):
    def __init__(self, config: BotConfig):
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(command_prefix="", intents=intents, proxy=config.proxy)

        self.config = config
        self.logger = logging.getLogger("youtify")
        self.db = Database(config.database_url)
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
        self.loop.create_task(self._sync_commands())

        self.logger.info("Setting up extension cogs")
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

        self.logger.info("Starting auth service and presence")
        await self.auth_service.start()
        self.presence.start()

        self.logger.info("Starting user worker jobs")
        for user in self.db.list_users():
            await self.workers.start_user(user.user_id)

    async def _sync_commands(self) -> None:
        self.logger.info("Syncing slash commands with discord")
        try:
            if self.config.guild_id is not None:
                guild = discord.Object(id=int(self.config.guild_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()
            self.logger.info("Slash commands synced successfully")
        except Exception as e:
            self.logger.error(f"Failed to sync slash commands: {e}")

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
    def _build_video_notification(video: Video) -> str:
        dt = datetime.fromisoformat(video.published_at)
        unix_ts = int(dt.timestamp())
        return f"{video.url}\n<t:{unix_ts}:F>, <t:{unix_ts}:R>"

    async def send_video_notification(self, user_id: int, video: Video) -> bool:
        user = self.db.ensure_user(user_id)
        if not user.notify_channel_id and not user.notify_dms:
            self.logger.debug("No notification target configured for user %s", user_id)
            return False

        content = self._build_video_notification(video)
        try:
            if user.notify_dms:
                user = await self.fetch_user(user_id)
                await user.send(content=content)
                self.logger.debug("Sent DM notification to user %s for video %s", user_id, video.video_id)
                return True
            elif user.notify_channel_id:
                guild = bot.get_guild(user.notify_guild_id)
                if guild is None:
                    self.logger.warning(
                        "Guild %s for user %s not found",
                        user.notify_channel_id, user.notify_guild_id, user.user_id)
                    return False

                channel = guild.get_channel(user.notify_channel_id)
                if channel is None:
                    self.logger.warning(
                        "Notification channel %s in guild %s for user %s not found",
                        user.notify_channel_id, user.notify_guild_id, user.user_id)
                    return False

                await channel.send(content=content)
                self.logger.debug("Sent channel notification for user %s in channel %s guild %s for video %s",
                                user_id, user.notify_channel_id, user.notify_guild_id, video.video_id)
                return True
        except discord.DiscordException as e:
            self.logger.error(f"Failed to send notification for user {user_id} video {video.video_id}: {e}")
            return False
        return False

    async def sync_user_playlists(self, user_id: int, progress: ProgressCallback | None = None) -> None:
        total = self.db.count_playlists(user_id)
        if total == 0:
            self.logger.debug("No playlists found for user %s", user_id)
            return

        progress("Playlist Sync", f"Waiting for progress...", 0, total)

        self.logger.debug("Syncing %d playlists for user %s (concurrency limit: %d)", total, user_id, self.config.concurrency_limit)
        user = self.db.ensure_user(user_id)
        playlists = self.db.list_playlists(user_id)
        catchup = user.catchup_enabled
        cutoff = utc_now()

        semaphore = asyncio.Semaphore(self.config.concurrency_limit)
        completed_count = 0

        async def sync_playlist(playlist) -> None:
            nonlocal completed_count
            async with semaphore:
                metadata = await self.youtube.fetch_playlist(user_id, playlist.playlist_id)
                if not metadata.can_access:
                    self.logger.debug("Unable to access playlist %s by user %s", playlist.playlist_id, user_id)
                    self.db.upsert_playlist(user_id, playlist.playlist_id, playlist.title, can_access=False)
                else:
                    if metadata.title and metadata.title != playlist.title:
                        self.db.upsert_playlist(user_id, playlist.playlist_id, metadata.title, can_access=True)
                    elif not playlist.can_access:
                        self.db.upsert_playlist(user_id, playlist.playlist_id, playlist.title, can_access=True)

                    try:
                        feed = self.youtube.fetch_playlist_items(user_id, playlist.playlist_id)
                    except YouTubeAPIError as e:
                        if e.status == 404 or e.reason in ("playlistNotFound", "resourceNotFound"):
                            self.db.upsert_playlist(user_id, playlist.playlist_id, playlist.title, can_access=False)
                            return
                        raise

                    async for batch in async_batched(feed, 100):
                        channel_ids: set[Channel] = set()
                        last_seen: dict[str, str] = {}

                        for video in batch:
                            channel_ids.add(video.channel_id)
                            if catchup and (video.channel_id not in last_seen or self.youtube._is_at_or_newer(video.added_at, last_seen[video.channel_id])):
                                last_seen[video.channel_id] = video.added_at
                                continue

                        channels = await self.youtube.fetch_channels(user_id, list(channel_ids))
                        self.db.upsert_playlist_channels(user_id, playlist.playlist_id, channels, last_seen if catchup else None)

                    self.db.remove_orphaned_playlist_channels(user_id, playlist.playlist_id, cutoff)
                    self.db.set_playlist_synced(user_id, playlist.playlist_id, utc_now())

                completed_count += 1
                if progress:
                    progress("Playlist Sync", f"Synced {playlist.title}", completed_count - 1, total)

        async with asyncio.TaskGroup() as tg:
            for playlist in playlists:
                tg.create_task(sync_playlist(playlist))

    async def scrape_latest_videos(self, user_id: int, progress: ProgressCallback | None = None) -> None:
        total = self.db.count_channels(user_id, "tracked")
        if total == 0:
            self.logger.debug("No tracked channels found for user %s", user_id)
            return

        progress("Scraping Videos", f"Waiting for progress...", 0, total)

        self.logger.debug("Scraping %d channels for user %s (concurrency limit: %d)", total, user_id, self.config.concurrency_limit)
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
                    try:
                        title, videos = await self.youtube.fetch_channel_videos(channel, after=channel.last_seen_ts)

                        total_videos = 0
                        async for video in videos:
                            total_videos += 1
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

                        self.logger.debug("Processed %s videos from channel %s", total_videos, channel.channel_id)
                    except YouTubeAPIError as e:
                        if e.status == 404 or e.reason in ("playlistNotFound", "resourceNotFound"):
                            await self.bot.db.remove_channel(user_id, channel.channel_id)
                            return
                        raise

                completed_count += 1
                if progress:
                    progress("Scraping Videos", f"Scraped {channel.title}", completed_count - 1, total)

        async with asyncio.TaskGroup() as tg:
            for channel in channels:
                tg.create_task(scrape_channel(channel))

def load_bot(config_path: str | Path = "config.json") -> YoutifyBot:
    config = BotConfig.load(config_path)
    if not config.bot_token:
        raise RuntimeError("config.json must contain bot_token")
    if config.proxy:
        os.environ["HTTP_PROXY"] = config.proxy
        os.environ["HTTPS_PROXY"] = config.proxy
        os.environ["ALL_PROXY"] = config.proxy
    return YoutifyBot(config)