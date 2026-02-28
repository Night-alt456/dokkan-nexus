import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import os
import re
from dotenv import load_dotenv

load_dotenv()

# ======================
# CONFIG
# ======================
TOKEN   = os.getenv("DISCORD_TOKEN")
DB_PATH = DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dokkan.db")

TYPE_COLORS = {
    "AGL": discord.Color.blue(),
    "TEQ": discord.Color.green(),
    "INT": discord.Color.purple(),
    "STR": discord.Color.red(),
    "PHY": discord.Color.orange(),
}

# Replace these with your actual server emoji IDs
#E.g. "AGL": "<:AGL:123456789012345678>"

TYPE_EMOJIS = {
    "AGL": "<:AGL:1477068036081979394>",
    "TEQ": "<:TEQ:1477068030298030141>",
    "INT": "<:INT:1477068031535485110>",
    "STR": "<:STR:1477068034660237312>",
    "PHY": "<:PHY:1477068033020264623>",
}

RARITY_EMOJIS = {
    "LR":  "<:LR:1477068021901037668>",
    "UR":  "<:UR:1477068023511777300>",
    "SSR": "<:SSR:1477068024774398094>",
    "SR":  "<:SR:1477068026456314028>",
    "R":   "<:R:1477068027903082566>",
    "N":   "<:N:1477068029152989204>",
}

# ======================
# COMMUNITY CONFIG
# ======================
# Your Discord user ID — can delete any submission
SUPER_ADMIN_ID = 703585529039552604  # Replace with your actual Discord user ID e.g. 123456789012345678

# Role name that can delete submissions in their server
MOD_ROLE_NAME = "Dokkan Mod"

# Current challenge events — update this list when new events release
CHALLENGE_EVENTS = [
    "Fighting Legend Goku",
    "Fighting Legend Vegeta",
    "Fighting Legend Frieza",
    "Dokkan Event Boss Rush",
    "Collection of Epic Battles",
    "Super Battle Road",
    "Fearsome Activition Cell Max",
    "Fighting Sprit of the Saiyans and Pride of the Wicked Bloodline",
    "Intense Fights",
    "Heart-Pounding Heroine Battle",
    "Festival of Battle",
    "Ultimate Red Zone",
    "Supreme Magnificent Battle",
    "The Greatest Tours",
]

# ======================
# BOT SETUP
# ======================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ======================
# DATABASE HELPERS
# ======================
def db_search(query: str, card_type: str = None, rarity: str = None, limit: int = 10):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    like = f"%{query}%"

    filters = "(name LIKE ? OR title LIKE ? OR page_title LIKE ?)"
    params = [like, like, like]

    if card_type:
        filters += " AND type = ?"
        params.append(card_type.upper())
    if rarity:
        filters += " AND rarity = ?"
        params.append(rarity.upper())

    params.append(limit * 3)  # fetch extra to account for filtering
    results = c.execute(f"""
        SELECT * FROM cards
        WHERE {filters}
        ORDER BY CASE rarity
            WHEN 'LR'  THEN 1
            WHEN 'UR'  THEN 2
            WHEN 'SSR' THEN 3
            WHEN 'SR'  THEN 4
            ELSE 5
        END
        LIMIT ?
    """, params).fetchall()

    # Filter out UR cards that have an LR version with the same character name
    if not rarity:  # only filter if user didn't explicitly request a rarity
        lr_names = set()
        for card in results:
            if card["rarity"] == "LR" and card["name"]:
                lr_names.add(card["name"].strip().lower())

        filtered = []
        for card in results:
            if card["rarity"] == "UR" and card["name"] and card["name"].strip().lower() in lr_names:
                continue  # skip this UR, an LR version exists
            filtered.append(card)
        results = filtered

    conn.close()
    return results[:limit]

def db_get_card(page_title: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    result = c.execute("SELECT * FROM cards WHERE page_title = ?", (page_title,)).fetchone()
    conn.close()
    return result

def db_count():
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    last_sync = conn.execute("SELECT MAX(synced_at) FROM cards").fetchone()[0]
    conn.close()
    return count, last_sync

def db_exists():
    if not os.path.exists(DB_PATH):
        return False
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    conn.close()
    return count > 0

def init_community_db():
    """Create community_teams table if it doesn't exist"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS community_teams (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            username    TEXT NOT NULL,
            server_id   TEXT,
            server_name TEXT,
            event       TEXT NOT NULL,
            leader      TEXT,
            card2       TEXT, card3 TEXT, card4 TEXT,
            card5       TEXT, card6 TEXT,
            friend_unit TEXT,
            description TEXT,
            submitted_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Add friend_unit column if it doesn't exist (for existing DBs)
    try:
        conn.execute("ALTER TABLE community_teams ADD COLUMN friend_unit TEXT")
    except Exception:
        pass
    # Add stage column if it doesn't exist (for existing DBs)
    try:
        conn.execute("ALTER TABLE community_teams ADD COLUMN stage TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()

def find_card_url(name: str) -> str:
    """Look up a card's wiki URL from the DB by name or title"""
    if not name:
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    like = f"%{name}%"
    result = conn.execute("""
        SELECT wiki_url, title, name, rarity FROM cards
        WHERE name LIKE ? OR title LIKE ?
        ORDER BY CASE rarity
            WHEN 'LR' THEN 1 WHEN 'UR' THEN 2 WHEN 'SSR' THEN 3 ELSE 4
        END LIMIT 1
    """, (like, like)).fetchone()
    conn.close()
    return (result["wiki_url"], result["title"] or result["name"], result["rarity"]) if result else (None, name, None)

# ======================
# ON READY
# ======================
GUILD_ID = 1476108585095139400
TEAM_LOG_CHANNEL_ID = 1476108750384398376  # Channel where submitted teams are posted
SERVER_COUNT_CHANNEL_ID = 1476257939470942279  # Replace with your voice channel ID

@bot.event
@bot.event
async def on_ready():
    init_community_db()
    
    # Fetch application emojis dynamically
    app_emojis = await bot.fetch_application_emojis()
    for emoji in app_emojis:
        if emoji.name in TYPE_EMOJIS:
            TYPE_EMOJIS[emoji.name] = str(emoji)
        if emoji.name in RARITY_EMOJIS:
            RARITY_EMOJIS[emoji.name] = str(emoji)
    
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")


async def update_server_count():
    if SERVER_COUNT_CHANNEL_ID == 0:
        return
    channel = bot.get_channel(SERVER_COUNT_CHANNEL_ID)
    if channel:
        count = len(bot.guilds)
        try:
            await channel.edit(name=f"📊 Servers: {count}")
        except Exception as e:
            print(f"⚠️  Could not update server count channel: {e}")

@bot.event
async def on_guild_join(guild):
    print(f"✅ Joined server: {guild.name}")
    await update_server_count()

@bot.event
async def on_guild_remove(guild):
    print(f"❌ Left server: {guild.name}")
    await update_server_count()

# ======================
# HELPERS
# ======================
def clean_wiki(text: str) -> str:
    """Strip wiki markup from text for clean display"""
    if not text:
        return ""
    import re as _re
    text = _re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=_re.DOTALL)
    text = _re.sub(r'<ref[^/]*/>', '', text)
    text = _re.sub(r'<br\s*/?>', '\n', text, flags=_re.IGNORECASE)
    text = _re.sub(r'<b>(.*?)</b>', r'\1', text, flags=_re.DOTALL)
    text = _re.sub(r'<[^>]+>', '', text)
    text = _re.sub(r'\[\[File:[^\]]+\]\]', '', text)
    text = _re.sub(r'\[\[([^\|\]]+\|)?([^\]]+)\]\]', r'\2', text)
    text = _re.sub(r'{{[^}]+}}', '', text)
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def clean_type(raw: str) -> str:
    raw = (raw or "").upper().strip()
    for t in ["AGL", "TEQ", "INT", "STR", "PHY"]:
        if raw.endswith(t):
            return t
    return raw[:3] if len(raw) >= 3 else raw

