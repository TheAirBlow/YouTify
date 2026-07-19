from dataclasses import dataclass

from sqlalchemy import *
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    user_id = Column(BigInteger, primary_key=True)
    notify_guild_id = Column(BigInteger, nullable=True)
    notify_channel_id = Column(BigInteger, nullable=True)
    notify_dms = Column(Boolean, nullable=False, default=False)
    catchup_enabled = Column(Boolean, nullable=False, default=False)
    is_whitelisted = Column(Boolean, nullable=False, default=False)
    next_playlist_run = Column(Float, nullable=False, default=0.0)
    next_latest_run = Column(Float, nullable=False, default=0.0)
    priority_job = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    channels = relationship("Channel", back_populates="user", cascade="all, delete-orphan")
    playlists = relationship("Playlist", back_populates="user", cascade="all, delete-orphan")
    auth_record = relationship("AuthRecord", back_populates="user", uselist=False, cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User(user_id={self.user_id})>"

class Channel(Base):
    __tablename__ = "channels"

    user_id = Column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    channel_id = Column(String, nullable=False)
    playlist_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    blacklisted = Column(Boolean, nullable=False, default=False)
    last_seen_ts = Column(String, nullable=True)
    rss_failures = Column(Integer, nullable=False, default=0)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "channel_id"),
        Index("idx_channels_user", "user_id"),
        Index("idx_channels_blacklisted", "user_id", "blacklisted"),
    )

    user = relationship("User", back_populates="channels")
    trackers = relationship(
        "ChannelTracker",
        back_populates="channel",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Channel(user_id={self.user_id}, channel_id={self.channel_id})>"

class ChannelTracker(Base):
    __tablename__ = "channel_trackers"

    user_id = Column(BigInteger, nullable=False)
    channel_id = Column(String, nullable=False)
    tracker_id = Column(String, nullable=False)
    created_at = Column(String, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "channel_id", "tracker_id"),
        ForeignKeyConstraint(
            ["user_id", "channel_id"],
            ["channels.user_id", "channels.channel_id"],
            ondelete="CASCADE"
        ),
        Index("idx_channel_trackers_user", "user_id"),
        Index("idx_channel_trackers_tracker", "user_id", "tracker_id"),
    )

    channel = relationship(
        "Channel",
        back_populates="trackers"
    )

    def __repr__(self) -> str:
        return f"<ChannelTracker(user_id={self.user_id}, channel_id={self.channel_id}, tracker_id={self.tracker_id})>"

class Playlist(Base):
    __tablename__ = "playlists"

    user_id = Column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    playlist_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    can_access = Column(Boolean, nullable=False, default=False)
    last_synced_ts = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "playlist_id"),
        Index("idx_playlists_user", "user_id"),
    )

    user = relationship("User", back_populates="playlists")

    def __repr__(self) -> str:
        return f"<Playlist(user_id={self.user_id}, playlist_id={self.playlist_id})>"

class AuthRecord(Base):
    __tablename__ = "auth_records"

    user_id = Column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True)
    credential_json = Column(Text, nullable=False)
    email = Column(String, nullable=True)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    user = relationship("User", back_populates="auth_record")

    def __repr__(self) -> str:
        return f"<AuthRecord(user_id={self.user_id})>"

@dataclass(slots=True)
class Video:
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