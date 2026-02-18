"""
Discord Template Bot - Main entry point.

A Xenon-style bot that can save any server's structure as a template
and load it onto another server. Also supports server backups.

Commands:
  /template save|load|list|delete|info
  /backup save|load|list|delete|info
"""

import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

import database as db

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("DISCORD_TOKEN not found in .env file")


def main():
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True

    bot = commands.Bot(
        command_prefix="!",  # unused, slash commands only
        intents=intents,
        description="Template bot — save and load server structures",
    )

    @bot.event
    async def on_ready():
        print(f"Logged in as {bot.user} (ID: {bot.user.id})")
        print(f"Connected to {len(bot.guilds)} guild(s)")
        print("------")

    @bot.event
    async def setup_hook():
        # Initialize database
        await db.init_db()
        print("Database initialized")

        # Load cogs
        await bot.load_extension("cogs.template_cog")
        await bot.load_extension("cogs.backup_cog")
        print("Cogs loaded")

        # Sync slash commands
        synced = await bot.tree.sync()
        print(f"Slash commands synced ({len(synced)} commands)")

    bot.run(TOKEN)


if __name__ == "__main__":
    main()
