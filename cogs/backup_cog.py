"""
Backup slash command group.

Provides /backup save, load, list, delete, info commands
for creating and restoring server backups.

Unlike templates, backups are tied to a specific guild and
auto-named with server name + timestamp.
"""

from __future__ import annotations

from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from models import TemplateData
from utils.serializer import serialize_guild
from utils.loader import load_template
from utils.confirmation import ConfirmView


class BackupCog(commands.Cog):
    """Slash commands for managing server backups."""

    backup_group = app_commands.Group(
        name="backup",
        description="Create and restore server backups",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Autocomplete ───────────────────────────────────────────

    async def backup_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        names = await db.get_backup_names(interaction.user.id)
        return [
            app_commands.Choice(name=n, value=n)
            for n in names
            if current.lower() in n.lower()
        ][:25]

    # ── /backup save ───────────────────────────────────────────

    @backup_group.command(name="save", description="Create a backup of this server")
    @app_commands.describe(name="Optional backup name (auto-generated if empty)")
    async def backup_save(self, interaction: discord.Interaction, name: str = None):
        await interaction.response.defer(ephemeral=True)

        # Auto-generate name if not provided
        if not name:
            timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            # Sanitize guild name for use as backup name
            safe_name = "".join(
                c if c.isalnum() or c in "-_" else "-"
                for c in interaction.guild.name[:20]
            ).strip("-")
            name = f"{safe_name}-{timestamp}"

        # Serialize the guild
        template_data = await serialize_guild(interaction.guild)
        json_data = template_data.to_json()

        # Save to database
        await db.save_backup(
            user_id=interaction.user.id,
            guild_id=interaction.guild.id,
            name=name,
            guild_name=interaction.guild.name,
            data=json_data,
        )

        embed = discord.Embed(
            title=f"Backup Created: `{name}`",
            color=discord.Color.green(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Server", value=interaction.guild.name, inline=True)
        embed.add_field(name="Roles", value=str(template_data.role_count), inline=True)
        embed.add_field(name="Channels", value=str(template_data.channel_count), inline=True)
        embed.add_field(name="Categories", value=str(template_data.category_count), inline=True)
        embed.add_field(name="Icon", value="Yes" if template_data.icon else "No", inline=True)
        embed.set_footer(text=f"Backed up by {interaction.user}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /backup load ───────────────────────────────────────────

    @backup_group.command(name="load", description="Restore a backup onto this server (destructive!)")
    @app_commands.describe(name="Backup name to restore")
    @app_commands.autocomplete(name=backup_name_autocomplete)
    async def backup_load(self, interaction: discord.Interaction, name: str):
        # Fetch backup
        record = await db.get_backup(interaction.user.id, name)
        if not record:
            await interaction.response.send_message(
                f"Backup `{name}` not found.", ephemeral=True
            )
            return

        template_data = TemplateData.from_json(record["data"])

        # Show confirmation
        embed = discord.Embed(
            title="⚠️ Restore Backup — Destructive Operation",
            description=(
                f"Restoring backup **`{name}`** will:\n"
                f"- **Delete ALL** existing channels\n"
                f"- **Delete ALL** removable roles\n"
                f"- Recreate **{template_data.role_count}** roles\n"
                f"- Recreate **{template_data.category_count}** categories "
                f"and **{template_data.channel_count}** channels\n"
                f"- Rename server to **{template_data.guild_name}**\n\n"
                f"**This cannot be undone.** Are you sure?"
            ),
            color=discord.Color.red(),
        )

        view = ConfirmView(author_id=interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        await view.wait()

        if not view.value:
            await interaction.followup.send("Restore cancelled.", ephemeral=True)
            return

        progress_msg = await interaction.followup.send(
            "Starting backup restore...", ephemeral=True
        )

        keep_channel = interaction.channel

        async def progress(msg: str):
            try:
                await progress_msg.edit(content=msg)
            except Exception:
                pass

        stats = await load_template(
            guild=interaction.guild,
            template=template_data,
            keep_channel=keep_channel,
            progress=progress,
        )

        result_embed = discord.Embed(
            title="Backup Restored",
            description=f"Backup **`{name}`** has been restored.",
            color=discord.Color.green(),
        )
        result_embed.add_field(
            name="Roles",
            value=f"Deleted: {stats['roles_deleted']}\nCreated: {stats['roles_created']}\nFailed: {stats['roles_failed']}",
            inline=True,
        )
        result_embed.add_field(
            name="Channels",
            value=f"Deleted: {stats['channels_deleted']}\nCreated: {stats['channels_created']}\nFailed: {stats['channels_failed']}",
            inline=True,
        )
        result_embed.add_field(
            name="Categories",
            value=f"Created: {stats['categories_created']}\nFailed: {stats['categories_failed']}",
            inline=True,
        )

        try:
            for channel in interaction.guild.text_channels:
                try:
                    await channel.send(embed=result_embed)
                    break
                except Exception:
                    continue
        except Exception:
            pass

    # ── /backup list ───────────────────────────────────────────

    @backup_group.command(name="list", description="List your saved backups")
    async def backup_list(self, interaction: discord.Interaction):
        backups = await db.list_backups(interaction.user.id)

        if not backups:
            await interaction.response.send_message(
                "You have no saved backups. Use `/backup save` to create one.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Your Backups",
            color=discord.Color.blue(),
        )

        for b in backups[:25]:
            created = b["created_at"][:19]
            embed.add_field(
                name=f"`{b['name']}`",
                value=f"Server: {b['guild_name']}\nCreated: {created}",
                inline=True,
            )

        embed.set_footer(text=f"{len(backups)} backup(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /backup delete ─────────────────────────────────────────

    @backup_group.command(name="delete", description="Delete a saved backup")
    @app_commands.describe(name="Backup name to delete")
    @app_commands.autocomplete(name=backup_name_autocomplete)
    async def backup_delete(self, interaction: discord.Interaction, name: str):
        deleted = await db.delete_backup(interaction.user.id, name)

        if deleted:
            await interaction.response.send_message(
                f"Backup `{name}` deleted.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Backup `{name}` not found.", ephemeral=True
            )

    # ── /backup info ───────────────────────────────────────────

    @backup_group.command(name="info", description="Show details about a backup")
    @app_commands.describe(name="Backup name to inspect")
    @app_commands.autocomplete(name=backup_name_autocomplete)
    async def backup_info(self, interaction: discord.Interaction, name: str):
        record = await db.get_backup(interaction.user.id, name)
        if not record:
            await interaction.response.send_message(
                f"Backup `{name}` not found.", ephemeral=True
            )
            return

        template_data = TemplateData.from_json(record["data"])

        embed = discord.Embed(
            title=f"Backup: `{name}`",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Server", value=template_data.guild_name, inline=True)
        embed.add_field(name="Server ID", value=str(record["guild_id"]), inline=True)
        embed.add_field(name="Roles", value=str(template_data.role_count), inline=True)
        embed.add_field(name="Channels", value=str(template_data.channel_count), inline=True)
        embed.add_field(name="Categories", value=str(template_data.category_count), inline=True)
        embed.add_field(name="Icon", value="Yes" if template_data.icon else "No", inline=True)

        if template_data.categories:
            cat_list = "\n".join(
                f"• {c.name} ({len(c.channels)} channels)"
                for c in template_data.categories
            )
            embed.add_field(name="Category List", value=cat_list[:1024], inline=False)

        if template_data.roles:
            role_list = ", ".join(r.name for r in reversed(template_data.roles))
            embed.add_field(name="Roles", value=role_list[:1024], inline=False)

        embed.add_field(name="Created", value=record["created_at"][:19], inline=True)
        embed.set_footer(text=f"Requested by {interaction.user}")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(BackupCog(bot))
