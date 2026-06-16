from discord import Interaction
from ui.embeds import warning_embed

async def require_notification_target(interaction: Interaction, bot) -> bool:
    user = bot.db.ensure_user(interaction.user.id)
    if user.notify_dms or user.notify_channel_id:
        return True

    await interaction.response.send_message(
        embed=warning_embed(
            "Notification target missing",
            "Please run `/notify` first so I know where to send notifications"
        ),
        ephemeral=True,
    )

    return False

async def require_not_ratelimited(interaction: Interaction, bot) -> bool:
    if not bot.workers.ratelimited:
        return True

    await interaction.response.send_message(
        embed=warning_embed(
            "YouTube API ratelimited",
            "The bot is unable to fetch anything from YouTube right now, try again later."
        ),
        ephemeral=True,
    )

    return False