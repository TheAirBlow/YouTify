from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from embeds import success_embed, warning_embed
from services.utils import require_notification_target
from utils import parse_playlist_identifier

class PlaylistCog(commands.GroupCog, name="playlist"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="add", description="Track a playlist")
    @app_commands.describe(playlist="Playlist link or raw ID")
    async def add(self, interaction: discord.Interaction, playlist: str) -> None:
        if not await require_notification_target(interaction, self.bot):
            return

        self.bot.db.ensure_user(interaction.user.id, interaction.guild_id)
        await interaction.response.defer(ephemeral=True)

        playlist_id = parse_playlist_identifier(playlist)
        playlist = await self.bot.youtube.fetch_playlist(interaction.user.id, playlist_id)
        if not playlist.can_access:
            if playlist_id in {"LL", "LM"} and self.bot.db.get_auth_record(user_id) is None:
                await interaction.followup.send(embed=warning_embed("Liked videos or music playlist", "Please run `/auth` first, then try again."), ephemeral=True)
                return
            await interaction.followup.send(embed=warning_embed("Playlist unavailable", "Failed to fetch the playlist. If it's private, run `/auth` and try again."), ephemeral=True)
            return

        self.bot.db.upsert_playlist(interaction.user.id, playlist_id, playlist.title)
        await self.bot.workers.restart_user(interaction.user.id, priority_job="full-refresh")

        await interaction.followup.send(embed=success_embed("Playlist added", f"**{playlist.title}** is now being tracked."), ephemeral=True)

    @app_commands.command(name="remove", description="Stop tracking a playlist")
    @app_commands.describe(playlist="Playlist link or raw ID")
    async def remove(self, interaction: discord.Interaction, playlist: str) -> None:
        if not await require_notification_target(interaction, self.bot):
            return
        self.bot.db.ensure_user(interaction.user.id, interaction.guild_id)

        playlist_id = parse_playlist_identifier(playlist)
        current = self.bot.db.get_playlist(interaction.user.id, playlist_id)
        if not current:
            await interaction.response.send_message(embed=warning_embed("Playlist missing", "That playlist is not tracked by you."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        affected_channels = self.bot.db.remove_playlist(interaction.user.id, playlist_id)
        await self.bot.workers.restart_user(interaction.user.id)

        self.bot.logger.info(
            "User %s removed playlist %s, affecting %s channels",
            interaction.user.id, playlist_id, affected_channels
        )

        await interaction.followup.send(
            embed=success_embed(
                "Playlist removed",
                f"**{current.title}** was removed. {affected_channels} channels have been affected."
            ),
            ephemeral=True
        )

    @app_commands.command(name="list", description="List tracked playlists")
    async def list(self, interaction: discord.Interaction) -> None:
        if not await require_notification_target(interaction, self.bot):
            return
        self.bot.db.ensure_user(interaction.user.id, interaction.guild_id)

        total_playlists = self.bot.db.count_playlists(interaction.user.id)
        if total_playlists == 0:
            await interaction.followup.send(embed=warning_embed("No playlists found", "You didnt add any playlists yet."), ephemeral=True)
            return

        from embeds import make_embed
        from constants import Palette

        items_per_page = 10
        total_pages = (total_playlists + items_per_page - 1) // items_per_page

        async def fetch_page(page_index: int) -> discord.Embed:
            offset = page_index * items_per_page
            playlists = self.bot.db.list_playlists_paginated(
                interaction.user.id,
                limit=items_per_page,
                offset=offset
            )

            rows = [f"[{pl.title}](https://www.youtube.com/playlist?list={pl.playlist_id})" for pl in playlists]
            embed = make_embed(f"Tracked Playlists ({page_index + 1}/{total_pages})", color=Palette.INFO)
            embed.description = "\n".join(rows)
            return embed

        from ui.pagination import LazyPagedEmbedView
        view = LazyPagedEmbedView(interaction.user.id, total_pages, fetch_page)
        await view.show(interaction)

async def setup(bot):
    await bot.add_cog(PlaylistCog(bot))