def get_rarity(raw: str) -> str:
    raw = (raw or "").upper().strip()
    for r in ["LR", "SSR", "SR", "UR", "R", "N"]:
        if raw == r:
            return r
    return raw[:2]

# ======================
# BUILD CARD EMBED
# ======================
def build_card_embed(card):
    card_type    = clean_type(card["type"] or "")
    rarity       = get_rarity(card["rarity"] or "")
    color        = TYPE_COLORS.get(card_type, discord.Color.blurple())
    type_emoji   = TYPE_EMOJIS.get(card_type, "⚪")
    rarity_emoji = RARITY_EMOJIS.get(rarity, "⭐")

    display_title = card["title"] or card["page_title"] or "Unknown Card"
    name = card["name"] or ""
    url  = card["wiki_url"] or ""

    embed = discord.Embed(title=f"{rarity_emoji} {display_title}", url=url, color=color)

    if name and name != display_title:
        embed.description = f"✦ *{name}*"

    # Card info + stats in one row
    embed.add_field(
        name="📋 Card Info",
        value=(
            f"{type_emoji} **{card_type}**\n"
            f"{rarity_emoji} **{rarity}**\n"
            f"💰 Cost: **{card['cost'] or '?'}**\n"
            f"📈 Max Lv: **{card['max_level'] or '?'}**"
        ),
        inline=True
    )

    if any(card[k] for k in ["base_hp", "base_atk", "base_def"]):
        embed.add_field(
            name="📊 Base Stats",
            value=(
                f"**HP:** {card['base_hp'] or '?'}\n"
                f"**ATK:** {card['base_atk'] or '?'}\n"
                f"**DEF:** {card['base_def'] or '?'}"
            ),
            inline=True
        )

    if any(card[k] for k in ["max_hp", "max_atk", "max_def"]):
        embed.add_field(
            name=f"💪 Max Stats (Lv.{card['max_level'] or '?'})",
            value=(
                f"**HP:** {card['max_hp'] or '?'}\n"
                f"**ATK:** {card['max_atk'] or '?'}\n"
                f"**DEF:** {card['max_def'] or '?'}"
            ),
            inline=True
        )

    embed.add_field(name="\u200b", value="\u200b", inline=False)

    if card["leader_skill"]:
        embed.add_field(name="👑 Leader Skill", value=f"```{card['leader_skill'][:500]}```", inline=False)

    if card["super_attack"]:
        sa_name = card["sa_name"] or ""
        sa_title = f"⚡ Super Attack — *{sa_name}*" if sa_name else "⚡ Super Attack"
        embed.add_field(name=sa_title, value=f"```{card['super_attack'][:300]}```", inline=False)

    if card["passive_skill"]:
        embed.add_field(name="✨ Passive Skill", value=card["passive_skill"][:1024], inline=False)

    if card["links"]:
        links = [l.strip() for l in card["links"].replace("|", " - ").split(" - ") if l.strip()]
        if links:
            embed.add_field(name="🔗 Link Skills", value="  •  ".join(links[:8]), inline=False)

    if card["categories"]:
        cats = [c.strip() for c in card["categories"].replace("|", " - ").split(" - ") if c.strip()]
        if cats:
            embed.add_field(name="📁 Categories", value="  •  ".join(cats[:10]), inline=False)

    if card["image"]:
        embed.set_thumbnail(url=card["image"])

    embed.set_footer(text="Dokkan Battle Wiki  •  Click the title to view full card page")
    return embed

# ======================
# ======================
# /card
# ======================
@bot.tree.command(name="card", description="Look up a Dokkan Battle card")
@app_commands.describe(
    name="Card name to search for (e.g. Broly, Super Saiyan Goku)",
    card_type="Filter by type (optional)",
    rarity="Filter by rarity (optional)"
)
@app_commands.choices(card_type=[
    app_commands.Choice(name="🔵 AGL", value="AGL"),
    app_commands.Choice(name="🟢 TEQ", value="TEQ"),
    app_commands.Choice(name="🟣 INT", value="INT"),
    app_commands.Choice(name="🔴 STR", value="STR"),
    app_commands.Choice(name="🟠 PHY", value="PHY"),
])
@app_commands.choices(rarity=[
    app_commands.Choice(name="🌟 LR",  value="LR"),
    app_commands.Choice(name="⭐ UR",  value="UR"),
    app_commands.Choice(name="💫 SSR", value="SSR"),
    app_commands.Choice(name="✨ SR",  value="SR"),
    app_commands.Choice(name="🔹 R",   value="R"),
    app_commands.Choice(name="⬜ N",   value="N"),
])
async def card_lookup(interaction: discord.Interaction, name: str, card_type: str = None, rarity: str = None):
    await interaction.response.defer()

    if not db_exists():
        return await interaction.followup.send("❌ Database is empty! Run `python sync.py` first.", ephemeral=True)

    results = db_search(name, card_type, rarity)

    if not results:
        msg = f"❌ No results found for **{name}**"
        parts = [x for x in [card_type, rarity] if x]
        if parts:
            msg += f" ({' • '.join(parts)})"
        return await interaction.followup.send(msg + ". Try a different search term.", ephemeral=True)

    if len(results) == 1:
        return await interaction.followup.send(embed=build_card_embed(results[0]))

    parts = [x for x in [card_type, rarity] if x]
    embed = discord.Embed(
        title=f"🔍 Results for '{name}'" + (f" ({' • '.join(parts)})" if parts else ""),
        description="Multiple cards found! Use `/cardurl` with a wiki URL for a specific one.",
        color=discord.Color.gold()
    )
    for i, card in enumerate(results[:10], 1):
        display = card["title"] or card["page_title"]
        r_emoji = RARITY_EMOJIS.get(get_rarity(card["rarity"] or ""), "⭐")
        t_emoji = TYPE_EMOJIS.get(clean_type(card["type"] or ""), "⚪")
        embed.add_field(
            name=f"{i}. {r_emoji} {t_emoji} {display}",
            value=f"[View on Wiki]({card['wiki_url']})",
            inline=False
        )
    embed.set_footer(text="Tip: Add more filters to narrow down results")
    await interaction.followup.send(embed=embed)


# /cardurl
# ======================
@bot.tree.command(name="cardurl", description="Look up a card using its Dokkan wiki URL")
@app_commands.describe(url="The full Dokkan wiki URL of the card")
async def card_by_url(interaction: discord.Interaction, url: str):
    await interaction.response.defer()

    if "dbz-dokkanbattle.fandom.com" not in url:
        return await interaction.followup.send("❌ Please use a valid Dokkan Battle wiki URL.", ephemeral=True)

    page_title = url.split("/wiki/")[-1].replace("_", " ").split("#")[0]
    card = db_get_card(page_title)

    if not card:
        return await interaction.followup.send(
            f"❌ **{page_title}** isn't in the database yet. Try syncing or searching by name.",
            ephemeral=True
        )

    await interaction.followup.send(embed=build_card_embed(card))

# ======================
# /dbstats
# ======================
@bot.tree.command(name="dbstats", description="Show Dokkan database stats")
async def db_stats(interaction: discord.Interaction):
    if not db_exists():
        return await interaction.response.send_message("❌ Database is empty! Run `python sync.py` first.", ephemeral=True)

    count, last_sync = db_count()
    embed = discord.Embed(title="🗄️ Dokkan Database Stats", color=discord.Color.blurple())
    embed.add_field(name="📊 Total Cards", value=f"`{count:,}`", inline=True)
    embed.add_field(name="🕐 Last Synced", value=f"`{last_sync[:19] if last_sync else 'Never'} UTC`", inline=True)
    embed.set_footer(text="Run python sync.py --update to add new cards")
    await interaction.response.send_message(embed=embed)

