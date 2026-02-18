"""
Server structure serializer.

Captures a guild's roles, categories, channels, and permission
overwrites into a TemplateData object for JSON storage.

Key design decisions:
- Permission overwrites reference roles by name (not ID)
- @everyone is stored as "everyone"
- Bot-managed roles and integration roles are skipped
- The bot's own role is skipped
"""

from __future__ import annotations

import base64
from typing import Optional

import discord

from models import (
    TemplateData,
    RoleData,
    CategoryData,
    ChannelData,
    PermissionOverwriteData,
)

# Channel type mapping
CHANNEL_TYPE_MAP = {
    discord.ChannelType.text: "text",
    discord.ChannelType.voice: "voice",
    discord.ChannelType.stage_voice: "stage",
    discord.ChannelType.forum: "forum",
    discord.ChannelType.news: "announcement",
}


def _serialize_overwrites(
    overwrites: dict[
        discord.Role | discord.Member, discord.PermissionOverwrite
    ],
    guild: discord.Guild,
) -> list[PermissionOverwriteData]:
    """Convert channel permission overwrites to serializable form."""
    result = []
    for target, overwrite in overwrites.items():
        allow, deny = overwrite.pair()
        if isinstance(target, discord.Role):
            # Skip managed/bot roles - they won't exist on the target server
            if target.managed:
                continue
            target_type = "role"
            target_name = "everyone" if target == guild.default_role else target.name
        elif isinstance(target, discord.Member):
            target_type = "member"
            target_name = str(target.id)
        else:
            continue

        result.append(
            PermissionOverwriteData(
                target_type=target_type,
                target_name=target_name,
                allow=allow.value,
                deny=deny.value,
            )
        )
    return result


def _serialize_channel(
    channel: discord.abc.GuildChannel, guild: discord.Guild
) -> Optional[ChannelData]:
    """Serialize a single channel."""
    channel_type = CHANNEL_TYPE_MAP.get(channel.type)
    if channel_type is None:
        return None

    data = ChannelData(
        name=channel.name,
        type=channel_type,
        position=channel.position,
        overwrites=_serialize_overwrites(channel.overwrites, guild),
    )

    # Text-like channel attributes
    if hasattr(channel, "topic"):
        data.topic = channel.topic
    if hasattr(channel, "slowmode_delay"):
        data.slowmode = channel.slowmode_delay
    if hasattr(channel, "nsfw"):
        data.nsfw = channel.nsfw
    if hasattr(channel, "default_auto_archive_duration"):
        data.default_auto_archive = channel.default_auto_archive_duration

    # Voice channel attributes
    if hasattr(channel, "bitrate"):
        data.bitrate = channel.bitrate
    if hasattr(channel, "user_limit"):
        data.user_limit = channel.user_limit

    return data


async def serialize_guild(guild: discord.Guild) -> TemplateData:
    """
    Capture the full structure of a guild into a TemplateData object.

    Serializes: roles, categories, channels (with overwrites), guild name, icon.
    Skips: bot-managed roles, the bot's own role, @everyone role (handled separately).
    """
    # Serialize icon
    icon_b64 = None
    if guild.icon:
        try:
            icon_bytes = await guild.icon.read()
            icon_b64 = base64.b64encode(icon_bytes).decode("utf-8")
        except Exception:
            pass

    # Serialize roles (skip @everyone, managed/bot roles)
    roles = []
    for role in sorted(guild.roles, key=lambda r: r.position):
        if role == guild.default_role:
            continue
        if role.managed:
            continue
        roles.append(
            RoleData(
                name=role.name,
                color=role.color.value,
                permissions=role.permissions.value,
                hoist=role.hoist,
                mentionable=role.mentionable,
                position=role.position,
            )
        )

    # Serialize categories and their channels
    categories = []
    categorized_channel_ids = set()

    for category in sorted(guild.categories, key=lambda c: c.position):
        cat_channels = []
        for channel in sorted(category.channels, key=lambda c: c.position):
            serialized = _serialize_channel(channel, guild)
            if serialized:
                cat_channels.append(serialized)
                categorized_channel_ids.add(channel.id)

        categories.append(
            CategoryData(
                name=category.name,
                position=category.position,
                overwrites=_serialize_overwrites(category.overwrites, guild),
                channels=cat_channels,
            )
        )

    # Serialize uncategorized channels
    uncategorized = []
    for channel in sorted(guild.channels, key=lambda c: c.position):
        if channel.id in categorized_channel_ids:
            continue
        if isinstance(channel, discord.CategoryChannel):
            continue
        serialized = _serialize_channel(channel, guild)
        if serialized:
            uncategorized.append(serialized)

    return TemplateData(
        guild_name=guild.name,
        icon=icon_b64,
        roles=roles,
        categories=categories,
        channels=uncategorized,
    )
