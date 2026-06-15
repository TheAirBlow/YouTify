from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from embeds import success_embed
from services.utils import require_notification_target

class CatchupCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="catchup", description="Toggle catchup mode for your playlists")
    async def catchup(self, interaction: discord.Interaction, enabled: bool) -> None:
        if not await require_notification_target(interaction, self.bot):
            return

        self.bot.db.ensure_user(interaction.user.id)

        await interaction.response.defer(ephemeral=True)
        self.bot.db.set_catchup(interaction.user.id, enabled)
        reset_count = self.bot.db.reset_channels_last_seen(interaction.user.id)
        await self.bot.workers.restart_user(interaction.user.id, priority_job="full-refresh")

        if enabled:
            message = f"Catch-up mode enabled, with {reset_count} channels affected.\nPlaylists and videos will be rescanned in the background."
        else:
            message = f"Catch-up mode disabled, with {reset_count} channels affected.\nPlaylists and videos will be rescanned in the background."
        await interaction.followup.send(embed=success_embed("Catch-up updated", message), ephemeral=True)

async def setup(bot):
    await bot.add_cog(CatchupCog(bot))


