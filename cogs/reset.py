from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ui.embeds import success_embed
from services.utils import require_notification_target

class ResetCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="reset", description="Remove all of your data")
    async def reset(self, interaction: discord.Interaction) -> None:
        if not await require_notification_target(interaction, self.bot):
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                title="Resetting data",
                description="⏳ Step 1/2: Stopping background tasks...",
                color=discord.Color.blue()
            ),
            ephemeral=True
        )
        user_id = interaction.user.id
        await self.bot.workers.stop_user(user_id)

        await interaction.edit_original_response(
            embed=discord.Embed(
                title="Resetting data",
                description="⏳ Step 2/2: Deleting stored entries from database...",
                color=discord.Color.blue()
            )
        )
        self.bot.db.delete_user(user_id)

        await interaction.edit_original_response(
            embed=success_embed(
                "Reset complete",
                "All of your stored entries were removed and tracking has been fully stopped."
            )
        )

async def setup(bot):
    await bot.add_cog(ResetCog(bot))