# ======================
# /links
# ======================
@bot.tree.command(name="links", description="Find the best linking partners for a card")
@app_commands.describe(
    name="Card name to find linking partners for",
    partner_type="Filter partners by type (optional)",
    partner_rarity="Filter partners by rarity (optional)"
)
@app_commands.choices(partner_type=[
    app_commands.Choice(name="🔵 AGL", value="AGL"),
    app_commands.Choice(name="🟢 TEQ", value="TEQ"),
    app_commands.Choice(name="🟣 INT", value="INT"),
    app_commands.Choice(name="🔴 STR", value="STR"),
    app_commands.Choice(name="🟠 PHY", value="PHY"),
])
@app_commands.choices(partner_rarity=[
    app_commands.Choice(name="🌟 LR",  value="LR"),
    app_commands.Choice(name="⭐ UR",  value="UR"),
    app_commands.Choice(name="💫 SSR", value="SSR"),
    app_commands.Choice(name="✨ SR",  value="SR"),
    app_commands.Choice(name="🔹 R",   value="R"),
    app_commands.Choice(name="⬜ N",   value="N"),
])
async def links_lookup(interaction: discord.Interaction, name: str, partner_type: str = None, partner_rarity: str = None):
    await interaction.response.defer()

    if not db_exists():
        return await interaction.followup.send("❌ Database is empty! Run `python sync.py` first.", ephemeral=True)

    # Find the base card — no type/rarity filter, just by name
    results = db_search(name, limit=5)
    if not results:
        return await interaction.followup.send(f"❌ No card found for **{name}**.", ephemeral=True)

    base_card = results[0]
    base_links = [l.strip() for l in (base_card["links"] or "").replace("|", " - ").split(" - ") if l.strip()]
    print(f"🔍 /links found card: '{base_card['page_title']}' | links raw: '{base_card['links'][:100] if base_card['links'] else 'EMPTY'}'")

    if not base_links:
        return await interaction.followup.send(
            f"❌ **{base_card['title'] or base_card['page_title']}** has no link skills in the database.",
            ephemeral=True
        )

    # Search DB for partners — apply type/rarity filters here
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    query = "SELECT * FROM cards WHERE links != '' AND links IS NOT NULL AND page_title != ?"
    params = [base_card["page_title"]]

    if partner_type:
        query += " AND type = ?"
        params.append(partner_type.upper())
    if partner_rarity:
        query += " AND rarity = ?"
        params.append(partner_rarity.upper())

    all_cards = c.execute(query, params).fetchall()
    conn.close()

    # Score by shared links, filtering out URs that have LR versions
    lr_names = set()
    for card in all_cards:
        if card["rarity"] == "LR" and card["name"]:
            lr_names.add(card["name"].strip().lower())

    scored = []
    for card in all_cards:
        # Skip UR if an LR of the same character exists
        if not partner_rarity and card["rarity"] == "UR" and card["name"] and card["name"].strip().lower() in lr_names:
            continue
        card_links = [l.strip() for l in (card["links"] or "").replace("|", " - ").split(" - ") if l.strip()]
        shared = [l for l in base_links if l in card_links]
        if shared:
            scored.append((card, shared))

    scored.sort(key=lambda x: len(x[1]), reverse=True)
    top = scored[:8]

    if not top:
        msg = f"❌ No linking partners found for **{base_card['title'] or base_card['page_title']}**"
        if partner_type or partner_rarity:
            parts = [x for x in [partner_type, partner_rarity] if x]
            msg += f" with filters: **{' • '.join(parts)}**"
        return await interaction.followup.send(msg + ".", ephemeral=True)

    # Build embed
    base_title  = base_card["title"] or base_card["page_title"]
    base_type   = clean_type(base_card["type"] or "")
    base_rarity = get_rarity(base_card["rarity"] or "")
    color       = TYPE_COLORS.get(base_type, discord.Color.blurple())

    embed = discord.Embed(
        title=f"🔗 Best Link Partners for {base_title}",
        description=(
            f"{TYPE_EMOJIS.get(base_type, '⚪')} {base_type}  •  "
            f"{RARITY_EMOJIS.get(base_rarity, '⭐')} {base_rarity}  •  "
            f"**{len(base_links)} link skills**\n"
            f"*{' • '.join(base_links)}*"
        ),
        color=color
    )

    for card, shared in top:
        partner_title  = card["title"] or card["page_title"]
        partner_name   = card["name"] or ""
        partner_type_  = clean_type(card["type"] or "")
        partner_rarity_ = get_rarity(card["rarity"] or "")
        t_emoji = TYPE_EMOJIS.get(partner_type_, "⚪")
        r_emoji = RARITY_EMOJIS.get(partner_rarity_, "⭐")

        display = partner_title
        if partner_name and partner_name != partner_title:
            display += f" *({partner_name})*"

        embed.add_field(
            name=f"{r_emoji} {t_emoji} {display}  —  **{len(shared)}/{len(base_links)} links**",
            value=f"✅ {' • '.join(shared)}\n[View on Wiki]({card['wiki_url']})",
            inline=False
        )

    embed.set_footer(text=f"Top {len(top)} linking partners • Dokkan Battle Wiki")
    if base_card["image"]:
        embed.set_thumbnail(url=base_card["image"])

    await interaction.followup.send(embed=embed)


# ======================
# TEAM BUILDER HELPERS
# ======================
def extract_leader_category(leader_skill: str) -> list:
    """Extract category names from a leader skill description"""
    if not leader_skill:
        return []

    # Common patterns: "Category Ki +X", '"Category Name" Category', 'Category or "Other" Category'
    categories = []

    # Match quoted category names like "Exploding Rage" Category
    quoted = re.findall(r'"([^"]+)"\s*Category', leader_skill, re.IGNORECASE)
    categories.extend(quoted)

    # Match unquoted patterns like: Saiyan Category, Pure Saiyans Category
    unquoted = re.findall(r'([A-Z][A-Za-z\s]+?)\s+Category', leader_skill)
    for c in unquoted:
        c = c.strip()
        if len(c) > 2 and c not in categories:
            categories.append(c)

    return categories

def score_team(team: list, candidate) -> int:
    """Score a candidate card based on link overlap with current team"""
    if not candidate["links"]:
        return 0
    candidate_links = set(l.strip() for l in candidate["links"].replace("|", " - ").split(" - ") if l.strip())
    if not candidate_links:
        return 0

    score = 0
    for member in team:
        if not member["links"]:
            continue
        member_links = set(l.strip() for l in member["links"].replace("|", " - ").split(" - ") if l.strip())
        shared = candidate_links & member_links
        score += len(shared)
    return score

def build_best_team(leader, pool: list, team_size: int = 5):
    """Greedily build best team from pool based on link synergy"""
    team = [leader]
    remaining = [c for c in pool if c["page_title"] != leader["page_title"]]
    honorable = []

    while len(team) < team_size + 1 and remaining:
        # Score each candidate against current team
        scored = [(card, score_team(team, card)) for card in remaining]
        scored.sort(key=lambda x: x[1], reverse=True)

        best_card, best_score = scored[0]
        team.append(best_card)
        honorable = [card for card, s in scored[1:4] if s > 0]
        remaining = [card for card, _ in scored[1:]]

    return team[1:], honorable  # exclude leader from team list

