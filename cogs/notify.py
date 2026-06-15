from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from embeds import error_embed, success_embed

class NotifyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="notify", description="Set your notification target")
    async def notify(self, interaction: discord.Interaction, channel: discord.TextChannel | None, dms: bool | None) -> None:
        self.bot.db.ensure_user(interaction.user.id)
        if dms:
            self.bot.db.set_notify_target(interaction.user.id, interaction.guild_id, None, True)
            await interaction.response.send_message(embed=success_embed("DM notifications enabled", "I will now DM you when updates are available."), ephemeral=True)
            return
        if channel is None:
            settings = self.bot.db.get_user_settings(interaction.user.id)
            channel_id, is_dm = settings.get("notify_channel_id"), settings.get("notify_dms")
            if not channel_id and not is_dm:
                await interaction.response.send_message(
                    embed=error_embed("No notification target configured", "Please choose a guild channel or use `dms:true`."),
                    ephemeral=True)
                return

            target_state = f"<#{channel_id}>" if channel_id else "Direct Messages"
            await interaction.response.send_message(
                embed=info_embed("Notification Settings",
                                 f"Your current notification target is set to: **{target_state}**"),
                ephemeral=True
            )
            return

        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message(embed=error_embed("Missing permission", "You need the Manage Channels permission to use a channel notification target."), ephemeral=True)
            return

        self.bot.db.set_notify_target(interaction.user.id, channel.guild.id, channel.id, False)
        await interaction.response.send_message(embed=success_embed("Notification target updated", f"I will now use {channel.mention} for your notifications."), ephemeral=True)


async def setup(bot):
    await bot.add_cog(NotifyCog(bot))