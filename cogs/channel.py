from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ui.embeds import error_embed, success_embed, warning_embed, make_embed
from services.utils import require_notification_target, require_not_ratelimited
from utils import parse_channel_identifier, ensure_valid_title
from constants import Palette

class ChannelCog(commands.GroupCog, name="channel"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="add", description="Track a channel manually")
    @app_commands.describe(channel="Channel link, handle, or raw ID")
    async def add(self, interaction: discord.Interaction, channel: str) -> None:
        if not await require_notification_target(interaction, self.bot):
            return
        if not await require_not_ratelimited(interaction, self.bot):
            return

        self.bot.db.ensure_user(interaction.user.id)
        channel_id = parse_channel_identifier(channel)
        current = self.bot.db.get_channel(interaction.user.id, channel_id)
        if current:
            await interaction.response.send_message(
                embed=warning_embed("Already added", f"**{ensure_valid_title(current.title)}** is already added."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        result = self.bot.youtube.fetch_channel_feed(channel_id)
        if not result:
            await interaction.followup.send(embed=error_embed("Channel not found", f"Failed to fetch the specified channel."), ephemeral=True)

        title, _ = result
        self.bot.db.upsert_channel(interaction.user.id, channel_id, title, trackers=["user"])
        await interaction.followup.send(
            embed=success_embed("Channel added", f"**{ensure_valid_title(title)}** was successfully added."),
            ephemeral=True
        )
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
                await interaction.followup.send(
                    embed=success_embed("Channel removed", f"**{ensure_valid_title(current.title)}** was removed."),
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    embed=success_embed("Channel unpinned", f"**{ensure_valid_title(current.title)}** is no longer pinned by you."),
                    ephemeral=True
                )
            await self.bot.workers.restart_user(interaction.user.id)
            return

        self.bot.db.remove_channel(interaction.user.id, channel_id)
        await interaction.followup.send(
            embed=success_embed("Channel removed", f"**{ensure_valid_title(current.title)}** was removed."),
            ephemeral=True
        )
        await self.bot.workers.restart_user(interaction.user.id)

    @app_commands.command(name="pin", description="Pin or unpin a tracked channel")
    @app_commands.describe(
        channel="Channel link, handle, or raw ID",
        pin="True to pin, False to unpin (default: True)"
    )
    async def pin(self, interaction: discord.Interaction, channel: str, pin: bool = True) -> None:
        if not await require_notification_target(interaction, self.bot):
            return

        self.bot.db.ensure_user(interaction.user.id)
        channel_id = parse_channel_identifier(channel)
        current = self.bot.db.get_channel(interaction.user.id, channel_id)

        if not current:
            await interaction.response.send_message(
                embed=error_embed("Not tracked", "You must add this channel before you can pin it."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        tracker_ids = {t.tracker_id for t in current.trackers}
        if pin:
            if "user" in tracker_ids:
                await interaction.followup.send(
                    embed=warning_embed("Already pinned", f"**{ensure_valid_title(current.title)}** is already pinned"),
                    ephemeral=True
                )
            else:
                self.bot.db.add_channel_tracker(interaction.user.id, channel_id, "user")
                await interaction.followup.send(
                    embed=success_embed("Channel pinned", f"**{ensure_valid_title(current.title)}** is now pinned."),
                    ephemeral=True
                )
        else:
            if "user" in tracker_ids:
                self.bot.db.remove_channel_tracker(interaction.user.id, channel_id, "user")
                await interaction.followup.send(
                    embed=success_embed("Channel unpinned", f"**{ensure_valid_title(current.title)}** is no longer pinned."),
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    embed=warning_embed("Not pinned", f"**{ensure_valid_title(current.title)}** was not pinned."),
                    ephemeral=True
                )

        await self.bot.workers.restart_user(interaction.user.id)

    @app_commands.command(name="blacklist", description="Blacklist/Unblacklist a channel from being tracked")
    @app_commands.describe(
        channel="Channel link, handle, or raw ID",
        blacklist="True to blacklist, False to unblacklist (default: True)"
    )
    async def blacklist(self, interaction: discord.Interaction, channel: str, blacklist: bool = True) -> None:
        if not await require_notification_target(interaction, self.bot):
            return

        self.bot.db.ensure_user(interaction.user.id)
        channel_id = parse_channel_identifier(channel)
        current = self.bot.db.get_channel(interaction.user.id, channel_id)

        if not current:
            await interaction.response.send_message(
                embed=error_embed("Not tracked", "You must track this channel before you can blacklist it."),
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        if blacklist:
            if current.blacklisted:
                await interaction.followup.send(
                    embed=warning_embed("Already blacklisted", f"**{ensure_valid_title(current.title)}** is already blacklisted."),
                    ephemeral=True
                )
            else:
                self.bot.db.set_channel_blacklisted(interaction.user.id, channel_id, current.title, True)
                await interaction.followup.send(
                    embed=success_embed("Channel blacklisted", f"**{ensure_valid_title(current.title)}** will now be excluded from scraping."),
                    ephemeral=True
                )
        else:
            if not current.blacklisted:
                await interaction.followup.send(
                    embed=warning_embed("Not blacklisted", f"**{ensure_valid_title(current.title)}** is not currently blacklisted."),
                    ephemeral=True
                )
            else:
                self.bot.db.set_channel_blacklisted(interaction.user.id, channel_id, current.title, False)
                await interaction.followup.send(
                    embed=success_embed("Channel unblacklisted", f"**{ensure_valid_title(current.title)}** will now be included in scraping."),
                    ephemeral=True
                )

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
            for channel in channels:
                status = []
                tracker_ids = {t.tracker_id for t in channel.trackers}
                if channel.blacklisted:
                    status.append("⛔")
                if "user" in tracker_ids:
                    status.append("📌")
                if any(tid != "user" for tid in tracker_ids):
                    status.append("📚")
                status_text = " ".join(status) if status else "✓"
                rows.append(f"{status_text} [{ensure_valid_title(channel.title)}](https://www.youtube.com/channel/{channel.channel_id})")

            embed = make_embed(f"Channels ({page_index + 1}/{total_pages})", color=Palette.INFO)
            embed.description = "\n".join(rows)
            return embed

        from ui.pagination import LazyPagedEmbedView
        view = LazyPagedEmbedView(interaction.user.id, total_pages, fetch_page)
        await view.show(interaction)

async def setup(bot):
    await bot.add_cog(ChannelCog(bot))