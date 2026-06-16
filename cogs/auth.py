from __future__ import annotations

import discord
from discord import app_commands, ui
from discord.ext import commands

from ui.embeds import info_embed, warning_embed

class RedirectUrlModal(ui.Modal, title="Paste your redirect URL"):
    redirect_url = ui.TextInput(
        label="Redirect URL",
        placeholder="http://127.0.0.1:8080/auth/callback?code=...&state=...",
        style=discord.TextStyle.long,
        required=True,
        max_length=4000,
    )

    def __init__(self, service, state: str, owner_id: int):
        super().__init__(timeout=300)
        self.service = service
        self.state = state
        self.owner_id = owner_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=warning_embed("Not your auth session",
                                    "Only the user who started authentication can use this modal"),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.service.complete_from_redirect_url(self.state, str(self.redirect_url.value))

class AuthPromptView(ui.View):
    def __init__(self, service, owner_id: int, auth_url: str, state: str, *, show_redirect_button: bool, timeout: float = 600.0):
        super().__init__(timeout=timeout)
        self.service = service
        self.owner_id = owner_id
        self.auth_url = auth_url
        self.state = state
        self.add_item(discord.ui.Button(label="Open Google auth", style=discord.ButtonStyle.link, url=auth_url))
        if show_redirect_button:
            redirect_button = discord.ui.Button(label="Paste redirect URL", style=discord.ButtonStyle.secondary)
            redirect_button.callback = self._open_modal
            self.add_item(redirect_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                embed=warning_embed("Not your auth session", "Only the user who started authentication can use these buttons"),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        await self.service.timeout_session(self.state)

    async def _open_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RedirectUrlModal(self.service, self.state, self.owner_id))

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
