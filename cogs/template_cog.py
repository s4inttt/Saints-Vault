"""
Template slash command group.

Provides /template save, load, list, delete, info commands
for saving and loading server structure templates.
"""

from __future__ import annotations

import re
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import database as db
from models import TemplateData
from utils.serializer import serialize_guild
from utils.loader import load_template
from utils.confirmation import ConfirmView

NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


class TemplateCog(commands.Cog):
    """Slash commands for managing server templates."""

    template_group = app_commands.Group(
        name="template",
        description="Save and load server templates",
        default_permissions=discord.Permissions(administrator=True),
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Autocomplete ───────────────────────────────────────────

    async def template_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        names = await db.get_template_names(interaction.user.id)
        return [
            app_commands.Choice(name=n, value=n)
            for n in names
            if current.lower() in n.lower()
        ][:25]

    # ── /template save ─────────────────────────────────────────

    @template_group.command(name="save", description="Save this server as a template")
    @app_commands.describe(name="Template name (1-32 chars, letters/numbers/-/_)")
    async def template_save(self, interaction: discord.Interaction, name: str):
        if not NAME_PATTERN.match(name):
            await interaction.response.send_message(
                "Invalid name. Use 1-32 characters: letters, numbers, `-`, `_`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Serialize the guild
        template_data = await serialize_guild(interaction.guild)
        json_data = template_data.to_json()

        # Save to database
        updated = await db.save_template(
            user_id=interaction.user.id,
            name=name,
            guild_name=interaction.guild.name,
            data=json_data,
        )

        action = "updated" if updated else "saved"

        embed = discord.Embed(
            title=f"Template {action}: `{name}`",
            color=discord.Color.green(),
            timestamp=datetime.utcnow(),
        )
        embed.add_field(name="Source Server", value=interaction.guild.name, inline=True)
        embed.add_field(name="Roles", value=str(template_data.role_count), inline=True)
        embed.add_field(name="Channels", value=str(template_data.channel_count), inline=True)
        embed.add_field(name="Categories", value=str(template_data.category_count), inline=True)
        embed.add_field(name="Icon", value="Yes" if template_data.icon else "No", inline=True)
        embed.set_footer(text=f"Saved by {interaction.user}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /template load ─────────────────────────────────────────

    @template_group.command(name="load", description="Load a template onto this server (destructive!)")
    @app_commands.describe(name="Template name to load")
    @app_commands.autocomplete(name=template_name_autocomplete)
    async def template_load(self, interaction: discord.Interaction, name: str):
        # Fetch template
        record = await db.get_template(interaction.user.id, name)
        if not record:
            await interaction.response.send_message(
                f"Template `{name}` not found.", ephemeral=True
            )
            return

        template_data = TemplateData.from_json(record["data"])

        # Show confirmation
        embed = discord.Embed(
            title="⚠️ Destructive Operation",
            description=(
                f"Loading template **`{name}`** will:\n"
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
            await interaction.followup.send("Load cancelled.", ephemeral=True)
            return

        # Defer for long operation - create a visible progress channel
        progress_msg = await interaction.followup.send(
            "Starting template load...", ephemeral=True
        )

        # Use the current channel as keep_channel for progress updates
        keep_channel = interaction.channel

        async def progress(msg: str):
            try:
                await progress_msg.edit(content=msg)
            except Exception:
                pass

        # Run the load
        stats = await load_template(
            guild=interaction.guild,
            template=template_data,
            keep_channel=keep_channel,
            progress=progress,
        )

        # Build results embed
        result_embed = discord.Embed(
            title="Template Loaded",
            description=f"Template **`{name}`** has been applied.",
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

        # Try to send in a new text channel since the old one may be deleted
        try:
            # Find any text channel to send the result
            for channel in interaction.guild.text_channels:
                try:
                    await channel.send(embed=result_embed)
                    break
                except Exception:
                    continue
        except Exception:
            pass

    # ── /template list ─────────────────────────────────────────

    @template_group.command(name="list", description="List your saved templates")
    async def template_list(self, interaction: discord.Interaction):
        templates = await db.list_templates(interaction.user.id)

        if not templates:
            await interaction.response.send_message(
                "You have no saved templates. Use `/template save` to create one.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Your Templates",
            color=discord.Color.blue(),
        )

        for t in templates[:25]:  # Discord embed field limit
            created = t["created_at"][:10]
            updated = t["updated_at"][:10]
            embed.add_field(
                name=f"`{t['name']}`",
                value=f"Server: {t['guild_name']}\nCreated: {created}\nUpdated: {updated}",
                inline=True,
            )

        embed.set_footer(text=f"{len(templates)} template(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /template delete ───────────────────────────────────────

    @template_group.command(name="delete", description="Delete a saved template")
    @app_commands.describe(name="Template name to delete")
    @app_commands.autocomplete(name=template_name_autocomplete)
    async def template_delete(self, interaction: discord.Interaction, name: str):
        deleted = await db.delete_template(interaction.user.id, name)

        if deleted:
            await interaction.response.send_message(
                f"Template `{name}` deleted.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Template `{name}` not found.", ephemeral=True
            )

    # ── /template info ─────────────────────────────────────────

    @template_group.command(name="info", description="Show details about a template")
    @app_commands.describe(name="Template name to inspect")
    @app_commands.autocomplete(name=template_name_autocomplete)
    async def template_info(self, interaction: discord.Interaction, name: str):
        record = await db.get_template(interaction.user.id, name)
        if not record:
            await interaction.response.send_message(
                f"Template `{name}` not found.", ephemeral=True
            )
            return

        template_data = TemplateData.from_json(record["data"])

        embed = discord.Embed(
            title=f"Template: `{name}`",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Source Server", value=template_data.guild_name, inline=True)
        embed.add_field(name="Roles", value=str(template_data.role_count), inline=True)
        embed.add_field(name="Channels", value=str(template_data.channel_count), inline=True)
        embed.add_field(name="Categories", value=str(template_data.category_count), inline=True)
        embed.add_field(name="Icon", value="Yes" if template_data.icon else "No", inline=True)

        # List categories
        if template_data.categories:
            cat_list = "\n".join(
                f"• {c.name} ({len(c.channels)} channels)"
                for c in template_data.categories
            )
            embed.add_field(name="Category List", value=cat_list[:1024], inline=False)

        # List roles
        if template_data.roles:
            role_list = ", ".join(r.name for r in reversed(template_data.roles))
            embed.add_field(name="Roles", value=role_list[:1024], inline=False)

        embed.add_field(name="Created", value=record["created_at"][:19], inline=True)
        embed.add_field(name="Updated", value=record["updated_at"][:19], inline=True)
        embed.set_footer(text=f"Requested by {interaction.user}")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TemplateCog(bot))
