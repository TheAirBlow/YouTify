from discord import Interaction
from embeds import warning_embed

async def require_notification_target(interaction: Interaction, bot) -> bool:
    settings = bot.db.get_user_settings(interaction.user.id)
    if settings and (settings.notify_dms or settings.notify_channel_id):
        return True

    await interaction.response.send_message(
        embed=warning_embed(
            "Notification target missing",
            "Please run `/notify` first so I know where to send notifications",
        ),
        ephemeral=True,
    )

    return False
