from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass(slots=True)
class BotConfig:
    bot_token: str = ""
    client_secrets_file: str = "credentials.json"
    youtube_api_key: str | None = None
    db_path: str = "youtify.sqlite3"
    guild_id: int | None = None
    proxy: str | None = None
    listen_address: str | None = None
    public_base_url: str | None = None
    owner: int | None = None
    whitelist: bool = True
    concurrency_limit: int = 10
    scrape_playlists_interval: int = 3600
    check_rss_interval: int = 900
    web_presence_interval: int = 300
    youtube_api_base: str = "https://www.googleapis.com/youtube/v3"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path = "config.json") -> "BotConfig":
        config_path = Path(path)
        data: dict[str, Any] = {}
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Failed to parse {config_path}: {e}") from e
        known_fields = {field.name for field in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in data.items() if k in known_fields}
        extra = {k: v for k, v in data.items() if k not in known_fields}
        cfg = cls(**kwargs)
        cfg.extra = extra
        return cfg

    def dump_template(self, path: str | Path = "config.example.json") -> None:
        sample = {
            "bot_token": "YOUR_DISCORD_BOT_TOKEN",
            "client_secrets_file": self.client_secrets_file,
            "youtube_api_key": self.youtube_api_key,
            "db_path": self.db_path,
            "guild_id": self.guild_id,
            "proxy": self.proxy,
            "listen_address": "127.0.0.1:8080",
            "public_base_url": "https://your-public-url.example/auth/callback",
            "owner": 123456789012345678,
            "whitelist": True,
            "concurrency_limit": self.concurrency_limit,
            "scrape_playlists_interval": self.scrape_playlists_interval,
            "check_rss_interval": self.check_rss_interval,
            "web_presence_interval": self.web_presence_interval,
        }
        Path(path).write_text(json.dumps(sample, indent=2), encoding="utf-8")