from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from embeds import info_embed
from ui.auth_prompt import AuthPromptView

class AuthCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="auth", description="Authenticate your Google account")
    async def auth(self, interaction: discord.Interaction) -> None:
        self.bot.db.ensure_user(interaction.user.id)
        session = await self.bot.auth_service.begin_auth(interaction)
        view = AuthPromptView(
            self.bot.auth_service,
            interaction.user.id,
            session.authorization_url,
            session.state,
            show_redirect_button=not self.bot.auth_service.has_http_server(),
            timeout=self.bot.auth_service.session_timeout_seconds,
        )

        embed = info_embed(
            "Authenticate your Google account",
            "Open the button below, finish Google sign-in, and return here. This is necessary for us to be able to access private playlists.",
        )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        self.bot.auth_service.schedule_publish(session)

async def setup(bot):
    await bot.add_cog(AuthCog(bot))
