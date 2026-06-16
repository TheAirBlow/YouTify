from __future__ import annotations
from datetime import datetime, timezone
from discord import Embed
from constants import Palette

def make_embed(title: str, description: str | None = None, *, color=Palette.PRIMARY) -> Embed:
    embed = Embed(title=title, description=description, color=color)
    embed.set_footer(text="YouTify")
    embed.timestamp = datetime.now(timezone.utc)
    return embed

def success_embed(title: str, description: str | None = None) -> Embed:
    return make_embed(title, description, color=Palette.SUCCESS)

def warning_embed(title: str, description: str | None = None) -> Embed:
    return make_embed(title, description, color=Palette.WARNING)

def error_embed(title: str, description: str | None = None) -> Embed:
    return make_embed(title, description, color=Palette.ERROR)

def info_embed(title: str, description: str | None = None) -> Embed:
    return make_embed(title, description, color=Palette.INFO)