from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass(slots=True)
class UserSettings:
    user_id: int
    guild_id: Optional[int] = None
    notify_channel_id: Optional[int] = None
    notify_dms: bool = False
    catchup_enabled: bool = False
    next_playlist_run: float = 0.0
    next_latest_run: float = 0.0
    priority_job: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

@dataclass(slots=True)
class ChannelRecord:
    user_id: int
    channel_id: str
    title: str
    blacklisted: bool = False
    last_seen_ts: Optional[str] = None
    trackers: list[str] = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @property
    def has_manual_tracker(self) -> bool:
        return "user" in self.trackers

    @property
    def playlist_trackers(self) -> list[str]:
        return [tracker for tracker in self.trackers if tracker != "user"]

    @property
    def tracked_by_playlist(self) -> bool:
        return any(tracker != "user" for tracker in self.trackers)

@dataclass(slots=True)
class PlaylistRecord:
    user_id: int
    playlist_id: str
    title: str
    is_private: bool = False
    last_seen_ts: Optional[str] = None
    last_synced_ts: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

@dataclass(slots=True)
class AuthRecord:
    user_id: int
    credential_json: str
    email: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

@dataclass(slots=True)
class VideoRecord:
    video_id: str
    title: str
    url: str
    channel_id: str
    channel_title: str
    published_at: str
    added_at: Optional[str] = None
    thumbnail_url: Optional[str] = None

@dataclass(slots=True)
class StatsSnapshot:
    users: int
    channels: int
    playlists: int
    manual_channels: int
    playlist_channels: int
    blacklisted_channels: int
    avg_channels_per_user: float
    avg_playlists_per_user: float
    pinned_channels: int
    dm_notifications: int
    channel_notifications: int

@dataclass(slots=True)
class AuthSessionInfo:
    user_id: int
    state: str
    redirect_uri: str
    authorization_url: str
    created_at: datetime