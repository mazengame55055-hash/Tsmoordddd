"""Tiny SQLite layer. Blocking sqlite3 calls run in a thread so the event loop never stalls."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    discord_id      INTEGER PRIMARY KEY,
    kaggle_username TEXT    NOT NULL,
    token_enc       BLOB    NOT NULL,
    token_kind      TEXT    NOT NULL,          -- 'token' (KGAT_...) or 'legacy' (username + key)
    created_at      INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id    INTEGER NOT NULL,
    kind          TEXT    NOT NULL,            -- 'blend' or 'audio'
    original_name TEXT    NOT NULL,
    dataset_slug  TEXT    NOT NULL,
    size_bytes    INTEGER NOT NULL,
    created_at    INTEGER NOT NULL,
    stored_name   TEXT                         -- ASCII name inside the Kaggle dataset (NULL on old rows = original_name)
);
CREATE INDEX IF NOT EXISTS idx_files_user ON files(discord_id);
CREATE TABLE IF NOT EXISTS renders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    discord_id  INTEGER NOT NULL,
    channel_id  INTEGER,
    kernel_slug TEXT    NOT NULL,
    mode        TEXT    NOT NULL,
    blend_name  TEXT    NOT NULL,
    config_json TEXT    NOT NULL,
    status      TEXT    NOT NULL,              -- queued | running | complete | error | cancelled | timeout
    created_at  INTEGER NOT NULL,
    finished_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_renders_user ON renders(discord_id);
"""


class Database:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(files)")}
            if "stored_name" not in cols:  # database created by v1.0.0-1.0.3
                self._conn.execute("ALTER TABLE files ADD COLUMN stored_name TEXT")
            self._conn.commit()

    def _run(self, sql: str, params: tuple = (), fetch: Optional[str] = None) -> Any:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return cur.lastrowid

    async def _exec(self, sql: str, params: tuple = (), fetch: Optional[str] = None) -> Any:
        return await asyncio.to_thread(self._run, sql, params, fetch)

    # ---- users ----
    async def save_user(self, discord_id: int, username: str, token_enc: bytes, kind: str) -> None:
        await self._exec(
            "INSERT INTO users(discord_id, kaggle_username, token_enc, token_kind, created_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(discord_id) DO UPDATE SET "
            "kaggle_username=excluded.kaggle_username, token_enc=excluded.token_enc, "
            "token_kind=excluded.token_kind, created_at=excluded.created_at",
            (discord_id, username, token_enc, kind, int(time.time())),
        )

    async def get_user(self, discord_id: int) -> Optional[sqlite3.Row]:
        return await self._exec("SELECT * FROM users WHERE discord_id=?", (discord_id,), "one")

    async def delete_user(self, discord_id: int) -> None:
        await self._exec("DELETE FROM files WHERE discord_id=?", (discord_id,))
        await self._exec("DELETE FROM renders WHERE discord_id=?", (discord_id,))
        await self._exec("DELETE FROM users WHERE discord_id=?", (discord_id,))

    # ---- files ----
    async def add_file(self, discord_id: int, kind: str, name: str, slug: str, size: int,
                       stored_name: Optional[str] = None) -> int:
        return await self._exec(
            "INSERT INTO files(discord_id, kind, original_name, dataset_slug, size_bytes, created_at, stored_name) "
            "VALUES (?,?,?,?,?,?,?)",
            (discord_id, kind, name, slug, size, int(time.time()), stored_name or name),
        )

    async def list_files(self, discord_id: int, limit: int = 25) -> list[sqlite3.Row]:
        return await self._exec(
            "SELECT * FROM files WHERE discord_id=? ORDER BY id DESC LIMIT ?",
            (discord_id, limit),
            "all",
        )

    async def get_file(self, discord_id: int, file_id: int) -> Optional[sqlite3.Row]:
        return await self._exec("SELECT * FROM files WHERE id=? AND discord_id=?", (file_id, discord_id), "one")

    async def delete_file(self, discord_id: int, file_id: int) -> None:
        await self._exec("DELETE FROM files WHERE id=? AND discord_id=?", (file_id, discord_id))

    # ---- renders ----
    async def add_render(self, discord_id: int, channel_id: Optional[int], slug: str, mode: str,
                         blend_name: str, config_json: str, status: str = "queued") -> int:
        return await self._exec(
            "INSERT INTO renders(discord_id, channel_id, kernel_slug, mode, blend_name, config_json, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (discord_id, channel_id, slug, mode, blend_name, config_json, status, int(time.time())),
        )

    async def fail_preparing(self) -> None:
        """A crash while a render was being sent to Kaggle leaves 'preparing' rows nobody will finish."""
        await self._exec("UPDATE renders SET status='error', finished_at=? WHERE status='preparing'",
                         (int(time.time()),))

    async def monitored_renders(self) -> list[sqlite3.Row]:
        return await self._exec("SELECT * FROM renders WHERE status IN ('queued','running') ORDER BY id", (), "all")

    async def set_render_status(self, render_id: int, status: str, finished: bool = False) -> None:
        await self._exec("UPDATE renders SET status=?, finished_at=? WHERE id=?",
                         (status, int(time.time()) if finished else None, render_id))

    async def active_renders(self, discord_id: Optional[int] = None) -> list[sqlite3.Row]:
        if discord_id is None:
            return await self._exec("SELECT * FROM renders WHERE status IN ('preparing','queued','running') ORDER BY id", (), "all")
        return await self._exec("SELECT * FROM renders WHERE discord_id=? AND status IN ('preparing','queued','running')",
                                (discord_id,), "all")

    async def list_renders(self, discord_id: int, limit: int = 8) -> list[sqlite3.Row]:
        return await self._exec("SELECT * FROM renders WHERE discord_id=? ORDER BY id DESC LIMIT ?",
                                (discord_id, limit), "all")
