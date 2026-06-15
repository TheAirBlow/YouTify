from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from bot import YoutifyBot

class PresenceRotator:
    def __init__(self, bot: YoutifyBot):
        self.bot = bot
        self._task: asyncio.Task | None = None
        self._states = ["users", "playlists", "channels"]

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="youtify-presence")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def refresh_once(self) -> None:
        await self.bot.wait_until_ready()
        await self._apply(self._states[0])

    async def _run(self) -> None:
        await self.bot.wait_until_ready()
        idx = 0
        interval = max(60, int(self.bot.config.web_presence_interval))
        while True:
            state = self._states[idx % len(self._states)]
            await self._apply(state)
            idx += 1
            await asyncio.sleep(interval)

    async def _apply(self, state: str) -> None:
        snapshot = self.bot.db.stats_snapshot()
        if state == "users":
            text = f"Watching {snapshot.users} users"
        elif state == "playlists":
            text = f"Watching {snapshot.playlists} playlists"
        else:
            text = f"Watching {snapshot.channels} channels"
        await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=text))
