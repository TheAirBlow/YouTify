from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from embeds import error_embed, success_embed, warning_embed, make_embed
from services.utils import require_notification_target
from utils import parse_channel_identifier
from constants import Palette

class ChannelCog(commands.GroupCog, name="channel"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="add", description="Track a channel manually")
    @app_commands.describe(channel="Channel link, handle, or raw ID")
    async def add(self, interaction: discord.Interaction, channel: str) -> None:
        if not await require_notification_target(interaction, self.bot):
            return

        self.bot.db.ensure_user(interaction.user.id)
        channel_id = parse_channel_identifier(channel)
        current = self.bot.db.get_channel(interaction.user.id, channel_id)
        if current and "user" in current.trackers:
            await interaction.response.send_message(embed=error_embed("Already pinned", "That channel is already pinned by you."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        if current:
            self.bot.db.add_channel_tracker(interaction.user.id, channel_id, "user")
            await interaction.followup.send(embed=success_embed("Channel pinned", f"`{current.title}` is now pinned and will stay tracked."), ephemeral=True)
            await self.bot.workers.restart_user(interaction.user.id)
            return

        result = self.bot.youtube.fetch_channel_feed(channel_id)
        if not result:
            await interaction.followup.send(embed=error_embed("Channel not found", f"Failed to fetch the specified channel."), ephemeral=True)

        title, _ = result
        self.bot.db.upsert_channel(interaction.user.id, channel_id, title, trackers=["user"])
        await interaction.followup.send(embed=success_embed("Channel added", f"`{title}` was successfully added."), ephemeral=True)
        await self.bot.workers.restart_user(interaction.user.id)

    @app_commands.command(name="remove", description="Remove a tracked channel or unpin it")
    @app_commands.describe(channel="Channel link, handle, or raw ID")
    async def remove(self, interaction: discord.Interaction, channel: str) -> None:
        if not await require_notification_target(interaction, self.bot):
            return
        self.bot.db.ensure_user(interaction.user.id)

        channel_id = parse_channel_identifier(channel)
        current = self.bot.db.get_channel(interaction.user.id, channel_id)
        if not current:
            await interaction.response.send_message(embed=warning_embed("Channel missing", "That channel is not tracked by you."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        if "user" in current.trackers:
            removed = self.bot.db.remove_channel_tracker(interaction.user.id, channel_id, "user")
            if removed is None:
                await interaction.followup.send(embed=success_embed("Channel removed", f"`{channel_id}` was removed."), ephemeral=True)
            else:
                await interaction.followup.send(embed=success_embed("Channel unpinned", f"`{channel_id}` is no longer pinned by you."), ephemeral=True)
            await self.bot.workers.restart_user(interaction.user.id)
            return

        self.bot.db.remove_channel(interaction.user.id, channel_id)
        await interaction.followup.send(embed=success_embed("Channel removed", f"`{channel_id}` was removed."), ephemeral=True)
        await self.bot.workers.restart_user(interaction.user.id)

    @app_commands.command(name="blacklist", description="Blacklist a channel without deleting it")
    @app_commands.describe(channel="Channel link, handle, or raw ID")
    async def blacklist(self, interaction: discord.Interaction, channel: str) -> None:
        if not await require_notification_target(interaction, self.bot):
            return
        self.bot.db.ensure_user(interaction.user.id)

        channel_id = parse_channel_identifier(channel)
        current = self.bot.db.get_channel(interaction.user.id, channel_id)
        await interaction.response.defer(ephemeral=True)

        self.bot.db.set_channel_blacklisted(interaction.user.id, channel_id, title, True)
        await interaction.followup.send(embed=success_embed("Channel blacklisted", f"`{current.title}` will be excluded from scraping."), ephemeral=True)
        await self.bot.workers.restart_user(interaction.user.id)

    @app_commands.command(name="list", description="List tracked channels")
    @app_commands.describe(filter="Filter for list output")
    @app_commands.choices(
        filter=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="Tracked", value="tracked"),
            app_commands.Choice(name="Blacklisted", value="blacklisted"),
            app_commands.Choice(name="Tracked by playlist", value="playlist"),
            app_commands.Choice(name="Manually pinned", value="manual"),
        ]
    )
    async def list(self, interaction: discord.Interaction, filter: app_commands.Choice[str] | None = None) -> None:
        if not await require_notification_target(interaction, self.bot):
            return

        await interaction.response.defer(ephemeral=True)
        self.bot.db.ensure_user(interaction.user.id)

        filter_name = filter.value if filter else "all"
        total_channels = self.bot.db.count_channels(interaction.user.id, filter_name)

        if total_channels == 0:
            await interaction.followup.send(embed=warning_embed("No channels found", "There are no channels matching that filter."), ephemeral=True)
            return

        items_per_page = 10
        total_pages = (total_channels + items_per_page - 1) // items_per_page

        async def fetch_page(page_index: int) -> discord.Embed:
            offset = page_index * items_per_page
            channels = self.bot.db.list_channels_paginated(
                interaction.user.id,
                limit=items_per_page,
                offset=offset,
                filter_name=filter_name
            )
            rows = []
            for record in channels:
                status = []
                if record.blacklisted:
                    status.append("⛔")
                if record.has_manual_tracker:
                    status.append("📌")
                if record.tracked_by_playlist:
                    status.append("📚")
                status_text = " ".join(status) if status else "✓"
                rows.append(f"{status_text} [{record.title}](https://www.youtube.com/channel/{record.channel_id})")

            embed = make_embed(f"Channels ({page_index + 1}/{total_pages})", color=Palette.INFO)
            embed.description = "\n".join(rows)
            return embed

        from ui.pagination import LazyPagedEmbedView
        view = LazyPagedEmbedView(interaction.user.id, total_pages, fetch_page)
        await view.show(interaction)

async def setup(bot):
    await bot.add_cog(ChannelCog(bot))