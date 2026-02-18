"""
Data models for server templates.

Nested dataclasses that serialize to/from JSON for storage.
Permission overwrites reference roles by name (not ID) since IDs
change when roles are recreated on a different server.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class PermissionOverwriteData:
    target_type: str  # "role" or "member"
    target_name: str  # role name or member ID; @everyone stored as "everyone"
    allow: int  # permission bitfield
    deny: int   # permission bitfield


@dataclass
class ChannelData:
    name: str
    type: str  # "text", "voice", "stage", "forum", "announcement"
    position: int
    topic: Optional[str] = None
    slowmode: int = 0
    nsfw: bool = False
    bitrate: Optional[int] = None
    user_limit: Optional[int] = None
    default_auto_archive: Optional[int] = None
    overwrites: list[PermissionOverwriteData] = field(default_factory=list)


@dataclass
class CategoryData:
    name: str
    position: int
    overwrites: list[PermissionOverwriteData] = field(default_factory=list)
    channels: list[ChannelData] = field(default_factory=list)


@dataclass
class RoleData:
    name: str
    color: int
    permissions: int  # permission bitfield
    hoist: bool
    mentionable: bool
    position: int


@dataclass
class TemplateData:
    guild_name: str
    icon: Optional[str] = None  # base64-encoded icon
    roles: list[RoleData] = field(default_factory=list)
    categories: list[CategoryData] = field(default_factory=list)
    channels: list[ChannelData] = field(default_factory=list)  # uncategorized

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> TemplateData:
        data = json.loads(raw)
        return cls(
            guild_name=data["guild_name"],
            icon=data.get("icon"),
            roles=[RoleData(**r) for r in data.get("roles", [])],
            categories=[
                CategoryData(
                    name=c["name"],
                    position=c["position"],
                    overwrites=[PermissionOverwriteData(**o) for o in c.get("overwrites", [])],
                    channels=[
                        ChannelData(
                            **{
                                k: (
                                    [PermissionOverwriteData(**o) for o in v]
                                    if k == "overwrites"
                                    else v
                                )
                                for k, v in ch.items()
                            }
                        )
                        for ch in c.get("channels", [])
                    ],
                )
                for c in data.get("categories", [])
            ],
            channels=[
                ChannelData(
                    **{
                        k: (
                            [PermissionOverwriteData(**o) for o in v]
                            if k == "overwrites"
                            else v
                        )
                        for k, v in ch.items()
                    }
                )
                for ch in data.get("channels", [])
            ],
        )

    @property
    def role_count(self) -> int:
        return len(self.roles)

    @property
    def channel_count(self) -> int:
        total = len(self.channels)
        for cat in self.categories:
            total += len(cat.channels)
        return total

    @property
    def category_count(self) -> int:
        return len(self.categories)
