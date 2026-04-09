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

from dataclasses import dataclass, field

from models import TemplateData, RoleData, ChannelData, PermissionOverwriteData

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


# ── Merge mode ────────────────────────────────────────────────


@dataclass
class MergePreview:
    """What a merge operation will do, computed before execution."""

    roles_create: list[str] = field(default_factory=list)
    roles_edit: list[str] = field(default_factory=list)
    roles_delete: list[str] = field(default_factory=list)
    categories_create: list[str] = field(default_factory=list)
    categories_edit: list[str] = field(default_factory=list)
    categories_delete: list[str] = field(default_factory=list)
    channels_create: list[str] = field(default_factory=list)
    channels_edit: list[str] = field(default_factory=list)
    channels_delete: list[str] = field(default_factory=list)
    channels_protected: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.roles_create or self.roles_edit or self.roles_delete
            or self.categories_create or self.categories_edit or self.categories_delete
            or self.channels_create or self.channels_edit or self.channels_delete
            or self.channels_protected
        )


def _role_differs(role: discord.Role, data: RoleData) -> bool:
    """Check whether an existing role's properties differ from template data."""
    return (
        role.color.value != data.color
        or role.permissions.value != data.permissions
        or role.hoist != data.hoist
        or role.mentionable != data.mentionable
    )


def _build_channel_map(
    guild: discord.Guild,
) -> dict[tuple[Optional[str], str], discord.abc.GuildChannel]:
    """Map (category_name | None, channel_name) → channel for non-category channels."""
    result: dict[tuple[Optional[str], str], discord.abc.GuildChannel] = {}
    for ch in guild.channels:
        if isinstance(ch, discord.CategoryChannel):
            continue
        cat_name = ch.category.name if ch.category else None
        result[(cat_name, ch.name)] = ch
    return result


def _is_protected(name: str, obj_id: Optional[int], protected: set) -> bool:
    """Check whether an item is protected by name or by channel/category ID."""
    return name in protected or (obj_id is not None and obj_id in protected)


def compute_merge_preview(
    guild: discord.Guild,
    template: TemplateData,
    protected: set[str | int],
    delete_extras: bool,
) -> MergePreview:
    """Diff the guild against a template and return what a merge would do."""
    preview = MergePreview()
    bot_member = guild.me

    # ── Roles ──
    existing_roles = {
        r.name: r for r in guild.roles
        if not r.managed and r != guild.default_role
    }
    template_role_names = {r.name for r in template.roles}

    for rd in template.roles:
        if rd.name in existing_roles:
            if _role_differs(existing_roles[rd.name], rd):
                preview.roles_edit.append(rd.name)
        else:
            preview.roles_create.append(rd.name)

    if delete_extras:
        for name, role in existing_roles.items():
            if name not in template_role_names:
                if bot_member and role >= bot_member.top_role:
                    continue
                preview.roles_delete.append(name)

    # ── Categories ──
    existing_cats = {c.name: c for c in guild.categories}
    template_cat_names = {c.name for c in template.categories}

    for cd in template.categories:
        if cd.name in existing_cats:
            preview.categories_edit.append(cd.name)
        else:
            preview.categories_create.append(cd.name)

    if delete_extras:
        for name in existing_cats:
            if name not in template_cat_names and not _is_protected(name, existing_cats[name].id, protected):
                preview.categories_delete.append(name)

    # ── Channels ──
    existing_channels = _build_channel_map(guild)
    accounted: set[tuple[Optional[str], str]] = set()

    # Categorized channels
    for cd in template.categories:
        for chd in cd.channels:
            key = (cd.name, chd.name)
            existing_ch = existing_channels.get(key)
            if _is_protected(chd.name, existing_ch.id if existing_ch else None, protected):
                if existing_ch is not None:
                    preview.channels_protected.append(f"{cd.name}/{chd.name}")
                accounted.add(key)
            elif existing_ch is not None:
                preview.channels_edit.append(f"{cd.name}/{chd.name}")
                accounted.add(key)
            else:
                preview.channels_create.append(f"{cd.name}/{chd.name}")

    # Uncategorized channels
    for chd in template.channels:
        key = (None, chd.name)
        existing_ch = existing_channels.get(key)
        if _is_protected(chd.name, existing_ch.id if existing_ch else None, protected):
            if existing_ch is not None:
                preview.channels_protected.append(chd.name)
            accounted.add(key)
        elif existing_ch is not None:
            preview.channels_edit.append(chd.name)
            accounted.add(key)
        else:
            preview.channels_create.append(chd.name)

    if delete_extras:
        for key, ch in existing_channels.items():
            if key not in accounted and not _is_protected(ch.name, ch.id, protected):
                cat_name, ch_name = key
                label = f"{cat_name}/{ch_name}" if cat_name else ch_name
                preview.channels_delete.append(label)

    return preview


