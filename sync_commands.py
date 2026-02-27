"""
sync_commands.py — Run this ONCE whenever you add new slash commands.
DO NOT run this on every restart — it will cause duplicates.

Usage:
    python sync_commands.py
"""

import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import sys

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Import the bot with all commands registered
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def sync():
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    async with client:
        await client.login(TOKEN)
        http = client.http
        app_id = (await http.application_info())["id"]

        # Step 1: Clear all existing global commands
        print("🗑️  Clearing all existing commands...")
        await http.bulk_upsert_global_commands(app_id, [])
        print("   ✅ Cleared!")

        # Step 2: Wait for Discord to propagate
        print("⏳ Waiting for Discord to propagate...")
        await asyncio.sleep(3)

    print("\n✅ Done! Now:")
    print("   1. Start your bot: python dokkan_bot.py")
    print("   2. In Discord, DM your bot: !sync")
    print("   3. Wait up to 1 hour for commands to appear globally")
    print("\n💡 Only run this script when you add NEW commands.")

asyncio.run(sync())