# ======================
# /team
# ======================
@bot.tree.command(name="team", description="Auto-build a team around a leader card")
@app_commands.describe(
    leader="The leader card name (e.g. Broly, Super Saiyan Goku)",
    card_type="Filter team members by type (optional)"
)
@app_commands.choices(card_type=[
    app_commands.Choice(name="🔵 AGL", value="AGL"),
    app_commands.Choice(name="🟢 TEQ", value="TEQ"),
    app_commands.Choice(name="🟣 INT", value="INT"),
    app_commands.Choice(name="🔴 STR", value="STR"),
    app_commands.Choice(name="🟠 PHY", value="PHY"),
])
async def team_builder(interaction: discord.Interaction, leader: str, card_type: str = None):
    await interaction.response.defer()

    if not db_exists():
        return await interaction.followup.send("❌ Database is empty! Run `python sync.py` first.", ephemeral=True)

    # Find leader card
    leader_results = db_search(leader, card_type=card_type, limit=5)
    if not leader_results:
        return await interaction.followup.send(f"❌ No card found for **{leader}**.", ephemeral=True)

    leader_card = leader_results[0]
    leader_title = leader_card["title"] or leader_card["page_title"]
    leader_type  = clean_type(leader_card["type"] or "")
    leader_rarity = get_rarity(leader_card["rarity"] or "")

    # Extract categories from leader skill
    leader_skill = leader_card["leader_skill"] or ""
    categories = extract_leader_category(leader_skill)

    if not categories:
        return await interaction.followup.send(
            f"❌ Couldn't extract a category from **{leader_title}**'s leader skill.\n"
            f"Leader skill: *{leader_skill[:200]}*",
            ephemeral=True
        )

    # Find all cards in those categories
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    category_filter = " OR ".join(["categories LIKE ?" for _ in categories])
    params = [f"%{cat}%" for cat in categories]

    if card_type:
        query = f"SELECT * FROM cards WHERE ({category_filter}) AND type = ? AND links != '' AND links IS NOT NULL"
        params.append(card_type.upper())
    else:
        query = f"SELECT * FROM cards WHERE ({category_filter}) AND links != '' AND links IS NOT NULL"

    pool = c.execute(query, params).fetchall()
    conn.close()

    # Filter out URs with LR versions
    lr_names = set()
    for card in pool:
        if card["rarity"] == "LR" and card["name"]:
            lr_names.add(card["name"].strip().lower())

    pool = [
        card for card in pool
        if not (card["rarity"] == "UR" and card["name"] and card["name"].strip().lower() in lr_names)
    ]

    if len(pool) < 2:
        return await interaction.followup.send(
            f"❌ Not enough cards found in categories: **{', '.join(categories)}**. Try without a type filter.",
            ephemeral=True
        )

    # Build the team
    team, honorable = build_best_team(leader_card, pool)

    # Calculate full team link coverage
    all_links = []
    for member in [leader_card] + list(team):
        if member["links"]:
            for l in member["links"].replace("|", " - ").split(" - "):
                l = l.strip()
                if l and l not in all_links:
                    all_links.append(l)

    # Build embed
    color = TYPE_COLORS.get(leader_type, discord.Color.blurple())
    t_emoji = TYPE_EMOJIS.get(leader_type, "⚪")
    r_emoji = RARITY_EMOJIS.get(leader_rarity, "⭐")

    embed = discord.Embed(
        title=f"👑 Team Builder — {leader_title}",
        description=(
            f"**Leader:** {r_emoji} {t_emoji} {leader_title}\n"
            f"**Leader Skill:** {leader_skill[:200]}\n"
            f"**Categories matched:** {' • '.join(categories)}"
        ),
        color=color
    )

    if leader_card["image"]:
        embed.set_thumbnail(url=leader_card["image"])

    embed.add_field(name="\u200b", value="**━━━━━━ Recommended Team ━━━━━━**", inline=False)

    for i, member in enumerate(team, 1):
        m_title  = member["title"] or member["page_title"]
        m_name   = member["name"] or ""
        m_type   = clean_type(member["type"] or "")
        m_rarity = get_rarity(member["rarity"] or "")
        m_links  = [l.strip() for l in (member["links"] or "").replace("|", " - ").split(" - ") if l.strip()]
        leader_links = [l.strip() for l in (leader_card["links"] or "").replace("|", " - ").split(" - ") if l.strip()]
        shared_with_leader = [l for l in m_links if l in leader_links]

        mt_emoji = TYPE_EMOJIS.get(m_type, "⚪")
        mr_emoji = RARITY_EMOJIS.get(m_rarity, "⭐")

        display = m_title
        if m_name and m_name != m_title:
            display += f" *({m_name})*"

        embed.add_field(
            name=f"**{i}.** {mr_emoji} {mt_emoji} {display}",
            value=(
                f"🔗 {len(shared_with_leader)} links with leader"
                + (f": {' • '.join(shared_with_leader[:4])}" if shared_with_leader else "")
                + f"\n[Wiki]({member['wiki_url']})"
            ),
            inline=False
        )

    if honorable:
        embed.add_field(name="\u200b", value="**━━━━━━ Honorable Mentions ━━━━━━**", inline=False)
        for card in honorable:
            h_title  = card["title"] or card["page_title"]
            h_type   = clean_type(card["type"] or "")
            h_rarity = get_rarity(card["rarity"] or "")
            ht_emoji = TYPE_EMOJIS.get(h_type, "⚪")
            hr_emoji = RARITY_EMOJIS.get(h_rarity, "⭐")
            embed.add_field(
                name=f"{hr_emoji} {ht_emoji} {h_title}",
                value=f"[View on Wiki]({card['wiki_url']})",
                inline=True
            )

    embed.set_footer(text=f"Team built from {len(pool)} eligible cards • Dokkan Battle Wiki")
    await interaction.followup.send(embed=embed)

# ======================
# COMMUNITY TEAM HELPERS
# ======================
def can_delete(interaction: discord.Interaction, submission_user_id: str) -> bool:
    """Check if user can delete a submission"""
    # Super admin can delete anything
    if interaction.user.id == SUPER_ADMIN_ID:
        return True
    # Original submitter can delete their own
    if str(interaction.user.id) == submission_user_id:
        return True
    # Mod role can delete in their server
    if interaction.guild:
        roles = [r.name for r in interaction.user.roles]
        if MOD_ROLE_NAME in roles:
            return True
    return False

