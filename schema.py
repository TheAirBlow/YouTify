from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import timezone
from pathlib import Path
from typing import Iterator, Sequence

from models import AuthRecord, ChannelRecord, PlaylistRecord, StatsSnapshot, UserSettings
from utils import utc_now

# noinspection PyArgumentList
class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self.initialize()

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        cursor = self._connection.cursor()
        try:
            yield cursor
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def initialize(self) -> None:
        with self.cursor() as cursor:
            cursor.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    guild_id INTEGER,
                    notify_channel_id INTEGER,
                    notify_dms INTEGER NOT NULL DEFAULT 0,
                    catchup_enabled INTEGER NOT NULL DEFAULT 0,
                    is_whitelisted INTEGER NOT NULL DEFAULT 0,
                    next_playlist_run REAL NOT NULL DEFAULT 0.0,
                    next_latest_run REAL NOT NULL DEFAULT 0.0,
                    priority_job TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS channels (
                    user_id INTEGER NOT NULL,
                    channel_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    blacklisted INTEGER NOT NULL DEFAULT 0,
                    last_seen_ts TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, channel_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS channel_trackers (
                    user_id INTEGER NOT NULL,
                    channel_id TEXT NOT NULL,
                    tracker_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, channel_id, tracker_id),
                    FOREIGN KEY (user_id, channel_id) REFERENCES channels(user_id, channel_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_channel_trackers_user ON channel_trackers(user_id);
                CREATE INDEX IF NOT EXISTS idx_channel_trackers_tracker ON channel_trackers(user_id, tracker_id);
                CREATE TABLE IF NOT EXISTS playlists (
                    user_id INTEGER NOT NULL,
                    playlist_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    is_private INTEGER NOT NULL DEFAULT 0,
                    last_synced_ts TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, playlist_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS auth_records (
                    user_id INTEGER PRIMARY KEY,
                    credential_json TEXT NOT NULL,
                    email TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_channels_user ON channels(user_id);
                CREATE INDEX IF NOT EXISTS idx_playlists_user ON playlists(user_id);
                CREATE INDEX IF NOT EXISTS idx_channels_blacklisted ON channels(user_id, blacklisted);
                """
            )

        # Migration: add worker scheduling columns if they don't exist
        with self.cursor() as cursor:
            try:
                cursor.execute("SELECT next_playlist_run FROM users LIMIT 1")
            except Exception:
                cursor.execute("ALTER TABLE users ADD COLUMN next_playlist_run REAL NOT NULL DEFAULT 0.0")
            try:
                cursor.execute("SELECT next_latest_run FROM users LIMIT 1")
            except Exception:
                cursor.execute("ALTER TABLE users ADD COLUMN next_latest_run REAL NOT NULL DEFAULT 0.0")
            try:
                cursor.execute("SELECT priority_job FROM users LIMIT 1")
            except Exception:
                cursor.execute("ALTER TABLE users ADD COLUMN priority_job TEXT")

    def ensure_user(self, user_id: int, guild_id: int | None = None) -> None:
        now = utc_now()
        with self.cursor() as cursor:
            cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
            exists = cursor.fetchone() is not None
            if exists:
                cursor.execute(
                    """
                    UPDATE users
                    SET guild_id = COALESCE(?, guild_id), updated_at=?
                    WHERE user_id=?
                    """,
                    (guild_id, now, user_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO users (user_id, guild_id, notify_channel_id, notify_dms, catchup_enabled, is_whitelisted, created_at, updated_at)
                    VALUES (?, ?, NULL, 0, 0, 0, ?, ?)
                    """,
                    (user_id, guild_id, now, now),
                )

    def get_user_settings(self, user_id: int) -> UserSettings | None:
        with self.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return UserSettings(
            user_id=row["user_id"],
            guild_id=row["guild_id"],
            notify_channel_id=row["notify_channel_id"],
            notify_dms=bool(row["notify_dms"]),
            catchup_enabled=bool(row["catchup_enabled"]),
            next_playlist_run=float(row["next_playlist_run"]) if row["next_playlist_run"] else 0.0,
            next_latest_run=float(row["next_latest_run"]) if row["next_latest_run"] else 0.0,
            priority_job=row["priority_job"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def set_notify_target(self, user_id: int, guild_id: int | None, channel_id: int | None, dms: bool) -> None:
        self.ensure_user(user_id, guild_id)
        now = utc_now()
        with self.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users
                SET guild_id=?, notify_channel_id=?, notify_dms=?, updated_at=?
                WHERE user_id=?
                """,
                (guild_id, channel_id, int(dms), now, user_id),
            )

    def set_catchup(self, user_id: int, enabled: bool) -> None:
        self.ensure_user(user_id)
        now = utc_now()
        with self.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET catchup_enabled=?, updated_at=? WHERE user_id=?",
                (int(enabled), now, user_id),
            )

    def set_whitelisted(self, user_id: int, enabled: bool) -> None:
        self.ensure_user(user_id)
        now = utc_now()
        with self.cursor() as cursor:
            cursor.execute(
                "UPDATE users SET is_whitelisted=?, updated_at=? WHERE user_id=?",
                (int(enabled), now, user_id),
            )

    def is_whitelisted(self, user_id: int) -> bool:
        with self.cursor() as cursor:
            cursor.execute("SELECT is_whitelisted FROM users WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
        return bool(row[0]) if row else False

    def list_whitelisted_users(self) -> list[int]:
        with self.cursor() as cursor:
            cursor.execute("SELECT user_id FROM users WHERE is_whitelisted=1 ORDER BY user_id")
            rows = cursor.fetchall()
        return [row[0] for row in rows]

    def set_worker_schedule(self, user_id: int, *, next_playlist_run: float | None = None, next_latest_run: float | None = None, priority_job: str | None = None) -> None:
        """Update worker scheduling state. Only updates provided fields."""
        self.ensure_user(user_id)
        now = utc_now()
        
        updates = []
        params = []
        
        if next_playlist_run is not None:
            updates.append("next_playlist_run=?")
            params.append(next_playlist_run)
        if next_latest_run is not None:
            updates.append("next_latest_run=?")
            params.append(next_latest_run)
        if priority_job is not None:
            updates.append("priority_job=?")
            params.append(priority_job)
        
        if updates:
            updates.append("updated_at=?")
            params.append(now)
            params.append(user_id)
            
            query = f"UPDATE users SET {', '.join(updates)} WHERE user_id=?"
            with self.cursor() as cursor:
                cursor.execute(query, params)

    def get_worker_schedule(self, user_id: int) -> tuple[float, float, str | None]:
        """Get worker scheduling state. Returns (next_playlist_run, next_latest_run, priority_job)."""
        with self.cursor() as cursor:
            cursor.execute("SELECT next_playlist_run, next_latest_run, priority_job FROM users WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
        
        if row is None:
            return 0.0, 0.0, None
        
        return (
            float(row["next_playlist_run"]) if row["next_playlist_run"] else 0.0,
            float(row["next_latest_run"]) if row["next_latest_run"] else 0.0,
            row["priority_job"]
        )

    def delete_user(self, user_id: int) -> None:
        with self.cursor() as cursor:
            cursor.execute("DELETE FROM channel_trackers WHERE user_id=?", (user_id,))
            cursor.execute("DELETE FROM channels WHERE user_id=?", (user_id,))
            cursor.execute("DELETE FROM playlists WHERE user_id=?", (user_id,))
            cursor.execute("DELETE FROM auth_records WHERE user_id=?", (user_id,))
            cursor.execute("DELETE FROM users WHERE user_id=?", (user_id,))

    def get_channel(self, user_id: int, channel_id: str) -> ChannelRecord | None:
        with self.cursor() as cursor:
            cursor.execute("SELECT * FROM channels WHERE user_id=? AND channel_id=?", (user_id, channel_id))
            row = cursor.fetchone()
        return self._row_to_channel(user_id, row) if row else None

    def count_channels(self, user_id: int | None = None, filter_name: str | None = "all") -> int:
        query = "SELECT COUNT(*) FROM channels c"
        conditions: list[str] = []
        params: list[object] = []

        if user_id is not None:
            conditions.append("c.user_id=?")
            params.append(user_id)

        match filter_name:
            case "blacklisted":
                conditions.append("c.blacklisted=1")
            case "manual":
                conditions.append("c.blacklisted=0")
                conditions.append("EXISTS (SELECT 1 FROM channel_trackers ct WHERE ct.user_id = c.user_id AND ct.channel_id = c.channel_id AND ct.tracker_id = 'user')")
            case "playlist":
                conditions.append("c.blacklisted=0")
                conditions.append("EXISTS (SELECT 1 FROM channel_trackers ct WHERE ct.user_id = c.user_id AND ct.channel_id = c.channel_id AND ct.tracker_id != 'user')")
            case "tracked":
                conditions.append("c.blacklisted=0")
                conditions.append("EXISTS (SELECT 1 FROM channel_trackers ct WHERE ct.user_id = c.user_id AND ct.channel_id = c.channel_id)")
            case _:
                pass

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        with self.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()

        return int(row[0] if row else 0)

    def list_channels(self, user_id: int, filter_name: str = "all") -> Iterator[ChannelRecord]:
        query = "SELECT c.* FROM channels c"
        conditions: list[str] = ["c.user_id = ?"]
        params: list[object] = [user_id]

        match filter_name:
            case "blacklisted":
                conditions.append("c.blacklisted = 1")
            case "manual":
                conditions.append("c.blacklisted = 0")
                conditions.append("EXISTS (SELECT 1 FROM channel_trackers ct WHERE ct.user_id = c.user_id AND ct.channel_id = c.channel_id AND ct.tracker_id = 'user')")
            case "playlist":
                conditions.append("c.blacklisted = 0")
                conditions.append("EXISTS (SELECT 1 FROM channel_trackers ct WHERE ct.user_id = c.user_id AND ct.channel_id = c.channel_id AND ct.tracker_id != 'user')")
            case "tracked":
                conditions.append("c.blacklisted = 0")
                conditions.append("EXISTS (SELECT 1 FROM channel_trackers ct WHERE ct.user_id = c.user_id AND ct.channel_id = c.channel_id)")
            case _:
                pass

        query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY c.title COLLATE NOCASE, c.channel_id"

        with self.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        for row in rows:
            yield self._row_to_channel(user_id, row)

    def list_channels_paginated(self, user_id: int, limit: int = 5, offset: int = 0, filter_name: str = "all") -> list[ChannelRecord]:
        query = "SELECT c.* FROM channels c"
        conditions: list[str] = ["c.user_id=?"]
        params: list[object] = [user_id]

        match filter_name:
            case "blacklisted":
                conditions.append("c.blacklisted=1")
            case "manual":
                conditions.append("c.blacklisted=0")
                conditions.append("EXISTS (SELECT 1 FROM channel_trackers ct WHERE ct.user_id = c.user_id AND ct.channel_id = c.channel_id AND ct.tracker_id = 'user')")
            case "playlist":
                conditions.append("c.blacklisted=0")
                conditions.append("EXISTS (SELECT 1 FROM channel_trackers ct WHERE ct.user_id = c.user_id AND ct.channel_id = c.channel_id AND ct.tracker_id != 'user')")
            case "tracked":
                conditions.append("c.blacklisted=0")
                conditions.append("EXISTS (SELECT 1 FROM channel_trackers ct WHERE ct.user_id = c.user_id AND ct.channel_id = c.channel_id)")
            case _:
                pass

        query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY c.title COLLATE NOCASE, c.channel_id LIMIT ? OFFSET ?"

        params.extend([limit, offset])

        with self.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        return [self._row_to_channel(user_id, row) for row in rows]

    def upsert_channel(
        self,
        user_id: int,
        channel_id: str,
        title: str,
        *,
        blacklisted: bool | None = None,
        last_seen_ts: str | None = None,
        trackers: Sequence[str] | None = None,
    ) -> ChannelRecord:
        now = utc_now()
        existing = self.get_channel(user_id, channel_id)

        resolved_blacklisted = existing.blacklisted if existing and blacklisted is None else bool(blacklisted) if blacklisted is not None else False
        resolved_last_seen = last_seen_ts if last_seen_ts is not None else (existing.last_seen_ts if existing else None)

        with self.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO channels (user_id, channel_id, title, blacklisted, last_seen_ts, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, COALESCE(?, ?), ?)
                ON CONFLICT(user_id, channel_id) DO UPDATE SET
                    title=excluded.title,
                    blacklisted=excluded.blacklisted,
                    last_seen_ts=CASE 
                        WHEN excluded.last_seen_ts IS NULL THEN channels.last_seen_ts
                        WHEN channels.last_seen_ts IS NULL THEN excluded.last_seen_ts
                        WHEN excluded.last_seen_ts > channels.last_seen_ts THEN excluded.last_seen_ts
                        ELSE channels.last_seen_ts
                    END,
                    updated_at=excluded.updated_at
                """,
                (
                    user_id, channel_id, title, int(resolved_blacklisted), resolved_last_seen,
                    existing.created_at if existing else now, now, now
                ),
            )

            if trackers:
                incoming_trackers = list(dict.fromkeys([t for t in trackers if t]))
                cursor.executemany(
                    """
                    INSERT OR IGNORE INTO channel_trackers (user_id, channel_id, tracker_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    [(user_id, channel_id, tracker_id, now) for tracker_id in incoming_trackers]
                )

        return self.get_channel(user_id, channel_id) or ChannelRecord(user_id=user_id, channel_id=channel_id, title=title)

    def upsert_playlist_channels(
        self,
        user_id: int,
        playlist_id: str,
        channels: dict[str, str],
        last_seen: dict[str, str] | None = None
    ) -> None:
        now = utc_now()
        is_dict_present = last_seen is not None
        last_seen_dict = last_seen or {}

        with self.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO channels (
                    user_id,
                    channel_id,
                    title,
                    blacklisted,
                    last_seen_ts,
                    created_at,
                    updated_at
                )
                -- FIX: If it's a new row insert and incoming is NULL, default to :now
                VALUES (:user_id, :channel_id, :title, 0, COALESCE(:incoming_last_seen, :now), :now, :now)
                ON CONFLICT(user_id, channel_id) DO UPDATE SET
                    title = excluded.title,
                    created_at = CASE
                        WHEN excluded.title != channels.title
                            THEN max(excluded.created_at, channels.created_at)
                        ELSE channels.created_at
                    END,

                    last_seen_ts = CASE
                        -- RULE 1: last_seen dict is null
                        WHEN :is_dict_present = 0 THEN 
                            COALESCE(channels.last_seen_ts, excluded.updated_at)

                        -- RULE 2: last_seen dict is present
                        ELSE 
                            CASE 
                                WHEN channels.last_seen_ts IS NULL OR excluded.last_seen_ts > channels.last_seen_ts 
                                    THEN excluded.last_seen_ts
                                ELSE channels.last_seen_ts
                            END
                    END,

                    updated_at = excluded.updated_at
                """,
                [
                    {
                        "user_id": user_id,
                        "channel_id": cid,
                        "title": title,
                        "incoming_last_seen": last_seen_dict.get(cid),
                        "is_dict_present": 1 if is_dict_present else 0,
                        "now": now,
                    }
                    for cid, title in channels.items()
                ],
            )

            cursor.executemany(
                """
                INSERT OR IGNORE INTO channel_trackers
                    (user_id, channel_id, tracker_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [(user_id, cid, playlist_id, now) for cid in channels],
            )

    def set_channel_blacklisted(self, user_id: int, channel_id: str, title: str, blacklisted: bool) -> ChannelRecord:
        record = self.upsert_channel(user_id, channel_id, title, blacklisted=blacklisted)
        now = utc_now()
        with self.cursor() as cursor:
            cursor.execute(
                "UPDATE channels SET blacklisted=?, updated_at=? WHERE user_id=? AND channel_id=?",
                (int(blacklisted), now, user_id, channel_id),
            )
        return self.get_channel(user_id, channel_id) or record

    def remove_channel(self, user_id: int, channel_id: str) -> None:
        with self.cursor() as cursor:
            cursor.execute(
                "DELETE FROM channel_trackers WHERE user_id=? AND channel_id=?",
                (user_id, channel_id)
            )
            cursor.execute(
                "DELETE FROM channels WHERE user_id=? AND channel_id=?",
                (user_id, channel_id)
            )

    def add_channel_tracker(self, user_id: int, channel_id: str, tracker: str):
        now = utc_now()

        with self.cursor() as cursor:
            cursor.execute(
                """
                INSERT OR IGNORE INTO channel_trackers (user_id, channel_id, tracker_id, created_at)
                SELECT ?, ?, ?, ?
                WHERE EXISTS (
                    SELECT 1 FROM channels
                    WHERE user_id=? AND channel_id=?
                )
                """,
                (user_id, channel_id, tracker, now, user_id, channel_id),
            )

    def remove_channel_tracker(self, user_id: int, channel_id: str, tracker: str) -> bool:
        with self.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM channel_trackers
                WHERE user_id=? AND channel_id=? AND tracker_id=?
                """,
                (user_id, channel_id, tracker),
            )

            removed = cursor.rowcount > 0

            if not removed:
                return False

            cursor.execute(
                """
                DELETE FROM channels
                WHERE user_id=? AND channel_id=?
                    AND blacklisted=0
                    AND NOT EXISTS (
                        SELECT 1 FROM channel_trackers
                        WHERE user_id=? AND channel_id=?
                    )
                """,
                (user_id, channel_id, user_id, channel_id),
            )

        return True

    def remove_orphaned_playlist_channels(self, user_id: int, playlist_id: str, cutoff: str) -> None:
        with self.cursor() as cursor:
            cursor.execute("""
                CREATE TEMP TABLE tmp_stale AS
                SELECT c.channel_id
                FROM channels c
                JOIN channel_trackers ct
                  ON ct.user_id = c.user_id AND ct.channel_id = c.channel_id
                WHERE c.user_id = ?
                  AND ct.tracker_id = ?
                  AND c.updated_at < ?
            """, (user_id, playlist_id, cutoff))

            cursor.execute("""
                DELETE FROM channel_trackers
                WHERE user_id = ?
                  AND tracker_id = ?
                  AND channel_id IN (SELECT channel_id FROM tmp_stale)
            """, (user_id, playlist_id))

            cursor.execute("""
                DELETE FROM channels
                WHERE user_id = ?
                  AND channel_id IN (
                      SELECT s.channel_id
                      FROM tmp_stale s
                      LEFT JOIN channel_trackers ct
                        ON ct.user_id = ? AND ct.channel_id = s.channel_id
                      WHERE ct.channel_id IS NULL
                  )
                  AND blacklisted = 0
            """, (user_id, user_id))

            cursor.execute("DROP TABLE tmp_stale")

    def reset_channels_last_seen(self, user_id: int) -> int:
        now = utc_now()
        with self.cursor() as cursor:
            cursor.execute(
                "UPDATE channels SET last_seen_ts=NULL, updated_at=? WHERE user_id=?",
                (now, user_id),
            )
            return cursor.rowcount

    @staticmethod
    def _row_to_playlist(row: sqlite3.Row) -> PlaylistRecord:
        return PlaylistRecord(
            user_id=row["user_id"],
            playlist_id=row["playlist_id"],
            title=row["title"],
            is_private=bool(row["is_private"]),
            last_synced_ts=row["last_synced_ts"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_channel(self, user_id: int, row: sqlite3.Row) -> ChannelRecord:
        with self.cursor() as cursor:
            cursor.execute(
                """
                SELECT tracker_id FROM channel_trackers
                WHERE user_id=? AND channel_id=?
                """,
                (user_id, row["channel_id"]),
            )
            trackers = [r[0] for r in cursor.fetchall()]

        return ChannelRecord(
            user_id=user_id,
            channel_id=row["channel_id"],
            title=row["title"],
            blacklisted=bool(row["blacklisted"]),
            last_seen_ts=row["last_seen_ts"],
            trackers=trackers,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def upsert_playlist(self, user_id: int, playlist_id: str, title: str, *, is_private: bool = False) -> PlaylistRecord:
        now = utc_now()
        with self.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO playlists (user_id, playlist_id, title, is_private, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, playlist_id) DO UPDATE SET
                    title=excluded.title,
                    is_private=excluded.is_private,
                    updated_at=excluded.updated_at
                """,
                (user_id, playlist_id, title, int(is_private), now, now),
            )
        return self.get_playlist(user_id, playlist_id)

    def get_playlist(self, user_id: int, playlist_id: str) -> PlaylistRecord | None:
        with self.cursor() as cursor:
            cursor.execute("SELECT * FROM playlists WHERE user_id=? AND playlist_id=?", (user_id, playlist_id))
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_playlist(row)

    def list_playlists(self, user_id: int) -> Iterator[PlaylistRecord]:
        with self.cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM playlists 
                WHERE user_id=? 
                ORDER BY title COLLATE NOCASE, playlist_id
                """,
                (user_id,),
            )

            while True:
                rows = cursor.fetchmany(10)
                if not rows:
                    break

                for row in rows:
                    if row:
                        yield self._row_to_playlist(row)

    def count_playlists(self, user_id: int) -> int:
        with self.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM playlists WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
        return int(row[0] if row else 0)

    def list_playlists_paginated(self, user_id: int, limit: int = 10, offset: int = 0) -> list[PlaylistRecord]:
        with self.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM playlists WHERE user_id=? ORDER BY title COLLATE NOCASE, playlist_id LIMIT ? OFFSET ?",
                (user_id, limit, offset),
            )
            rows = cursor.fetchall()
        return [self.get_playlist(user_id, row["playlist_id"]) for row in rows if row]

    def set_playlist_synced(self, user_id: int, playlist_id: str, ts: str | None) -> None:
         now = utc_now()
         with self.cursor() as cursor:
             cursor.execute(
                 "UPDATE playlists SET last_synced_ts=?, updated_at=? WHERE user_id=? AND playlist_id=?",
                 (ts, now, user_id, playlist_id),
             )

    def remove_playlist(self, user_id: int, playlist_id: str) -> int:
        with self.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM channel_trackers
                WHERE user_id = ? AND tracker_id = ?
                """,
                (user_id, playlist_id),
            )
            removed_count = cursor.rowcount

            cursor.execute(
                """
                DELETE FROM playlists
                WHERE user_id = ? AND playlist_id = ?
                """,
                (user_id, playlist_id),
            )

            cursor.execute(
                """
                DELETE FROM channels
                WHERE user_id = ? 
                    AND blacklisted = 0
                    AND channel_id NOT IN (
                        SELECT channel_id 
                        FROM channel_trackers 
                        WHERE user_id = ?
                    )
                """,
                (user_id, user_id),
            )

        return removed_count

    def list_users(self) -> list[UserSettings]:
        with self.cursor() as cursor:
            cursor.execute("SELECT * FROM users ORDER BY user_id")
            rows = cursor.fetchall()
        return [
            UserSettings(
                user_id=row["user_id"],
                guild_id=row["guild_id"],
                notify_channel_id=row["notify_channel_id"],
                notify_dms=bool(row["notify_dms"]),
                catchup_enabled=bool(row["catchup_enabled"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def count_users(self) -> int:
        with self.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM users")
            row = cursor.fetchone()
        return int(row[0] if row else 0)

    def set_auth_record(self, user_id: int, credential_json: str, email: str | None = None) -> None:
        now = utc_now()
        with self.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO auth_records (user_id, credential_json, email, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    credential_json=excluded.credential_json,
                    email=excluded.email,
                    updated_at=excluded.updated_at
                """,
                (user_id, credential_json, email, now, now),
            )

    def get_auth_record(self, user_id: int) -> AuthRecord | None:
        with self.cursor() as cursor:
            cursor.execute("SELECT * FROM auth_records WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return AuthRecord(
            user_id=row["user_id"],
            credential_json=row["credential_json"],
            email=row["email"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def remove_auth_record(self, user_id: int) -> None:
        with self.cursor() as cursor:
            cursor.execute("DELETE FROM auth_records WHERE user_id=?", (user_id,))


    def stats_snapshot(self) -> StatsSnapshot:
        with self.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM users")
            users = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM channels")
            channels = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM playlists")
            playlists = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM channels WHERE blacklisted=1")
            blacklisted = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(DISTINCT channel_id) FROM channel_trackers WHERE tracker_id='user'")
            pinned = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM users WHERE notify_dms=1")
            dms = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM users WHERE notify_channel_id IS NOT NULL")
            channel_notifs = int(cursor.fetchone()[0])
            cursor.execute("""
                SELECT COUNT(*) FROM channels c 
                WHERE c.blacklisted=0 
                  AND EXISTS (SELECT 1 FROM channel_trackers ct WHERE ct.user_id = c.user_id AND ct.channel_id = c.channel_id AND ct.tracker_id = 'user')
            """)
            manual_channels = int(cursor.fetchone()[0])
            cursor.execute("""
                SELECT COUNT(*) FROM channels c 
                WHERE c.blacklisted=0 
                  AND EXISTS (SELECT 1 FROM channel_trackers ct WHERE ct.user_id = c.user_id AND ct.channel_id = c.channel_id AND ct.tracker_id != 'user')
            """)
            playlist_channels = int(cursor.fetchone()[0])
        avg_channels = channels / users if users else 0.0
        avg_playlists = playlists / users if users else 0.0
        return StatsSnapshot(
            users=users,
            channels=channels,
            playlists=playlists,
            manual_channels=manual_channels,
            playlist_channels=playlist_channels,
            blacklisted_channels=blacklisted,
            avg_channels_per_user=avg_channels,
            avg_playlists_per_user=avg_playlists,
            pinned_channels=pinned,
            dm_notifications=dms,
            channel_notifications=channel_notifs
        )

    def count_user_objects(self, user_id: int) -> tuple[int, int]:
        with self.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM channels WHERE user_id=?", (user_id,))
            channels = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM playlists WHERE user_id=?", (user_id,))
            playlists = int(cursor.fetchone()[0])
        return channels, playlists