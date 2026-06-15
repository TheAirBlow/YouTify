from __future__ import annotations

import discord
from discord import ui

from embeds import error_embed, success_embed, warning_embed

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