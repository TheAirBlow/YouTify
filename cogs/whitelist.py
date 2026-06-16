from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ui.embeds import error_embed, success_embed, info_embed, make_embed
from constants import Palette
from services.utils import require_notification_target

class WhitelistCog(commands.GroupCog, name="whitelist"):
    def __init__(self, bot):
        self.bot = bot

    def _is_owner(self, interaction: discord.Interaction) -> bool:
        return self.bot.config.owner is not None and interaction.user.id == self.bot.config.owner

    @app_commands.command(name="add", description="Whitelist a user")
    async def add(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await require_notification_target(interaction, self.bot):
            return
        if not self.bot.config.whitelist:
            await interaction.response.send_message(embed=error_embed("Whitelist disabled", "This bot was configured without the whitelist feature."), ephemeral=True)
            return
        if not self._is_owner(interaction):
            await interaction.response.send_message(embed=error_embed("Not allowed", "Only the configured owner can use this command."), ephemeral=True)
            return
        self.bot.db.set_whitelisted(user.id, True)
        await interaction.response.send_message(embed=success_embed("Whitelist updated", f"{user.mention} is now allowed."), ephemeral=True)

    @app_commands.command(name="remove", description="Remove a user from the whitelist")
    async def remove(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await require_notification_target(interaction, self.bot):
            return
        if not self.bot.config.whitelist:
            await interaction.response.send_message(embed=error_embed("Whitelist disabled", "This bot was configured without the whitelist feature."), ephemeral=True)
            return
        if not self._is_owner(interaction):
            await interaction.response.send_message(embed=error_embed("Not allowed", "Only the configured owner can use this command."), ephemeral=True)
            return
        self.bot.db.set_whitelisted(user.id, False)
        await interaction.response.send_message(embed=success_embed("Whitelist updated", f"{user.mention} was removed from the whitelist."), ephemeral=True)

    @app_commands.command(name="list", description="List all whitelisted users")
    async def list(self, interaction: discord.Interaction) -> None:
        if not self.bot.config.whitelist:
            await interaction.response.send_message(embed=error_embed("Whitelist disabled", "This bot was configured without the whitelist feature."), ephemeral=True)
            return
        if not self._is_owner(interaction):
            await interaction.response.send_message(embed=error_embed("Not allowed", "Only the configured owner can use this command."), ephemeral=True)
            return
        whitelisted = self.bot.db.list_whitelisted_users()
        if not whitelisted:
            await interaction.response.send_message(embed=info_embed("Whitelisted users", "No users are currently whitelisted."), ephemeral=True)
            return
        embed = make_embed(f"Whitelisted Users ({len(whitelisted)})", color=Palette.SUCCESS)
        user_rows = []
        for user_id in whitelisted:
            user_rows.append(f"`{user_id}`")
        embed.description = "\n".join(user_rows)
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(WhitelistCog(bot))