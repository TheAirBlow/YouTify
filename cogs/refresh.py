from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ui.embeds import success_embed
from services.utils import require_notification_target, require_not_ratelimited

class RefreshCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="refresh", description="Force immediate refresh of playlists and videos")
    async def refresh(self, interaction: discord.Interaction) -> None:
        if not await require_notification_target(interaction, self.bot):
            return
        if not await require_not_ratelimited(interaction, self.bot):
            return
        self.bot.db.ensure_user(interaction.user.id)

        await interaction.response.send_message(
            embed=success_embed(
                "Refresh scheduled",
                "Your playlists and channels will be scraped soon. Check `/me` for progress."
            ),
            ephemeral=True
        )

        await self.bot.workers.restart_user(interaction.user.id, priority_job="full-refresh")

async def setup(bot):
    await bot.add_cog(RefreshCog(bot))