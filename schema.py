from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from sqlalchemy.orm import selectinload, sessionmaker
from sqlalchemy.exc import IntegrityError

from models import *
from utils import utc_now

# noinspection PyArgumentList
class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        is_sqlite = self.path.startswith("sqlite")

        if is_sqlite:
            self._engine = create_engine(
                self.path,
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        else:
            self._engine = create_engine(
                self.path,
                poolclass=QueuePool,
                pool_size=10,
                max_overflow=20
            )

        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)

        if is_sqlite:
            with self._engine.connect() as conn:
                conn.execute(text("PRAGMA foreign_keys = ON"))
                conn.commit()

        self.initialize()

    @property
    def connection(self):
        return self._engine.raw_connection()

    def close(self) -> None:
        self._engine.dispose()

    @contextmanager
    def cursor(self):
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def initialize(self) -> None:
        Base.metadata.create_all(self._engine)

    def ensure_user(self, user_id: int) -> User:
        with self.cursor() as session:
            user = session.get(User, user_id)
            if not user:
                now = utc_now()
                user = User(
                    user_id=user_id,
                    notify_channel_id=None,
                    notify_dms=False,
                    catchup_enabled=False,
                    is_whitelisted=False,
                    created_at=now,
                    updated_at=now,
                )
                session.add(user)
            return user

    def set_notify_target(self, user_id: int, guild_id: int | None, channel_id: int | None, dms: bool) -> None:
        self.ensure_user(user_id)
        with self.cursor() as session:
            session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(
                    notify_guild_id=guild_id,
                    notify_channel_id=channel_id,
                    notify_dms=dms,
                    updated_at=utc_now()
                )
            )

    def set_catchup(self, user_id: int, enabled: bool) -> None:
        self.ensure_user(user_id)
        with self.cursor() as session:
            session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(catchup_enabled=enabled, updated_at=utc_now())
            )

    def set_whitelisted(self, user_id: int, enabled: bool) -> None:
        self.ensure_user(user_id)
        with self.cursor() as session:
            session.execute(
                update(User)
                .where(User.user_id == user_id)
                .values(is_whitelisted=enabled, updated_at=utc_now())
            )

    def is_whitelisted(self, user_id: int) -> bool:
        with self.cursor() as session:
            return session.scalar(select(User.is_whitelisted).where(User.user_id == user_id)) or False

    def list_whitelisted_users(self) -> list[int]:
        with self.cursor() as session:
            return list(session.scalars(
                select(User.user_id).where(User.is_whitelisted == True).order_by(User.user_id)
            ))

    def set_worker_schedule(
        self,
        user_id: int,
        *,
        next_playlist_run: float | None = None,
        next_latest_run: float | None = None,
        priority_job: str | None = None,
    ) -> None:
        self.ensure_user(user_id)
        values: dict[str, Any] = {
            "updated_at": utc_now(),
            "priority_job": priority_job
        }

        if next_playlist_run is not None: values["next_playlist_run"] = next_playlist_run
        if next_latest_run is not None: values["next_latest_run"] = next_latest_run

        with self.cursor() as session:
            session.execute(update(User).where(User.user_id == user_id).values(**values))

    def get_worker_schedule(self, user_id: int) -> tuple[float, float, str | None]:
        with self.cursor() as session:
            row = session.execute(
                select(User.next_playlist_run, User.next_latest_run, User.priority_job)
                .where(User.user_id == user_id)
            ).first()

        if row is None:
            return 0.0, 0.0, None
        return float(row[0]), float(row[1]), row[2]

    def delete_user(self, user_id: int) -> None:
        with self.cursor() as session:
            session.execute(delete(User).where(User.user_id == user_id))

    def get_channel(self, user_id: int, channel_id: str) -> Channel | None:
        with self.cursor() as session:
            return session.scalar(
                select(Channel)
                .options(selectinload(Channel.trackers))
                .where(Channel.user_id == user_id, Channel.channel_id == channel_id)
            )

    @staticmethod
    def _apply_channel_filter(query, filter_name: str):
        match filter_name:
            case "blacklisted":
                return query.where(Channel.blacklisted == True)
            case "manual":
                return query.where(
                    Channel.blacklisted == False,
                    Channel.trackers.any(ChannelTracker.tracker_id == "user"),
                )
            case "playlist":
                return query.where(
                    Channel.blacklisted == False,
                    Channel.trackers.any(ChannelTracker.tracker_id != "user"),
                )
            case "tracked":
                return query.where(
                    Channel.blacklisted == False,
                    Channel.trackers.any(),
                )
        return query

    def count_channels(self, user_id: int | None = None, filter_name: str = "all") -> int:
        with self.cursor() as session:
            stmt = select(func.count(Channel.channel_id))
            if user_id is not None:
                stmt = stmt.where(Channel.user_id == user_id)

            stmt = self._apply_channel_filter(stmt, filter_name)
            return session.scalar(stmt) or 0

    def list_channels(self, user_id: int, filter_name: str = "all") -> Iterator[Channel]:
        with self.cursor() as session:
            stmt = select(Channel).options(selectinload(Channel.trackers)).where(Channel.user_id == user_id)
            stmt = self._apply_channel_filter(stmt, filter_name)
            stmt = stmt.order_by(Channel.title, Channel.channel_id)

            yield from session.scalars(stmt).yield_per(100)

    def list_channels_paginated(
        self, user_id: int, limit: int = 5, offset: int = 0, filter_name: str = "all"
    ) -> list[Channel]:
        with self.cursor() as session:
            stmt = select(Channel).options(selectinload(Channel.trackers)).where(Channel.user_id == user_id)
            stmt = self._apply_channel_filter(stmt, filter_name)
            stmt = stmt.order_by(Channel.title, Channel.channel_id).limit(limit).offset(offset)

            return list(session.scalars(stmt).all())

    def upsert_channel(
        self,
        user_id: int,
        channel_id: str,
        title: str,
        *,
        blacklisted: bool | None = None,
        last_seen_ts: str | None = None,
        trackers: Sequence[str] | None = None,
    ) -> Channel:
        now = utc_now()

        with self.cursor() as session:
            channel = session.scalar(
                select(Channel).options(selectinload(Channel.trackers))
                    .where(Channel.user_id == user_id, Channel.channel_id == channel_id)
            )

            resolved_blacklisted = channel.blacklisted if channel and blacklisted is None else bool(blacklisted)
            resolved_last_seen = last_seen_ts if last_seen_ts is not None else (channel.last_seen_ts if channel else None)

            if channel:
                channel.title = title
                channel.blacklisted = resolved_blacklisted
                if resolved_last_seen is not None and (channel.last_seen_ts is None or resolved_last_seen > channel.last_seen_ts):
                    channel.last_seen_ts = resolved_last_seen
                channel.updated_at = now
            else:
                channel = Channel(
                    user_id=user_id, channel_id=channel_id, title=title,
                    blacklisted=resolved_blacklisted, last_seen_ts=resolved_last_seen,
                    created_at=now, updated_at=now,
                )
                session.add(channel)

            if trackers:
                incoming_trackers = set(t for t in trackers if t)

                for tid in incoming_trackers:
                    with session.begin_nested():
                        tracker = ChannelTracker(
                            user_id=user_id,
                            channel_id=channel_id,
                            tracker_id=tid,
                            created_at=now,
                        )
                        session.add(tracker)

                        try:
                            session.flush()
                        except IntegrityError:
                            session.rollback()
                            pass

            return channel

    def upsert_playlist_channels(
        self,
        user_id: int,
        playlist_id: str,
        channels: list[Channel],
        last_seen: dict[str, str] | None = None,
    ) -> None:
        if not channels:
            return

        now = utc_now()
        is_dict_present = last_seen is not None
        last_seen_dict = last_seen or {}

        channel_ids = [c.channel_id for c in channels]
        channel_map = {c.channel_id: c for c in channels}

        with self.cursor() as session:
            existing_channels = session.scalars(
                select(Channel).where(
                    Channel.user_id == user_id,
                    Channel.channel_id.in_(channel_ids)
                )
            ).all()
            existing_map = {c.channel_id: c for c in existing_channels}

            new_channels = []

            for cid, channel in channel_map.items():
                new_last_seen = last_seen_dict.get(cid)
                if cid in existing_map:
                    c = existing_map[cid]
                    c.title = channel.title
                    c.playlist_id = channel.playlist_id
                    c.updated_at = now
                    if not is_dict_present:
                        if c.last_seen_ts is None:
                            c.last_seen_ts = c.updated_at
                    else:
                        if new_last_seen is not None and (c.last_seen_ts is None or new_last_seen > c.last_seen_ts):
                            c.last_seen_ts = new_last_seen
                else:
                    new_channels.append(Channel(
                        user_id=user_id,
                        channel_id=cid,
                        playlist_id=channel.playlist_id,
                        title=channel.title,
                        blacklisted=False,
                        last_seen_ts=new_last_seen if is_dict_present else now,
                        created_at=now,
                        updated_at=now
                    ))

            if new_channels:
                session.add_all(new_channels)
                session.flush()

            existing_trackers = session.scalars(
                select(ChannelTracker.channel_id).where(
                    ChannelTracker.user_id == user_id,
                    ChannelTracker.tracker_id == playlist_id,
                    ChannelTracker.channel_id.in_(channel_ids)
                )
            ).all()
            existing_tracker_set = set(existing_trackers)

            for cid in channel_ids:
                if cid in existing_tracker_set:
                    continue

                with session.begin_nested():
                    tracker = ChannelTracker(
                        user_id=user_id,
                        channel_id=cid,
                        tracker_id=playlist_id,
                        created_at=now
                    )
                    session.add(tracker)

                    try:
                        session.flush()
                    except IntegrityError:
                        session.rollback()
                        pass

    def set_channel_blacklisted(self, user_id: int, channel_id: str, title: str, blacklisted: bool) -> Channel:
        return self.upsert_channel(user_id, channel_id, title, blacklisted=blacklisted)

    def remove_channel(self, user_id: int, channel_id: str) -> None:
        with self.cursor() as session:
            session.execute(delete(ChannelTracker).where(ChannelTracker.user_id == user_id, ChannelTracker.channel_id == channel_id))
            session.execute(delete(Channel).where(Channel.user_id == user_id, Channel.channel_id == channel_id))

    def add_channel_tracker(self, user_id: int, channel_id: str, tracker: str) -> None:
        now = utc_now()
        with self.cursor() as session:
            channel_exists = session.scalar(
                select(Channel.channel_id).where(Channel.user_id == user_id, Channel.channel_id == channel_id))
            if channel_exists:
                tracker_exists = session.scalar(
                    select(ChannelTracker.tracker_id)
                    .where(ChannelTracker.user_id == user_id, ChannelTracker.channel_id == channel_id,
                           ChannelTracker.tracker_id == tracker)
                )
                if not tracker_exists:
                    session.add(
                        ChannelTracker(user_id=user_id, channel_id=channel_id, tracker_id=tracker, created_at=now))

    def remove_channel_tracker(self, user_id: int, channel_id: str, tracker: str) -> bool:
        with self.cursor() as session:
            result = session.execute(
                delete(ChannelTracker)
                .where(ChannelTracker.user_id == user_id, ChannelTracker.channel_id == channel_id,
                       ChannelTracker.tracker_id == tracker)
            )

            if result.rowcount == 0:
                return False

            session.execute(
                delete(Channel)
                .where(
                    Channel.user_id == user_id,
                    Channel.channel_id == channel_id,
                    Channel.blacklisted == False,
                    ~Channel.trackers.any()
                )
            )
        return True

    def remove_orphaned_playlist_channels(self, user_id: int, playlist_id: str, cutoff: str) -> None:
        with self.cursor() as session:
            stale_channel_ids = session.scalars(
                select(Channel.channel_id).join(ChannelTracker).where(
                    Channel.user_id == user_id,
                    ChannelTracker.tracker_id == playlist_id,
                    Channel.updated_at < cutoff
                )
            ).all()

            if stale_channel_ids:
                session.execute(
                    delete(ChannelTracker).where(
                        ChannelTracker.user_id == user_id,
                        ChannelTracker.tracker_id == playlist_id,
                        ChannelTracker.channel_id.in_(stale_channel_ids)
                    )
                )

                session.execute(
                    delete(Channel).where(
                        Channel.user_id == user_id,
                        Channel.channel_id.in_(stale_channel_ids),
                        Channel.blacklisted == False,
                        ~Channel.trackers.any()
                    )
                )

    def reset_channels_last_seen(self, user_id: int) -> None:
        with self.cursor() as session:
            session.execute(
                update(Channel).where(Channel.user_id == user_id).values(last_seen_ts=None, updated_at=utc_now())
            )

    def upsert_playlist(
        self, user_id: int, playlist_id: str, title: str, *, can_access: bool = False
    ) -> Playlist:
        now = utc_now()
        with self.cursor() as session:
            playlist = session.scalar(
                select(Playlist).where(Playlist.user_id == user_id, Playlist.playlist_id == playlist_id))

            if playlist:
                playlist.title = title
                playlist.can_access = can_access
                playlist.updated_at = now
            else:
                playlist = Playlist(
                    user_id=user_id, playlist_id=playlist_id, title=title,
                    can_access=can_access, created_at=now, updated_at=now
                )
                session.add(playlist)

            return playlist

    def get_playlist(self, user_id: int, playlist_id: str) -> Playlist | None:
        with self.cursor() as session:
            return session.scalar(
                select(Playlist).where(Playlist.user_id == user_id, Playlist.playlist_id == playlist_id))

    def list_playlists(self, user_id: int) -> Iterator[Playlist]:
        with self.cursor() as session:
            stmt = select(Playlist).where(Playlist.user_id == user_id).order_by(Playlist.title, Playlist.playlist_id)
            yield from session.scalars(stmt).yield_per(100)

    def count_playlists(self, user_id: int) -> int:
        with self.cursor() as session:
            return session.scalar(select(func.count(Playlist.playlist_id)).where(Playlist.user_id == user_id)) or 0

    def list_playlists_paginated(self, user_id: int, limit: int = 10, offset: int = 0) -> list[Playlist]:
        with self.cursor() as session:
            stmt = select(Playlist).where(Playlist.user_id == user_id).order_by(Playlist.title,
                                                                                Playlist.playlist_id).limit(
                limit).offset(offset)
            return list(session.scalars(stmt).all())

    def set_playlist_synced(self, user_id: int, playlist_id: str, ts: str | None) -> None:
        with self.cursor() as session:
            session.execute(
                update(Playlist)
                .where(Playlist.user_id == user_id, Playlist.playlist_id == playlist_id)
                .values(last_synced_ts=ts, updated_at=utc_now())
            )

    def remove_playlist(self, user_id: int, playlist_id: str) -> int:
        with self.cursor() as session:
            tracker_result = session.execute(
                delete(ChannelTracker).where(ChannelTracker.user_id == user_id,
                                             ChannelTracker.tracker_id == playlist_id)
            )

            session.execute(delete(Playlist).where(Playlist.user_id == user_id, Playlist.playlist_id == playlist_id))

            session.execute(
                delete(Channel).where(
                    Channel.user_id == user_id,
                    Channel.blacklisted == False,
                    ~Channel.trackers.any()
                )
            )

        return tracker_result.rowcount

    def list_users(self) -> list[User]:
        with self.cursor() as session:
            return list(session.scalars(select(User).order_by(User.user_id)).all())

    def count_users(self) -> int:
        with self.cursor() as session:
            return session.scalar(select(func.count(User.user_id))) or 0

    def set_auth_record(self, user_id: int, credential_json: str, email: str | None = None) -> None:
        now = utc_now()
        with self.cursor() as session:
            auth_record = session.scalar(select(AuthRecord).where(AuthRecord.user_id == user_id))

            if auth_record:
                auth_record.credential_json = credential_json
                auth_record.email = email
                auth_record.updated_at = now
            else:
                auth_record = AuthRecord(
                    user_id=user_id, credential_json=credential_json, email=email,
                    created_at=now, updated_at=now
                )
                session.add(auth_record)

    def get_auth_record(self, user_id: int) -> AuthRecord | None:
        with self.cursor() as session:
            return session.scalar(select(AuthRecord).where(AuthRecord.user_id == user_id))

    def remove_auth_record(self, user_id: int) -> None:
        with self.cursor() as session:
            session.execute(delete(AuthRecord).where(AuthRecord.user_id == user_id))

    def stats_snapshot(self) -> StatsSnapshot:
        with self.cursor() as session:
            users = session.scalar(select(func.count(User.user_id))) or 0
            channels = session.scalar(select(func.count(Channel.channel_id))) or 0
            playlists = session.scalar(select(func.count(Playlist.playlist_id))) or 0

            blacklisted = session.scalar(select(func.count(Channel.channel_id)).where(Channel.blacklisted == True)) or 0
            pinned = session.scalar(select(func.count(func.distinct(ChannelTracker.channel_id))).where(
                ChannelTracker.tracker_id == "user")) or 0

            dms = session.scalar(select(func.count(User.user_id)).where(User.notify_dms == True)) or 0
            channel_notifs = session.scalar(
                select(func.count(User.user_id)).where(User.notify_channel_id.isnot(None))) or 0

            manual_channels = session.scalar(select(func.count(Channel.channel_id)).where(
                Channel.blacklisted == False, Channel.trackers.any(ChannelTracker.tracker_id == "user")
            )) or 0

            playlist_channels = session.scalar(select(func.count(Channel.channel_id)).where(
                Channel.blacklisted == False, Channel.trackers.any(ChannelTracker.tracker_id != "user")
            )) or 0

        avg_channels = channels / users if users else 0.0
        avg_playlists = playlists / users if users else 0.0

        return StatsSnapshot(
            users=users, channels=channels, playlists=playlists,
            manual_channels=manual_channels, playlist_channels=playlist_channels,
            blacklisted_channels=blacklisted, avg_channels_per_user=avg_channels,
            avg_playlists_per_user=avg_playlists, pinned_channels=pinned,
            dm_notifications=dms, channel_notifications=channel_notifs,
        )

    def count_user_objects(self, user_id: int) -> tuple[int, int]:
        with self.cursor() as session:
            stmt = select(
                select(func.count(Channel.channel_id)).where(Channel.user_id == user_id).scalar_subquery(),
                select(func.count(Playlist.playlist_id)).where(Playlist.user_id == user_id).scalar_subquery()
            )
            row = session.execute(stmt).one()
            return row[0] or 0, row[1] or 0