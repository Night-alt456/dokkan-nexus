import discord
import asyncio
from dotenv import load_dotenv
import os

load_dotenv()

async def clear():
    client = discord.Client(intents=discord.Intents.default())
    async with client:
        await client.login(os.getenv("DISCORD_TOKEN"))
        http = client.http
        app_id = (await http.application_info())["id"]
        await http.bulk_upsert_global_commands(app_id, [])
        print("✅ All commands cleared!")

asyncio.run(clear())