async def _edit_channel(
    channel: discord.abc.GuildChannel,
    ch_data: ChannelData,
    category: Optional[discord.CategoryChannel],
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite],
) -> None:
    """Edit an existing channel's settings and overwrites to match template data."""
    kwargs: dict = {"overwrites": overwrites, "reason": "Template merge: syncing channel"}
    if category is not None:
        kwargs["category"] = category
    if hasattr(channel, "topic"):
        kwargs["topic"] = ch_data.topic
    if hasattr(channel, "slowmode_delay"):
        kwargs["slowmode_delay"] = ch_data.slowmode
    if hasattr(channel, "nsfw"):
        kwargs["nsfw"] = ch_data.nsfw
    if hasattr(channel, "bitrate") and ch_data.bitrate:
        kwargs["bitrate"] = ch_data.bitrate
    if hasattr(channel, "user_limit") and ch_data.user_limit is not None:
        kwargs["user_limit"] = ch_data.user_limit
    await channel.edit(**kwargs)


async def merge_template(
    guild: discord.Guild,
    template: TemplateData,
    protected: set[str | int],
    delete_extras: bool,
    progress: Callable[[str], Awaitable[None]],
) -> dict:
    """
    Smart-merge a template onto a guild.

    Creates missing items, edits changed items, optionally deletes extras.
    Protected channels keep their content but get permission overwrites synced.

    Execution order:
        1. Sync roles (create missing, edit changed)
        2. Reorder roles to match template hierarchy
        3. Delete extra roles (if delete_extras)
        4. Sync categories (create / edit with overwrites)
        5. Sync channels (create / edit with overwrites, protected = perms only)
        6. Apply overwrite sync on protected channels
        7. Apply server settings (name, icon)
    """
    stats = {
        "roles_created": 0,
        "roles_edited": 0,
        "roles_deleted": 0,
        "roles_failed": 0,
        "channels_created": 0,
        "channels_edited": 0,
        "channels_deleted": 0,
        "channels_protected": 0,
        "channels_failed": 0,
        "categories_created": 0,
        "categories_edited": 0,
        "categories_deleted": 0,
        "categories_failed": 0,
    }

    bot_member = guild.me

    # ── Phase 1: Sync roles ───────────────────────────────────────
    await progress("**Phase 1/6** — Syncing roles...")

    existing_roles = {
        r.name: r for r in guild.roles
        if not r.managed and r != guild.default_role
    }
    template_role_names = {r.name for r in template.roles}
    role_map: dict[str, discord.Role] = {"everyone": guild.default_role}

    for rd in sorted(template.roles, key=lambda r: r.position):
        if rd.name in existing_roles:
            role = existing_roles[rd.name]
            role_map[rd.name] = role
            if _role_differs(role, rd):
                try:
                    await role.edit(
                        color=discord.Color(rd.color),
                        permissions=discord.Permissions(rd.permissions),
                        hoist=rd.hoist,
                        mentionable=rd.mentionable,
                        reason="Template merge: syncing role",
                    )
                    stats["roles_edited"] += 1
                except Exception:
                    stats["roles_failed"] += 1
                await asyncio.sleep(ROLE_DELAY)
        else:
            try:
                new_role = await guild.create_role(
                    name=rd.name,
                    color=discord.Color(rd.color),
                    permissions=discord.Permissions(rd.permissions),
                    hoist=rd.hoist,
                    mentionable=rd.mentionable,
                    reason="Template merge: creating role",
                )
                role_map[rd.name] = new_role
                stats["roles_created"] += 1
            except Exception:
                stats["roles_failed"] += 1
            await asyncio.sleep(ROLE_DELAY)

    # Include non-template roles in role_map so overwrites can reference them
    for name, role in existing_roles.items():
        if name not in role_map:
            role_map[name] = role

    # ── Phase 2: Reorder roles ────────────────────────────────────
    await progress("**Phase 2/6** — Reordering roles...")

    positions: dict[discord.Role, int] = {}
    for rd in template.roles:
        if rd.name in role_map and role_map[rd.name] != guild.default_role:
            positions[role_map[rd.name]] = rd.position
    # Push non-template roles to the bottom
    for role in guild.roles:
        if role == guild.default_role or role.managed:
            continue
        if bot_member and role >= bot_member.top_role:
            continue
        if role.name not in template_role_names:
            positions[role] = 1
    if positions:
        try:
            await guild.edit_role_positions(positions=positions)
        except Exception:
            pass

    # ── Phase 3: Delete extra roles ───────────────────────────────
    if delete_extras:
        await progress("**Phase 3/6** — Deleting extra roles...")
        for name, role in existing_roles.items():
            if name not in template_role_names:
                if bot_member and role >= bot_member.top_role:
                    continue
                try:
                    await role.delete(reason="Template merge: removing extra role")
                    stats["roles_deleted"] += 1
                except Exception:
                    pass
                await asyncio.sleep(ROLE_DELAY)

    # ── Phase 4: Sync categories ──────────────────────────────────
    await progress("**Phase 4/6** — Syncing categories...")

    existing_cats = {c.name: c for c in guild.categories}
    template_cat_names = {c.name for c in template.categories}
    cat_map: dict[str, discord.CategoryChannel] = {}

    for cd in sorted(template.categories, key=lambda c: c.position):
        overwrites = _resolve_overwrites(cd.overwrites, role_map, guild)
        if cd.name in existing_cats:
            cat = existing_cats[cd.name]
            cat_map[cd.name] = cat
            try:
                await cat.edit(
                    position=cd.position,
                    overwrites=overwrites,
                    reason="Template merge: syncing category",
                )
                stats["categories_edited"] += 1
            except Exception:
                stats["categories_failed"] += 1
        else:
            try:
                cat = await guild.create_category(
                    name=cd.name,
                    position=cd.position,
                    overwrites=overwrites,
                    reason="Template merge: creating category",
                )
                cat_map[cd.name] = cat
                stats["categories_created"] += 1
            except Exception:
                stats["categories_failed"] += 1
        await asyncio.sleep(CHANNEL_DELAY)

    # Delete extra categories
    if delete_extras:
        for name, cat in existing_cats.items():
            if name not in template_cat_names and not _is_protected(name, cat.id, protected):
                try:
                    await cat.delete(reason="Template merge: removing extra category")
                    stats["categories_deleted"] += 1
                except Exception:
                    pass
                await asyncio.sleep(CHANNEL_DELAY)

    # ── Phase 5: Sync channels ────────────────────────────────────
    await progress("**Phase 5/6** — Syncing channels...")

    existing_channels = _build_channel_map(guild)
    accounted: set[tuple[Optional[str], str]] = set()
    protected_overwrite_queue: list[
        tuple[discord.abc.GuildChannel, dict]
    ] = []

    # Categorized template channels
    for cd in template.categories:
        category = cat_map.get(cd.name)
        for chd in sorted(cd.channels, key=lambda c: c.position):
            key = (cd.name, chd.name)
            overwrites = _resolve_overwrites(chd.overwrites, role_map, guild)
            existing_ch = existing_channels.get(key)

            if _is_protected(chd.name, existing_ch.id if existing_ch else None, protected):
                if existing_ch is not None:
                    protected_overwrite_queue.append(
                        (existing_ch, overwrites)
                    )
                    stats["channels_protected"] += 1
                accounted.add(key)
            elif key in existing_channels:
                try:
                    await _edit_channel(
                        existing_channels[key], chd, category, overwrites
                    )
                    stats["channels_edited"] += 1
                except Exception:
                    stats["channels_failed"] += 1
                accounted.add(key)
                await asyncio.sleep(CHANNEL_DELAY)
            else:
                try:
                    await _create_channel(guild, chd, role_map, category)
                    stats["channels_created"] += 1
                except Exception:
                    stats["channels_failed"] += 1
                await asyncio.sleep(CHANNEL_DELAY)

    # Uncategorized template channels
    for chd in sorted(template.channels, key=lambda c: c.position):
        key = (None, chd.name)
        overwrites = _resolve_overwrites(chd.overwrites, role_map, guild)
        existing_ch = existing_channels.get(key)

        if _is_protected(chd.name, existing_ch.id if existing_ch else None, protected):
            if existing_ch is not None:
                protected_overwrite_queue.append(
                    (existing_ch, overwrites)
                )
                stats["channels_protected"] += 1
            accounted.add(key)
        elif key in existing_channels:
            try:
                await _edit_channel(
                    existing_channels[key], chd, None, overwrites
                )
                stats["channels_edited"] += 1
            except Exception:
                stats["channels_failed"] += 1
            accounted.add(key)
            await asyncio.sleep(CHANNEL_DELAY)
        else:
            try:
                await _create_channel(guild, chd, role_map, category=None)
                stats["channels_created"] += 1
            except Exception:
                stats["channels_failed"] += 1
            await asyncio.sleep(CHANNEL_DELAY)

    # Delete extra channels
    if delete_extras:
        for key, ch in existing_channels.items():
            if key not in accounted and not _is_protected(ch.name, ch.id, protected):
                try:
                    await ch.delete(reason="Template merge: removing extra channel")
                    stats["channels_deleted"] += 1
                except Exception:
                    pass
                await asyncio.sleep(CHANNEL_DELAY)

    # ── Phase 6: Sync permissions on protected channels ───────────
    if protected_overwrite_queue:
        await progress("**Phase 6/6** — Syncing protected channel permissions...")
        for ch, overwrites in protected_overwrite_queue:
            try:
                await ch.edit(
                    overwrites=overwrites,
                    reason="Template merge: syncing protected channel permissions",
                )
            except Exception:
                pass
            await asyncio.sleep(CHANNEL_DELAY)

    # ── Apply server settings ─────────────────────────────────────
    await progress("Applying server settings...")
    try:
        kwargs: dict = {"name": template.guild_name}
        if template.icon:
            try:
                icon_bytes = base64.b64decode(template.icon)
                kwargs["icon"] = icon_bytes
            except Exception:
                pass
        await guild.edit(**kwargs, reason="Template merge: applying settings")
    except Exception:
        pass

    return stats
