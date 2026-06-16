from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ui.embeds import info_embed

class GlobalStatsView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.blurple, custom_id="refresh_global_stats")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        snapshot = self.bot.db.stats_snapshot()
        embed = create_global_stats_embed(snapshot)

        await interaction.edit_original_response(embed=embed, view=self)

class UserStatsView(discord.ui.View):
    def __init__(self, bot: commands.Bot, user_id: int, guild_id: int | None):
        super().__init__(timeout=180)
        self.bot = bot
        self.user_id = user_id
        self.guild_id = guild_id

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.blurple)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("You cannot refresh someone else's stats!", ephemeral=True)
            return

        await interaction.response.defer()

        channels, playlists = self.bot.db.count_user_objects(self.user_id)
        user = self.bot.db.ensure_user(self.user_id)
        auth_record = self.bot.db.get_auth_record(self.user_id)
        job = self.bot.workers.get_active_job(self.user_id)

        embed = create_user_stats_embed(channels, playlists, user, auth_record, job)
        await interaction.edit_original_response(embed=embed, view=self)

def create_global_stats_embed(snapshot) -> discord.Embed:
    embed = info_embed("Global statistics")
    embed.add_field(name="Tracked",
                    value=f"{snapshot.users} users, {snapshot.channels} channels, {snapshot.playlists} playlists",
                    inline=False)
    embed.add_field(name="Channels",
                    value=f"{snapshot.manual_channels} manually added, {snapshot.playlist_channels} tracked by playlist, "
                          f"{snapshot.blacklisted_channels} blacklisted, {snapshot.pinned_channels} pinned",
                    inline=False)
    embed.add_field(name="Notification targets",
                    value=f"{snapshot.dm_notifications} prefer DMs, {snapshot.channel_notifications} prefer a channel",
                    inline=False)
    embed.add_field(name="Averages",
                    value=f"{snapshot.avg_channels_per_user:.2f} channels/user, {snapshot.avg_playlists_per_user:.2f} playlists/user",
                    inline=False)
    return embed

def create_user_stats_embed(channels, playlists, settings, auth_record, job) -> discord.Embed:
    catchup_state = "Enabled" if settings and settings.catchup_enabled else "Disabled"
    auth_state = "Linked" if auth_record is not None else "Not linked"
    target_state = f"<#{settings.notify_channel_id}>" if settings and settings.notify_channel_id else "Direct Messages" if settings and settings.notify_dms else "Not configured"

    embed = info_embed("Your stats")
    embed.add_field(name="📺 Channels", value=f"{channels} tracked", inline=True)
    embed.add_field(name="📋 Playlists", value=f"{playlists} tracked", inline=True)
    embed.add_field(name="🌐 Google Account", value=auth_state, inline=True)
    embed.add_field(name="🔄 Catch-up", value=catchup_state, inline=True)
    embed.add_field(name="🔔 Notifications", value=target_state, inline=True)
    if job:
        embed.add_field(name="⚙️ Active job", value=f"**{job.name}**", inline=True)
        embed.add_field(name="Status", value=job.status, inline=True)
        embed.add_field(name="Progress", value=f"{job.current}/{job.total} ({job.percent:.1f}%)", inline=True)
    else:
        embed.add_field(name="⚙️ Active jobs", value="None", inline=True)
    return embed

class StatsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="stats", description="Global bot-wide tracking statistics")
    async def global_stats(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=False)
        snapshot = self.bot.db.stats_snapshot()
        embed = create_global_stats_embed(snapshot)

        view = GlobalStatsView(self.bot)
        await interaction.followup.send(embed=embed, view=view, ephemeral=False)

    @app_commands.command(name="me", description="Current settings, your own tracking statistics and job status")
    async def user_stats(self, interaction: discord.Interaction) -> None:
        self.bot.db.ensure_user(interaction.user.id)
        channels, playlists = self.bot.db.count_user_objects(interaction.user.id)
        settings = self.bot.db.ensure_user(interaction.user.id)
        auth_record = self.bot.db.get_auth_record(interaction.user.id)
        job = self.bot.workers.get_active_job(interaction.user.id)
        embed = create_user_stats_embed(channels, playlists, settings, auth_record, job)

        view = UserStatsView(self.bot, interaction.user.id, interaction.guild_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

async def setup(bot):
    await bot.add_cog(StatsCog(bot))