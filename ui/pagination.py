from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Awaitable

import discord
from discord import ui
from ui.embeds import warning_embed

@dataclass(slots=True)
class PageViewState:
    pages: list[discord.Embed]
    index: int = 0

class LazyPagedEmbedView(ui.View):
    def __init__(
        self,
        owner_id: int,
        total_pages: int,
        page_fetcher: Callable[[int], Awaitable[discord.Embed]],
        *,
        timeout: float = 300.0,
    ):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.total_pages = total_pages
        self.page_fetcher = page_fetcher
        self.current_index = 0
        self._current_embed: discord.Embed | None = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.first.disabled = self.current_index <= 0
        self.prev.disabled = self.current_index <= 0
        self.next.disabled = self.current_index >= self.total_pages - 1
        self.last.disabled = self.current_index >= self.total_pages - 1
        self.counter.label = f"{self.current_index + 1}/{self.total_pages}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=warning_embed("Not your pagination", "Only the command sender can use these buttons."),
                ephemeral=True,
            )
            return False
        return True

    async def show(self, interaction: discord.Interaction) -> None:
        self._current_embed = await self.page_fetcher(self.current_index)
        self._update_buttons()
        if interaction.response.is_done():
            await interaction.followup.send(embed=self._current_embed, view=self)
        else:
            await interaction.response.send_message(embed=self._current_embed, view=self)

    async def _switch(self, interaction: discord.Interaction, new_index: int) -> None:
        self.current_index = new_index
        self._current_embed = await self.page_fetcher(self.current_index)
        self._update_buttons()
        await interaction.response.edit_message(embed=self._current_embed, view=self)

    @ui.button(label="⏮", style=discord.ButtonStyle.secondary)
    async def first(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._switch(interaction, 0)

    @ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._switch(interaction, max(0, self.current_index - 1))

    @ui.button(label="1/1", style=discord.ButtonStyle.primary, disabled=True)
    async def counter(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer()

    @ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._switch(interaction, min(self.total_pages - 1, self.current_index + 1))

    @ui.button(label="⏭", style=discord.ButtonStyle.secondary)
    async def last(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._switch(interaction, self.total_pages - 1)

class PagedEmbedView(ui.View):
    def __init__(self, owner_id: int, pages: list[discord.Embed], *, timeout: float = 300.0):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.state = PageViewState(pages=pages, index=0)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.first.disabled = self.state.index <= 0
        self.prev.disabled = self.state.index <= 0
        self.next.disabled = self.state.index >= len(self.state.pages) - 1
        self.last.disabled = self.state.index >= len(self.state.pages) - 1
        self.counter.label = f"{self.state.index + 1}/{len(self.state.pages)}"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=warning_embed("Not your pagination", "Only the command sender can use these buttons."),
                ephemeral=True,
            )
            return False
        return True

    async def show(self, interaction: discord.Interaction) -> None:
        self._update_buttons()
        if interaction.response.is_done():
            await interaction.followup.send(embed=self.state.pages[self.state.index], view=self)
        else:
            await interaction.response.send_message(embed=self.state.pages[self.state.index], view=self)

    async def _switch(self, interaction: discord.Interaction, new_index: int) -> None:
        self.state.index = new_index
        self._update_buttons()
        await interaction.response.edit_message(embed=self.state.pages[self.state.index], view=self)

    @ui.button(label="⏮", style=discord.ButtonStyle.secondary)
    async def first(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._switch(interaction, 0)

    @ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._switch(interaction, max(0, self.state.index - 1))

    @ui.button(label="1/1", style=discord.ButtonStyle.primary, disabled=True)
    async def counter(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await interaction.response.defer()

    @ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._switch(interaction, min(len(self.state.pages) - 1, self.state.index + 1))

    @ui.button(label="⏭", style=discord.ButtonStyle.secondary)
    async def last(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self._switch(interaction, len(self.state.pages) - 1)
