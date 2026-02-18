"""
Template loader - recreates server structure from a template.

This is a DESTRUCTIVE operation that:
1. Deletes all existing channels (except the interaction channel)
2. Deletes all removable roles
3. Recreates roles from the template
4. Recreates categories and channels with permission overwrites
5. Applies server settings (name, icon)

Rate limit mitigation is built in via sleep delays between API calls.
Per-item try/except ensures partial failures don't stop the entire load.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Optional, Callable, Awaitable

import discord

from models import TemplateData, ChannelData, PermissionOverwriteData

# Delays between API calls to avoid rate limits
ROLE_DELAY = 0.5
CHANNEL_DELAY = 0.3


async def load_template(
    guild: discord.Guild,
    template: TemplateData,
    keep_channel: Optional[discord.TextChannel],
    progress: Callable[[str], Awaitable[None]],
) -> dict:
    """
    Load a template onto a guild, replacing all existing structure.

    Args:
        guild: The target guild to rebuild.
        template: The template data to apply.
        keep_channel: Channel to keep alive for progress updates (deleted at end).
        progress: Async callback for status updates.

    Returns:
        Dict with counts of created/failed items.
    """
    stats = {
        "roles_deleted": 0,
        "roles_created": 0,
        "roles_failed": 0,
        "channels_deleted": 0,
        "channels_created": 0,
        "channels_failed": 0,
        "categories_created": 0,
        "categories_failed": 0,
    }

    # ── Phase 1: Delete existing channels ──────────────────────
    await progress("**Phase 1/5** — Deleting existing channels...")

    for channel in list(guild.channels):
        if keep_channel and channel.id == keep_channel.id:
            continue
        try:
            await channel.delete(reason="Template load: clearing server")
            stats["channels_deleted"] += 1
            await asyncio.sleep(CHANNEL_DELAY)
        except Exception:
            pass

    # ── Phase 2: Delete existing roles ─────────────────────────
    await progress("**Phase 2/5** — Deleting existing roles...")

    bot_member = guild.me
    for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
        if role == guild.default_role:
            continue
        if role.managed:
            continue
        if bot_member and role >= bot_member.top_role:
            continue
        try:
            await role.delete(reason="Template load: clearing server")
            stats["roles_deleted"] += 1
            await asyncio.sleep(ROLE_DELAY)
        except Exception:
            pass

    # ── Phase 3: Create roles ──────────────────────────────────
    await progress("**Phase 3/5** — Creating roles...")

    role_map: dict[str, discord.Role] = {"everyone": guild.default_role}

    # Create roles from bottom to top (lowest position first)
    for role_data in sorted(template.roles, key=lambda r: r.position):
        try:
            new_role = await guild.create_role(
                name=role_data.name,
                color=discord.Color(role_data.color),
                permissions=discord.Permissions(role_data.permissions),
                hoist=role_data.hoist,
                mentionable=role_data.mentionable,
                reason="Template load: creating role",
            )
            role_map[role_data.name] = new_role
            stats["roles_created"] += 1
            await asyncio.sleep(ROLE_DELAY)
        except Exception:
            stats["roles_failed"] += 1

    # Batch reposition roles
    if role_map:
        positions = {}
        for role_data in template.roles:
            if role_data.name in role_map and role_map[role_data.name] != guild.default_role:
                positions[role_map[role_data.name]] = role_data.position
        if positions:
            try:
                await guild.edit_role_positions(positions=positions)
            except Exception:
                pass

    # ── Phase 4: Create categories + channels ──────────────────
    await progress("**Phase 4/5** — Creating channels...")

    # Create categories first
    for cat_data in sorted(template.categories, key=lambda c: c.position):
        try:
            overwrites = _resolve_overwrites(cat_data.overwrites, role_map, guild)
            category = await guild.create_category(
                name=cat_data.name,
                position=cat_data.position,
                overwrites=overwrites,
                reason="Template load: creating category",
            )
            stats["categories_created"] += 1
            await asyncio.sleep(CHANNEL_DELAY)

            # Create channels inside this category
            for ch_data in sorted(cat_data.channels, key=lambda c: c.position):
                try:
                    await _create_channel(guild, ch_data, role_map, category)
                    stats["channels_created"] += 1
                    await asyncio.sleep(CHANNEL_DELAY)
                except Exception:
                    stats["channels_failed"] += 1

        except Exception:
            stats["categories_failed"] += 1

    # Create uncategorized channels
    for ch_data in sorted(template.channels, key=lambda c: c.position):
        try:
            await _create_channel(guild, ch_data, role_map, category=None)
            stats["channels_created"] += 1
            await asyncio.sleep(CHANNEL_DELAY)
        except Exception:
            stats["channels_failed"] += 1

    # ── Phase 5: Apply server settings ─────────────────────────
    await progress("**Phase 5/5** — Applying server settings...")

    try:
        kwargs: dict = {"name": template.guild_name}
        if template.icon:
            try:
                icon_bytes = base64.b64decode(template.icon)
                kwargs["icon"] = icon_bytes
            except Exception:
                pass
        await guild.edit(**kwargs, reason="Template load: applying settings")
    except Exception:
        pass

    return stats


def _resolve_overwrites(
    overwrite_data: list[PermissionOverwriteData],
    role_map: dict[str, discord.Role],
    guild: discord.Guild,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    """Resolve serialized permission overwrites to discord objects."""
    overwrites = {}
    for ow in overwrite_data:
        if ow.target_type == "role":
            target = role_map.get(ow.target_name)
            if target is None:
                continue
        elif ow.target_type == "member":
            try:
                target = guild.get_member(int(ow.target_name))
                if target is None:
                    continue
            except (ValueError, TypeError):
                continue
        else:
            continue

        overwrites[target] = discord.PermissionOverwrite.from_pair(
            discord.Permissions(ow.allow),
            discord.Permissions(ow.deny),
        )
    return overwrites


async def _create_channel(
    guild: discord.Guild,
    ch_data: ChannelData,
    role_map: dict[str, discord.Role],
    category: Optional[discord.CategoryChannel],
) -> discord.abc.GuildChannel:
    """Create a single channel from serialized data."""
    overwrites = _resolve_overwrites(ch_data.overwrites, role_map, guild)

    if ch_data.type == "text":
        return await guild.create_text_channel(
            name=ch_data.name,
            category=category,
            topic=ch_data.topic,
            slowmode_delay=ch_data.slowmode,
            nsfw=ch_data.nsfw,
            overwrites=overwrites,
            reason="Template load: creating channel",
        )
    elif ch_data.type == "voice":
        kwargs = {
            "name": ch_data.name,
            "category": category,
            "overwrites": overwrites,
            "reason": "Template load: creating channel",
        }
        if ch_data.bitrate:
            kwargs["bitrate"] = ch_data.bitrate
        if ch_data.user_limit is not None:
            kwargs["user_limit"] = ch_data.user_limit
        return await guild.create_voice_channel(**kwargs)
    elif ch_data.type == "stage":
        return await guild.create_stage_channel(
            name=ch_data.name,
            category=category,
            overwrites=overwrites,
            reason="Template load: creating channel",
        )
    elif ch_data.type == "forum":
        return await guild.create_forum(
            name=ch_data.name,
            category=category,
            topic=ch_data.topic,
            slowmode_delay=ch_data.slowmode,
            nsfw=ch_data.nsfw,
            overwrites=overwrites,
            reason="Template load: creating channel",
        )
    elif ch_data.type == "announcement":
        return await guild.create_text_channel(
            name=ch_data.name,
            category=category,
            topic=ch_data.topic,
            slowmode_delay=ch_data.slowmode,
            nsfw=ch_data.nsfw,
            overwrites=overwrites,
            news=True,
            reason="Template load: creating channel",
        )
    else:
        return await guild.create_text_channel(
            name=ch_data.name,
            category=category,
            overwrites=overwrites,
            reason="Template load: creating channel",
        )