# ======================
# /submitteam
# ======================
@bot.tree.command(name="submitteam", description="Submit your team for a challenge event")
@app_commands.describe(
    event="The challenge event you cleared",
    stage="The specific stage (e.g. Stage 1-10, Final Stage, Floor 50)",
    leader="Your leader card (slot 1)",
    card2="Team slot 2",
    card3="Team slot 3",
    card4="Team slot 4",
    card5="Team slot 5",
    card6="Team slot 6",
    friend_unit="Your friend/support unit (optional)",
    description="Tips or notes about your team (optional)"
)
async def submit_team(
    interaction: discord.Interaction,
    event: str,
    leader: str,
    card2: str,
    card3: str,
    card4: str,
    card5: str,
    card6: str,
    stage: str = None,
    friend_unit: str = None,
    description: str = None
):
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO community_teams
        (user_id, username, server_id, server_name, event, stage, leader, card2, card3, card4, card5, card6, friend_unit, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(interaction.user.id),
        str(interaction.user),
        str(interaction.guild.id) if interaction.guild else None,
        interaction.guild.name if interaction.guild else "DM",
        event,
        stage,
        leader, card2, card3, card4, card5, card6,
        friend_unit,
        description
    ))
    sub_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Look up card URLs for confirmation
    slots = [leader, card2, card3, card4, card5, card6]
    team_lines = []
    labels = ["👑 Leader", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
    for label, slot in zip(labels, slots):
        url, found_title, rarity = find_card_url(slot)
        r_emoji = RARITY_EMOJIS.get(get_rarity(rarity or ""), "⭐") if rarity else "⭐"
        if url:
            team_lines.append(f"{label} {r_emoji} [{found_title}]({url})")
        else:
            team_lines.append(f"{label} {slot}")

    if friend_unit:
        url, found_title, rarity = find_card_url(friend_unit)
        r_emoji = RARITY_EMOJIS.get(get_rarity(rarity or ""), "⭐") if rarity else "⭐"
        if url:
            team_lines.append(f"🤝 Friend: {r_emoji} [{found_title}]({url})")
        else:
            team_lines.append(f"🤝 Friend: {friend_unit}")

    event_display = f"**{event}**" + (f" — *{stage}*" if stage else "")
    embed = discord.Embed(
        title="✅ Team Submitted!",
        description=f"📌 {event_display}\n\n" + "\n".join(team_lines),
        color=discord.Color.green()
    )
    if description:
        embed.add_field(name="💬 Notes", value=description, inline=False)
    embed.add_field(name="🆔 Submission ID", value=f"`#{sub_id:04d}`", inline=True)
    embed.add_field(name="🗑️ Delete", value=f"`/deleteteam id:{sub_id}`", inline=True)
    embed.set_footer(text="Your team is now visible to all servers globally!")
    await interaction.followup.send(embed=embed, ephemeral=True)

    # Post to mod log channel
    try:
        log_channel = bot.get_channel(TEAM_LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(
                title=f"📋 New Team Submission — #{sub_id:04d}",
                description=f"📌 {event_display}\n\n" + "\n".join(team_lines),
                color=discord.Color.blurple()
            )
            if description:
                log_embed.add_field(name="💬 Notes", value=description, inline=False)
            log_embed.add_field(name="👤 Submitted by", value=f"{interaction.user} (`{interaction.user.id}`)", inline=True)
            log_embed.add_field(name="🖥️ Server", value=interaction.guild.name if interaction.guild else "DM", inline=True)
            log_embed.add_field(name="🗑️ Delete", value=f"`/deleteteam id:{sub_id}`", inline=False)
            log_embed.set_footer(text="To remove this team, use /deleteteam — bot will notify the user.")
            await log_channel.send(embed=log_embed)
    except Exception as e:
        print(f"⚠️  Failed to post to log channel: {e}")

@submit_team.autocomplete("event")
async def event_autocomplete(interaction: discord.Interaction, current: str):
    try:
        return [
            app_commands.Choice(name=event, value=event)
            for event in CHALLENGE_EVENTS
            if current.lower() in event.lower()
        ][:25]
    except Exception as e:
        print(f"Event autocomplete error: {e}")
        return []

async def card_slot_autocomplete(interaction: discord.Interaction, current: str):
    """Shared autocomplete for all card slots — searches DB by title"""
    if len(current) < 2:
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        like = f"%{current}%"
        results = conn.execute("""
            SELECT title, name, rarity FROM cards
            WHERE title LIKE ? OR name LIKE ?
            ORDER BY CASE rarity
                WHEN 'LR' THEN 1 WHEN 'UR' THEN 2 WHEN 'SSR' THEN 3 ELSE 4
            END
            LIMIT 50
        """, (like, like)).fetchall()
        conn.close()

        # Filter out URs that have LR versions
        lr_names = set()
        for r in results:
            if r["rarity"] == "LR" and r["name"]:
                lr_names.add(r["name"].strip().lower())

        choices = []
        for r in results:
            if r["rarity"] == "UR" and r["name"] and r["name"].strip().lower() in lr_names:
                continue
            title = r["title"] or r["name"] or ""
            if not title:
                continue
            label = f"[{r['rarity']}] {title}"[:100]
            choices.append(app_commands.Choice(name=label, value=title))
            if len(choices) >= 25:
                break

        return choices
    except Exception as e:
        print(f"Autocomplete error: {e}")
        return []

@submit_team.autocomplete("leader")
async def leader_autocomplete(interaction: discord.Interaction, current: str):
    return await card_slot_autocomplete(interaction, current)

@submit_team.autocomplete("card2")
async def card2_autocomplete(interaction: discord.Interaction, current: str):
    return await card_slot_autocomplete(interaction, current)

@submit_team.autocomplete("card3")
async def card3_autocomplete(interaction: discord.Interaction, current: str):
    return await card_slot_autocomplete(interaction, current)

@submit_team.autocomplete("card4")
async def card4_autocomplete(interaction: discord.Interaction, current: str):
    return await card_slot_autocomplete(interaction, current)

@submit_team.autocomplete("card5")
async def card5_autocomplete(interaction: discord.Interaction, current: str):
    return await card_slot_autocomplete(interaction, current)

@submit_team.autocomplete("card6")
async def card6_autocomplete(interaction: discord.Interaction, current: str):
    return await card_slot_autocomplete(interaction, current)

@submit_team.autocomplete("friend_unit")
async def friend_autocomplete(interaction: discord.Interaction, current: str):
    return await card_slot_autocomplete(interaction, current)

# ======================
# /communityteams
# ======================
@bot.tree.command(name="communityteams", description="Browse community submitted teams for challenge events")
@app_commands.describe(
    event="Filter by challenge event",
    page="Page number (default 1)"
)
async def community_teams(interaction: discord.Interaction, event: str = None, page: int = 1):
    await interaction.response.defer()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    per_page = 3
    offset = (page - 1) * per_page

    if event:
        total = conn.execute("SELECT COUNT(*) FROM community_teams WHERE event LIKE ?", (f"%{event}%",)).fetchone()[0]
        rows = conn.execute("""
            SELECT * FROM community_teams WHERE event LIKE ?
            ORDER BY submitted_at DESC LIMIT ? OFFSET ?
        """, (f"%{event}%", per_page, offset)).fetchall()
    else:
        total = conn.execute("SELECT COUNT(*) FROM community_teams").fetchone()[0]
        rows = conn.execute("""
            SELECT * FROM community_teams
            ORDER BY submitted_at DESC LIMIT ? OFFSET ?
        """, (per_page, offset)).fetchall()
    conn.close()

    if not rows:
        return await interaction.followup.send(
            f"❌ No teams found{f' for **{event}**' if event else ''}. Be the first to submit with `/submitteam`!",
            ephemeral=True
        )

    total_pages = max(1, (total + per_page - 1) // per_page)

    embed = discord.Embed(
        title=f"🌍 Community Teams{f' — {event}' if event else ''}",
        description=f"Page {page}/{total_pages}  •  {total} total submissions",
        color=discord.Color.blurple()
    )

    for row in rows:
        event_display = row['event'] + (f" — {row['stage']}" if row["stage"] else "")
        embed.add_field(name=f"📌 {event_display}", value="\u200b", inline=False)
        leader_image = build_team_fields(embed, row, show_footer=True)
        if leader_image and not embed.thumbnail.url:
            embed.set_thumbnail(url=leader_image)

    embed.set_footer(text=f"Use /communityteams page:{page+1} for more  •  Submit yours with /submitteam")
    await interaction.followup.send(embed=embed)

@community_teams.autocomplete("event")
async def community_event_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=event, value=event)
        for event in CHALLENGE_EVENTS
        if current.lower() in event.lower()
    ][:25]

# ======================
# /deleteteam
# ======================
@bot.tree.command(name="deleteteam", description="Delete a community team submission")
@app_commands.describe(
    id="The submission ID to delete (e.g. 0042)",
    reason="Reason for deletion (required when removing someone else's team)"
)
async def delete_team(interaction: discord.Interaction, id: int, reason: str = None):
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM community_teams WHERE id = ?", (id,)).fetchone()

    if not row:
        conn.close()
        return await interaction.followup.send(f"❌ No submission found with ID `#{id:04d}`.", ephemeral=True)

    if not can_delete(interaction, row["user_id"]):
        conn.close()
        return await interaction.followup.send(
            "❌ You can only delete your own submissions. Mods with the `Dokkan Mod` role can delete any submission.",
            ephemeral=True
        )

    # If mod/admin is deleting someone else's team, require a reason
    is_own = str(interaction.user.id) == row["user_id"]
    if not is_own and not reason:
        conn.close()
        return await interaction.followup.send(
            "❌ You must provide a **reason** when deleting someone else's team.\nUsage: `/deleteteam id:{} reason:Your reason here`".format(id),
            ephemeral=True
        )

    conn.execute("DELETE FROM community_teams WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    await interaction.followup.send(
        f"✅ Submission `#{id:04d}` (**{row['event']}** by {row['username']}) has been deleted.",
        ephemeral=True
    )

    # DM the user if someone else deleted their team
    if not is_own:
        try:
            user = await bot.fetch_user(int(row["user_id"]))
            dm_embed = discord.Embed(
                title="🗑️ Your Team Submission Was Removed",
                color=discord.Color.red()
            )
            dm_embed.add_field(name="📌 Event", value=row["event"], inline=True)
            dm_embed.add_field(name="🆔 Submission", value=f"`#{id:04d}`", inline=True)
            dm_embed.add_field(name="📝 Reason", value=reason, inline=False)
            dm_embed.add_field(name="🛡️ Removed by", value=str(interaction.user), inline=False)
            dm_embed.set_footer(text="You can resubmit with /submitteam if you believe this was a mistake.")
            await user.send(embed=dm_embed)
        except Exception as e:
            print(f"⚠️  Could not DM user {row['user_id']}: {e}")

def truncate(text: str, length: int = 40) -> str:
    return text if len(text) <= length else text[:length - 1] + "…"

def build_team_fields(embed, row, show_footer=True):
    """Add team cards as individual fields to embed, each card on its own line as a link"""
    slots  = [row["leader"], row["card2"], row["card3"], row["card4"], row["card5"], row["card6"]]
    labels = ["👑", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣"]
    leader_image = None

    for i, (label, slot) in enumerate(zip(labels, slots)):
        if not slot:
            continue
        url, found_title, rarity = find_card_url(slot)
        r_emoji = RARITY_EMOJIS.get(get_rarity(rarity or ""), "⭐") if rarity else "⭐"

        conn2 = sqlite3.connect(DB_PATH)
        conn2.row_factory = sqlite3.Row
        like = f"%{slot}%"
        card_row = conn2.execute(
            "SELECT type, image FROM cards WHERE name LIKE ? OR title LIKE ? ORDER BY CASE rarity WHEN 'LR' THEN 1 WHEN 'UR' THEN 2 ELSE 3 END LIMIT 1",
            (like, like)
        ).fetchone()
        conn2.close()

        t_emoji = TYPE_EMOJIS.get(clean_type(card_row["type"] or ""), "") if card_row else ""
        if i == 0 and card_row and card_row["image"]:
            leader_image = card_row["image"]

        short_title = truncate(found_title or slot, 45)
        value = f"[{short_title}]({url})" if url else short_title
        embed.add_field(name=f"{label} {r_emoji} {t_emoji}", value=value, inline=True)

    if row["friend_unit"]:
        url, found_title, rarity = find_card_url(row["friend_unit"])
        r_emoji = RARITY_EMOJIS.get(get_rarity(rarity or ""), "⭐") if rarity else "⭐"
        short_title = truncate(found_title or row["friend_unit"], 45)
        value = f"[{short_title}]({url})" if url else short_title
        embed.add_field(name=f"🤝 {r_emoji}", value=value, inline=True)

    if row["description"]:
        embed.add_field(name="💬 Notes", value=row["description"], inline=False)

    if show_footer:
        embed.add_field(
            name="\u200b",
            value=f"🆔 `#{row['id']:04d}`  •  👤 {row['username']}  •  🖥️ {row['server_name'] or 'Unknown'}",
            inline=False
        )

    return leader_image

# ======================
# /myteams
# ======================
@bot.tree.command(name="myteams", description="View your own community team submissions")
async def my_teams(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT * FROM community_teams WHERE user_id = ?
        ORDER BY submitted_at DESC LIMIT 10
    """, (str(interaction.user.id),)).fetchall()
    conn.close()

    if not rows:
        return await interaction.followup.send(
            "❌ You haven't submitted any teams yet! Use `/submitteam` to get started.",
            ephemeral=True
        )

    embed = discord.Embed(
        title="📋 Your Team Submissions",
        description=f"{len(rows)} submission(s)",
        color=discord.Color.blurple()
    )

    for row in rows:
        event_display = row['event'] + (f" — {row['stage']}" if row["stage"] else "")
        embed.add_field(name=f"📌 {event_display}  •  `#{row['id']:04d}`", value="\u200b", inline=False)
        leader_image = build_team_fields(embed, row, show_footer=False)
        embed.add_field(name="\u200b", value=f"🗑️ `/deleteteam id:{row['id']}`", inline=False)
        if leader_image and not embed.thumbnail:
            embed.set_thumbnail(url=leader_image)

    embed.set_footer(text="Only you can see this • Use /deleteteam to remove a submission")
    await interaction.followup.send(embed=embed, ephemeral=True)

# ======================
# /upcoming
# ======================
@bot.tree.command(name="upcoming", description="Show upcoming cards scheduled to release in Dokkan Battle")
@app_commands.describe(
    filter="Filter by type or rarity (optional)",
    page="Page number (default 1)"
)
async def upcoming_cards(interaction: discord.Interaction, filter: str = None, page: int = 1):
    await interaction.response.defer()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Check if schedule table exists and has data
    try:
        total = conn.execute("SELECT COUNT(*) FROM schedule").fetchone()[0]
    except Exception:
        conn.close()
        return await interaction.followup.send(
            "❌ Schedule table not found. Run `python sync.py` to populate it.",
            ephemeral=True
        )

    if total == 0:
        conn.close()
        return await interaction.followup.send(
            "❌ No upcoming cards found. Run `python sync.py` to sync the schedule.",
            ephemeral=True
        )

    per_page = 8
    offset = (page - 1) * per_page

    if filter:
        f = filter.upper()
        rows = conn.execute("""
            SELECT * FROM schedule WHERE type = ? OR rarity = ?
            LIMIT ? OFFSET ?
        """, (f, f, per_page, offset)).fetchall()
        count = conn.execute("SELECT COUNT(*) FROM schedule WHERE type = ? OR rarity = ?", (f, f)).fetchone()[0]
    else:
        rows = conn.execute("SELECT * FROM schedule LIMIT ? OFFSET ?", (per_page, offset)).fetchall()
        count = total

    synced_at = conn.execute("SELECT synced_at FROM schedule ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()

    if not rows:
        return await interaction.followup.send(
            f"❌ No upcoming cards found{f' for **{filter}**' if filter else ''}.",
            ephemeral=True
        )

    total_pages = max(1, (count + per_page - 1) // per_page)
    last_sync = synced_at["synced_at"][:16].replace("T", " ") if synced_at else "Unknown"

    embed = discord.Embed(
        title="📅 Upcoming Dokkan Cards",
        description=f"Page {page}/{total_pages}  •  {count} cards  •  Last synced: {last_sync} UTC",
        color=discord.Color.gold()
    )

    for row in rows:
        title    = row["title"] or "Unknown"
        name     = row["name"] or ""
        rarity   = get_rarity(row["rarity"] or "")
        type_    = clean_type(row["type"] or "")
        r_emoji  = RARITY_EMOJIS.get(rarity, "⭐")
        t_emoji  = TYPE_EMOJIS.get(type_, "")
        wiki_url = row["wiki_url"] or ""

        display = f"[{title}]({wiki_url})" if wiki_url else title
        if name and name != title:
            display += f"\n*{name}*"

        embed.add_field(
            name=f"{r_emoji} {t_emoji} {rarity or '?'}",
            value=display,
            inline=True
        )

        if row["image"] and not embed.thumbnail:
            embed.set_thumbnail(url=row["image"])

    embed.set_footer(text=f"Run python sync.py to refresh • Use /upcoming page:2 for more")
    await interaction.followup.send(embed=embed)

# ======================
# /ezainfo
# ======================
@bot.tree.command(name="ezainfo", description="Look up a card's Extreme Z-Awakening info")
@app_commands.describe(
    name="Card name to look up EZA info for",
    card_type="Filter by type (optional)",
)
@app_commands.choices(card_type=[
    app_commands.Choice(name="🔵 AGL", value="AGL"),
    app_commands.Choice(name="🟢 TEQ", value="TEQ"),
    app_commands.Choice(name="🟣 INT", value="INT"),
    app_commands.Choice(name="🔴 STR", value="STR"),
    app_commands.Choice(name="🟠 PHY", value="PHY"),
])
async def eza_info(interaction: discord.Interaction, name: str, card_type: str = None):
    await interaction.response.defer()

    if not db_exists():
        return await interaction.followup.send("❌ Database is empty! Run `python sync.py` first.", ephemeral=True)

    results = db_search(name, card_type=card_type, limit=5)
    if not results:
        return await interaction.followup.send(f"❌ No card found for **{name}**.", ephemeral=True)

    card = results[0]

    # Check if EZA data exists
    has_eza = any([
        card["eza_leader_skill"], card["eza_super_attack"],
        card["eza_passive_skill"], card["eza_max_hp"]
    ])

    title   = card["title"] or card["page_title"]
    name_   = card["name"] or ""
    type_   = clean_type(card["type"] or "")
    rarity  = get_rarity(card["rarity"] or "")
    t_emoji = TYPE_EMOJIS.get(type_, "⚪")
    r_emoji = RARITY_EMOJIS.get(rarity, "⭐")
    color   = TYPE_COLORS.get(type_, discord.Color.gold())

    if not has_eza:
        embed = discord.Embed(
            title=f"⚡ EZA Info — {title}",
            description=f"{r_emoji} {t_emoji} *{name_}*\n\n❌ No EZA data found for this card.\nThis card may not have an EZA yet, or run `python sync.py` to refresh.",
            color=color
        )
        if card["image"]:
            embed.set_thumbnail(url=card["image"])
        embed.set_footer(text="Dokkan Battle Wiki")
        return await interaction.followup.send(embed=embed)

    embed = discord.Embed(
        title=f"⚡ {title}",
        url=card['wiki_url'],
        description=f"{r_emoji} {t_emoji} *{name_}*",
        color=color
    )

    if card["image"]:
        embed.set_thumbnail(url=card["image"])

    # Stats — show max stats with note that EZA boosts them further
    # Wiki doesn't store separate EZA stats, so show current max stats
    if card["max_hp"] or card["max_atk"] or card["max_def"]:
        stats = (
            f"**HP:** {card['max_hp'] or '?'}\n"
            f"**ATK:** {card['max_atk'] or '?'}\n"
            f"**DEF:** {card['max_def'] or '?'}"
        )
        embed.add_field(name="💪 Max Stats (Post-EZA)", value=stats, inline=False)

    # Leader skill
    if card["eza_leader_skill"]:
        ls = clean_wiki(card["eza_leader_skill"])[:500]
        embed.add_field(name="👑 EZA Leader Skill", value=f"```{ls}```", inline=False)
    elif card["leader_skill"]:
        ls = clean_wiki(card["leader_skill"])[:300]
        embed.add_field(name="👑 Leader Skill (unchanged)", value=f"```{ls}```", inline=False)

    # Super attack
    if card["eza_super_attack"]:
        sa_name = card["eza_sa_name"] or card["sa_name"] or ""
        sa = clean_wiki(card["eza_super_attack"])[:300]
        embed.add_field(
            name=f"⚡ EZA Super Attack" + (f" — *{sa_name}*" if sa_name else ""),
            value=f"```{sa}```",
            inline=False
        )

    # Passive skill — clean wiki markup
    if card["eza_passive_skill"]:
        ps = clean_wiki(card["eza_passive_skill"])[:1024]
        embed.add_field(name="✨ EZA Passive Skill", value=ps, inline=False)

    embed.set_footer(text="Dokkan Battle Wiki • Click the title to view full card page")
    await interaction.followup.send(embed=embed)


# ======================
# /summon
# ======================
SUMMON_RATES = [
    ("SSR", 0.17),
    ("SR",  0.33),
    ("R",   0.35),
    ("N",   0.15),
]

SUMMON_COLORS = {
    "LR":  discord.Color.from_rgb(255, 50, 50),
    "SSR": discord.Color.gold(),
    "SR":  discord.Color.purple(),
    "R":   discord.Color.blue(),
    "N":   discord.Color.light_grey(),
}

SUMMON_SPARKLE = {
    "LR":  "🌟",
    "SSR": "✨",
    "SR":  "💜",
    "R":   "💙",
    "N":   "⬜",
}

# Summon animations — normal vs special (LR guaranteed)
NORMAL_ANIMATIONS = [
    "https://media.giphy.com/media/sBaf2rkZufvEbjXmPv/giphy.gif",
    "https://media.giphy.com/media/HKzR1H7nsH6YV0xeRs/giphy.gif",
    "https://media.giphy.com/media/rsDpYA4jSUt1e/giphy.gif",
]

SPECIAL_ANIMATIONS = [
    "https://media.giphy.com/media/FvUaNoQTphyuFhbuw6/giphy.gif",
    "https://media.giphy.com/media/dtIuRQqjejKcvoZzXa/giphy.gif",
    "https://media.giphy.com/media/eJ1U3jkPwvnGTcTiRz/giphy.gif",
]

def pull_card(conn, rarity: str):
    conn.row_factory = sqlite3.Row
    row = conn.execute("""
        SELECT title, name, type, rarity, image, wiki_url FROM cards
        WHERE rarity = ? AND title IS NOT NULL
        ORDER BY RANDOM() LIMIT 1
    """, (rarity,)).fetchone()
    return row

def weighted_rarity() -> str:
    import random
    roll = random.random()
    cumulative = 0
    for rarity, rate in SUMMON_RATES:
        cumulative += rate
        if roll <= cumulative:
            return rarity
    return "N"

def build_single_result(rarity, card) -> discord.Embed:
    sparkle = SUMMON_SPARKLE[rarity]
    color   = SUMMON_COLORS[rarity]
    title   = card["title"] if card else "Unknown Card"
    name_   = card["name"] if card else ""
    t_emoji = TYPE_EMOJIS.get(clean_type(card["type"] or ""), "") if card else ""
    r_emoji = RARITY_EMOJIS.get(rarity, "⭐")
    wiki_url = card["wiki_url"] if card else None

    embed = discord.Embed(
        title=f"{sparkle} You pulled a {rarity}!",
        description=f"{r_emoji} {t_emoji} [{title}]({wiki_url})\n*{name_}*" if wiki_url else f"{r_emoji} {t_emoji} {title}\n*{name_}*",
        color=color
    )
    if card and card["image"]:
        embed.set_image(url=card["image"])
    embed.set_footer(text="Use /summon type:Multi for 10 pulls!")
    return embed

def build_multi_result(results) -> discord.Embed:
    ssr_count = sum(1 for r, _ in results if r == "SSR")
    color = discord.Color.gold() if ssr_count > 0 else discord.Color.blurple()
    lines = []
    for rarity, card in results:
        sparkle = SUMMON_SPARKLE[rarity]
        r_emoji = RARITY_EMOJIS.get(rarity, "⭐")
        t_emoji = TYPE_EMOJIS.get(clean_type(card["type"] or ""), "") if card else ""
        title   = card["title"] if card else "Unknown Card"
        wiki_url = card["wiki_url"] if card else None
        if wiki_url:
            lines.append(f"{sparkle} {r_emoji} {t_emoji} [{title}]({wiki_url})")
        else:
            lines.append(f"{sparkle} {r_emoji} {t_emoji} {title}")

    summary = f"✨ **{ssr_count} SSR{'s' if ssr_count != 1 else ''}** pulled!" if ssr_count > 0 else "😢 No SSRs this time..."
    embed = discord.Embed(
        title="🎲 Multi Summon Results!",
        description=summary + "\n\n" + "\n".join(lines),
        color=color
    )
    best = next(((r, c) for r, c in results if r == "SSR" and c and c["image"]), None)
    if not best:
        best = next(((r, c) for r, c in results if c and c["image"]), None)
    if best:
        embed.set_thumbnail(url=best[1]["image"])
    embed.set_footer(text="Use /summon type:Single for a single pull!")
    return embed

@bot.tree.command(name="summon", description="Simulate a Dokkan Battle summon!")
@app_commands.describe(type="Single pull or Multi (10 pulls)")
@app_commands.choices(type=[
    app_commands.Choice(name="Single (1 pull)", value="single"),
    app_commands.Choice(name="Multi (10 pulls)", value="multi"),
])
async def summon(interaction: discord.Interaction, type: str = "single"):
    import random, asyncio

    # 1 in 10 chance of special LR animation
    is_special = random.random() < 0.10

    # Step 1: Show animation
    if is_special:
        anim_url = random.choice(SPECIAL_ANIMATIONS)
        loading_embed = discord.Embed(
            title="🌟 A Special Summon Appears!",
            description="✨ *The Dragon Balls are glowing brighter than usual...* ✨",
            color=discord.Color.gold()
        )
    else:
        anim_url = random.choice(NORMAL_ANIMATIONS)
        loading_embed = discord.Embed(
            title="🔮 Summoning...",
            description="The Dragon Balls are gathering...",
            color=discord.Color.dark_gold()
        )
    loading_embed.set_image(url=anim_url)
    await interaction.response.send_message(embed=loading_embed)

    # Step 2: Roll results
    conn = sqlite3.connect(DB_PATH)
    pulls = 1 if type == "single" else 10
    results = []

    if is_special:
        # Guaranteed LR on first pull, rest are normal
        lr_card = pull_card(conn, "LR")
        if lr_card:
            results.append(("LR", lr_card))
        else:
            results.append((weighted_rarity(), pull_card(conn, weighted_rarity())))
        for _ in range(pulls - 1):
            rarity = weighted_rarity()
            results.append((rarity, pull_card(conn, rarity)))
    else:
        for _ in range(pulls):
            rarity = weighted_rarity()
            results.append((rarity, pull_card(conn, rarity)))
    conn.close()

    # Step 3: Dramatic pause — longer for special
    await asyncio.sleep(3.5 if is_special else 2.5)

    # Step 4: Reveal
    if type == "single":
        result_embed = build_single_result(*results[0])
        if is_special:
            result_embed.title = f"🌟 SPECIAL SUMMON! {result_embed.title}"
    else:
        result_embed = build_multi_result(results)
        if is_special:
            result_embed.title = "🌟 SPECIAL MULTI SUMMON!"
            result_embed.description = "⚡ **Guaranteed LR!**\n\n" + (result_embed.description or "")

    await interaction.edit_original_response(embed=result_embed)

# ======================
# /festgoat
# ======================

# Replace these with the real YouTube playlist/video links
FESTGOAT_Channels = [
    ("WingmanDBZ", "https://www.youtube.com/@wingmandbz", "https://youtube.com/playlist?list=PL2SPEf2wCeiKhil4JoSYiiRnk1RTU_h-k&si=eBVlfleUPXcNQRaJ"),
    ("DatruthDT", "https://www.youtube.com/@DaTruthDT", "https://youtube.com/playlist?list=PLxDKxnBDRDIW7uTowgNdBrxrij_Wwqaen&si=U3J7W-1BHXfLVx4P"),
    ("Dokkan World", "https://www.youtube.com/@dokkanworld", "https://youtube.com/playlist?list=PLzQ1mduP0EgH0i91ByEY42an3gPFZPIoQ&si=3qc7AJ-7PQJqrXfk"),
    ("ErrdaySAMA", "https://www.youtube.com/@ErrdaySAMA", "https://youtube.com/playlist?list=PLvhfViQpWFVywC2TikRTowiGt87S1JF-P&si=ZwEr3_86K6i2ig32"),
]

@bot.tree.command(name="festgoat", description="Check out the best Festival of Battles content!")
async def fest_goat(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🐐 Festival of Battles GOAT",
        description="The best FOB content on YouTube — check these out!",
        color=discord.Color.red()
    )
    for name, channel, playlist in FESTGOAT_Channels:
        embed.add_field(
            name=f"🎬 {name}",
            value=f"[📺 Channel]({channel})  •  [▶️ FoB Playlist]({playlist})",
            inline=False
        )
    embed.set_footer(text="Subscribe for the best Dokkan Battle content!")
    await interaction.response.send_message(embed=embed)


# ======================
# /help
# ======================
@bot.tree.command(name="help", description="See all Dokkan Nexus commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📖 Dokkan Nexus — Command List",
        description="Your complete Dokkan Battle hub. Here's everything you can do:",
        color=discord.Color.from_rgb(255, 140, 0)
    )

    embed.add_field(
        name="🔍 Card Lookup",
        value=(
            "`/card` — Search for a card by name\n"
            "`/cardurl` — Look up a card by its wiki URL\n"
            "`/ezainfo` — View a card's EZA leader skill, super attack & passive\n"
            "`/upcoming` — See upcoming cards coming to the game"
        ),
        inline=False
    )

    embed.add_field(
        name="👥 Team Building",
        value=(
            "`/team` — Auto-build a team around a leader card\n"
            "`/links` — Find the best linking partners for a card"
        ),
        inline=False
    )

    embed.add_field(
        name="🌍 Community Teams",
        value=(
            "`/submitteam` — Submit a team for an event\n"
            "`/communityteams` — Browse global team submissions\n"
            "`/myteams` — View your own submissions\n"
            "`/deleteteam` — Delete one of your submissions"
        ),
        inline=False
    )

    embed.add_field(
        name="🎲 Fun",
        value=(
            "`/summon` — Simulate a Dokkan summon (single or multi)\n"
            "`/festgoat` — Best Festival of Battles content on YouTube"
        ),
        inline=False
    )

    embed.add_field(
        name="📊 Info",
        value=(
            "`/dbstats` — View database stats and last sync time\n"
            "`/ping` — Check bot latency and status\n"
            "`/invite` — Add Dokkan Nexus to your server\n"
            "`/help` — Show this command list"
        ),
        inline=False
    )

    embed.add_field(
        name="☕ Support Dokkan Nexus",
        value="Enjoying the bot? [Buy me a Ko-fi!](https://ko-fi.com/duskmatter/tiers) Your support keeps the bot running 🙏",
        inline=False
    )

    embed.set_footer(text="Dokkan Nexus • Not affiliated with Bandai Namco or Akatsuki Inc.")
    await interaction.response.send_message(embed=embed)

# ======================
# /ping
# ======================
@bot.tree.command(name="ping", description="Check if Dokkan Nexus is online and responsive")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    if latency < 100:
        status = "🟢 Excellent"
        color = discord.Color.green()
    elif latency < 200:
        status = "🟡 Good"
        color = discord.Color.yellow()
    else:
        status = "🔴 Slow"
        color = discord.Color.red()

    embed = discord.Embed(
        title="🏓 Pong!",
        color=color
    )
    embed.add_field(name="Latency", value=f"`{latency}ms`", inline=True)
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Servers", value=f"`{len(bot.guilds)}`", inline=True)
    embed.set_footer(text="Dokkan Nexus is online!")
    await interaction.response.send_message(embed=embed)

# ======================
# /invite
# ======================
@bot.tree.command(name="invite", description="Add Dokkan Nexus to your server!")
async def invite(interaction: discord.Interaction):
    embed = discord.Embed(
        title="➕ Add Dokkan Nexus to Your Server!",
        description="Bring the ultimate Dokkan Battle hub to your community!",
        color=discord.Color.from_rgb(255, 140, 0)
    )
    embed.add_field(name="🔗 Invite Link", value="[Click here to add Dokkan Nexus](https://bit.ly/DokkanNexus)", inline=False)
    embed.add_field(name="☕ Support", value="[Ko-fi — Help keep the bot running!](https://ko-fi.com/duskmatter/tiers)", inline=False)
    embed.set_footer(text="Dokkan Nexus • Your Dokkan Battle Hub")
    await interaction.response.send_message(embed=embed)

# ======================
# RUN
# ======================
bot.run(TOKEN)
