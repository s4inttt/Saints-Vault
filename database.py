"""
Async SQLite database layer for template and backup storage.

Uses aiosqlite for non-blocking database operations.
Templates and backups are stored in separate tables with
the same schema. Template data is stored as a JSON text blob.
"""

from __future__ import annotations

import aiosqlite
from datetime import datetime
from typing import Optional

DB_PATH = "templates.db"


async def _get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db() -> None:
    """Create tables if they don't exist."""
    db = await _get_db()
    try:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                guild_name TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, name)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                guild_name TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, name)
            )
        """)
        await db.commit()
    finally:
        await db.close()


# ── Template CRUD ──────────────────────────────────────────────


async def save_template(
    user_id: int, name: str, guild_name: str, data: str
) -> bool:
    """Save or update a template. Returns True if updated, False if created."""
    db = await _get_db()
    try:
        now = datetime.utcnow().isoformat()
        existing = await db.execute_fetchall(
            "SELECT id FROM templates WHERE user_id = ? AND name = ?",
            (user_id, name),
        )
        if existing:
            await db.execute(
                "UPDATE templates SET guild_name = ?, data = ?, updated_at = ? "
                "WHERE user_id = ? AND name = ?",
                (guild_name, data, now, user_id, name),
            )
            await db.commit()
            return True
        else:
            await db.execute(
                "INSERT INTO templates (user_id, name, guild_name, data, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, name, guild_name, data, now, now),
            )
            await db.commit()
            return False
    finally:
        await db.close()


async def get_template(user_id: int, name: str) -> Optional[dict]:
    """Get a single template by user and name."""
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM templates WHERE user_id = ? AND name = ?",
            (user_id, name),
        )
        if rows:
            return dict(rows[0])
        return None
    finally:
        await db.close()


async def list_templates(user_id: int) -> list[dict]:
    """List all templates for a user."""
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name, guild_name, created_at, updated_at "
            "FROM templates WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def delete_template(user_id: int, name: str) -> bool:
    """Delete a template. Returns True if deleted."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM templates WHERE user_id = ? AND name = ?",
            (user_id, name),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_template_names(user_id: int) -> list[str]:
    """Get all template names for autocomplete."""
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT name FROM templates WHERE user_id = ? ORDER BY name",
            (user_id,),
        )
        return [r["name"] for r in rows]
    finally:
        await db.close()


# ── Backup CRUD ────────────────────────────────────────────────


async def save_backup(
    user_id: int, guild_id: int, name: str, guild_name: str, data: str
) -> None:
    """Save a new backup."""
    db = await _get_db()
    try:
        now = datetime.utcnow().isoformat()
        await db.execute(
            "INSERT OR REPLACE INTO backups (user_id, guild_id, name, guild_name, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, guild_id, name, guild_name, data, now),
        )
        await db.commit()
    finally:
        await db.close()


async def get_backup(user_id: int, name: str) -> Optional[dict]:
    """Get a single backup by user and name."""
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM backups WHERE user_id = ? AND name = ?",
            (user_id, name),
        )
        if rows:
            return dict(rows[0])
        return None
    finally:
        await db.close()


async def list_backups(user_id: int) -> list[dict]:
    """List all backups for a user."""
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT id, name, guild_name, guild_id, created_at "
            "FROM backups WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def delete_backup(user_id: int, name: str) -> bool:
    """Delete a backup. Returns True if deleted."""
    db = await _get_db()
    try:
        cursor = await db.execute(
            "DELETE FROM backups WHERE user_id = ? AND name = ?",
            (user_id, name),
        )
        await db.commit()
        return cursor.rowcount > 0
    finally:
        await db.close()


async def get_backup_names(user_id: int) -> list[str]:
    """Get all backup names for autocomplete."""
    db = await _get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT name FROM backups WHERE user_id = ? ORDER BY name",
            (user_id,),
        )
        return [r["name"] for r in rows]
    finally:
        await db.close()
