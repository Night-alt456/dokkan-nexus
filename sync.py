"""
sync.py — Dokkan Battle card database sync script
Run this once to populate the database, then schedule it daily.

Usage:
    python sync.py            # Full sync (all cards)
    python sync.py --update   # Only sync cards added/changed recently
"""

import aiohttp
import asyncio
import sqlite3
import re
import time
import argparse
from datetime import datetime, timedelta

# ======================
# CONFIG
# ======================
WIKI_API    = "https://dbz-dokkanbattle.fandom.com/api.php"
DB_PATH     = "dokkan.db"
BATCH_SIZE  = 50       # cards to fetch concurrently
DELAY       = 0.3      # seconds between batches to avoid rate limits

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# ======================
# DATABASE SETUP
# ======================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            page_title      TEXT UNIQUE NOT NULL,
            title           TEXT,
            name            TEXT,
            type            TEXT,
            rarity          TEXT,
            cost            TEXT,
            max_level       TEXT,
            base_hp         TEXT,
            base_atk        TEXT,
            base_def        TEXT,
            max_hp          TEXT,
            max_atk         TEXT,
            max_def         TEXT,
            leader_skill    TEXT,
            super_attack    TEXT,
            sa_name         TEXT,
            passive_skill   TEXT,
            links           TEXT,
            categories      TEXT,
            image           TEXT,
            wiki_url        TEXT,
            synced_at       TEXT,
            eza_leader_skill  TEXT,
            eza_super_attack  TEXT,
            eza_sa_name       TEXT,
            eza_passive_skill TEXT,
            eza_max_hp        TEXT,
            eza_max_atk       TEXT,
            eza_max_def       TEXT
        )
    """)
    # Add EZA columns to existing DBs
    for col in ["eza_leader_skill", "eza_super_attack", "eza_sa_name", "eza_passive_skill",
                "eza_max_hp", "eza_max_atk", "eza_max_def"]:
        try:
            c.execute(f"ALTER TABLE cards ADD COLUMN {col} TEXT")
        except Exception:
            pass
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_name ON cards(name);
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_title ON cards(title);
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_type ON cards(type);
    """)
    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_rarity ON cards(rarity);
    """)
    conn.commit()
    return conn

# ======================
# WIKI API HELPERS
# ======================
async def api_get(session: aiohttp.ClientSession, params: dict):
    params["format"] = "json"
    try:
        async with session.get(
            WIKI_API,
            params=params,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
    except Exception as e:
        print(f"  ❌ API error: {e}")
    return None

async def get_all_card_titles(session: aiohttp.ClientSession):
    """Get all card page titles from the wiki using category members"""
    print("📋 Fetching all card page titles from wiki...")
    titles = set()

    # Try multiple categories to get full coverage
    categories = [
        "Category:LR",
        "Category:UR",
        "Category:SSR",
        "Category:SR",
        "Category:R",
        "Category:N",
    ]

    for category in categories:
        print(f"  📂 Fetching {category}...")
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": category,
            "cmlimit": 500,
            "cmtype": "page"
        }

        while True:
            data = await api_get(session, params)
            if not data:
                break

            members = data.get("query", {}).get("categorymembers", [])
            for m in members:
                titles.add(m["title"])

            cont = data.get("continue", {}).get("cmcontinue")
            if cont:
                params["cmcontinue"] = cont
                await asyncio.sleep(0.2)
            else:
                break

        print(f"    ✅ {len(titles)} total so far")
        await asyncio.sleep(0.3)

    titles = list(titles)
    print(f"  ✅ Found {len(titles)} card pages total")
    return titles

async def get_wikitext(session: aiohttp.ClientSession, page_title: str):
    """Fetch raw wikitext for a page"""
    data = await api_get(session, {
        "action": "parse",
        "page": page_title,
        "prop": "wikitext",
        "formatversion": "2"
    })
    if not data:
        return None
    return data.get("parse", {}).get("wikitext", "")

# ======================
# WIKITEXT PARSER
# ======================
def clean_wiki(text: str) -> str:
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^>]*>.*?</ref>', '', text, flags=re.DOTALL)
    text = re.sub(r'<ref[^/]*/>', '', text)
    # Remove image links like [[30px|link=Category:PHY Ki Spheres]]
    text = re.sub(r'\[\[\d+px[^\]]*\]\]', '', text)
    # Remove File/Image embeds
    text = re.sub(r'\[\[(?:File|Image):[^\]]*\]\]', '', text, flags=re.IGNORECASE)
    # Remove nested templates
    while re.search(r'\{\{[^\{\}]*\}\}', text):
        text = re.sub(r'\{\{[^\{\}]*\}\}', '', text)
    # Convert wiki links [[link|text]] -> text, [[text]] -> text
    text = re.sub(r'\[\[[^\|\]]+\|([^\]]+)\]\]', r'\1', text)
    text = re.sub(r'\[\[([^\]]+)\]\]', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r"'''?", '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def extract_field(wikitext: str, *fields):
    for field in fields:
        pattern = rf'\|\s*{re.escape(field)}\s*=\s*(.*?)(?=\n\s*\||\n\s*\}}|\Z)'
        match = re.search(pattern, wikitext, re.IGNORECASE | re.DOTALL)
        if match:
            return clean_wiki(match.group(1))
    return None

def clean_type(raw: str) -> str:
    raw = raw.upper().strip()
    for t in ["AGL", "TEQ", "INT", "STR", "PHY"]:
        if raw.endswith(t):
            return t
    return raw[:3] if len(raw) >= 3 else raw

def parse_wikitext(wikitext: str, page_title: str):
    card = {"page_title": page_title}


    card["title"]  = extract_field(wikitext, "name1")
    card["name"]   = extract_field(wikitext, "name2")
    card["type"]   = clean_type(extract_field(wikitext, "type") or "")
    card["rarity"] = (extract_field(wikitext, "rarity") or "").upper()
    card["cost"]   = extract_field(wikitext, "cost")

    raw_max_lv = extract_field(wikitext, "max lv", "max_lv")
    if raw_max_lv and raw_max_lv.upper() == "LR":
        card["max_level"] = extract_field(wikitext, "lv_max", "lv max", "max_level") or "150"
        card["rarity"] = "LR"
    else:
        card["max_level"] = raw_max_lv

    card["base_hp"]  = extract_field(wikitext, "HP1", "hp1", "HP_1", "hp_1", "base HP", "Base HP", "HP base", "hp base", "HP")
    card["base_atk"] = extract_field(wikitext, "ATK1", "atk1", "ATK_1", "atk_1", "base ATK", "Base ATK", "ATK base", "atk base", "ATK")
    card["base_def"] = extract_field(wikitext, "DEF1", "def1", "DEF_1", "def_1", "base DEF", "Base DEF", "DEF base", "def base", "DEF")
    card["max_hp"]   = extract_field(wikitext, "HP_max", "hp_max", "HP2", "hp2", "max HP", "Max HP", "HP max", "hp max", "HP_lv120", "HP_lv150", "HP_lv200")
    card["max_atk"]  = extract_field(wikitext, "ATK_max", "atk_max", "ATK2", "atk2", "max ATK", "Max ATK", "ATK max", "atk max", "ATK_lv120", "ATK_lv150", "ATK_lv200")
    card["max_def"]  = extract_field(wikitext, "DEF_max", "def_max", "DEF2", "def2", "max DEF", "Max DEF", "DEF max", "def max", "DEF_lv120", "DEF_lv150", "DEF_lv200")

    # Debug: print stat fields if still missing
    if not card["base_hp"]:
        # Find any field with HP, ATK, DEF in the name
        hp_fields = re.findall(r'\|\s*([^\|\}\n]*(?:HP|ATK|DEF|hp|atk|def)[^\|\}\n]*?)\s*=\s*(\d+)', wikitext)
        if hp_fields:
            print(f"  ⚠️  Stat fields found but not matched in '{page_title}': {hp_fields[:10]}")

    card["leader_skill"]  = extract_field(wikitext, "LS description", "ls description")
    card["sa_name"]       = extract_field(wikitext, "SA name", "sa name", "MSA name")
    card["super_attack"]  = extract_field(wikitext, "SA description", "sa description")
    card["passive_skill"] = extract_field(wikitext, "PS description", "ps description")

    # EZA fields — wiki uses "Z" suffix for EZA versions
    card["eza_leader_skill"]  = extract_field(wikitext, "LS description Z", "LS description z")
    card["eza_sa_name"]       = extract_field(wikitext, "UltraSA name", "SA name Z", "sa name Z")
    card["eza_super_attack"]  = extract_field(wikitext, "UltraSA description Z", "SA description Z", "sa description Z")
    card["eza_passive_skill"] = extract_field(wikitext, "PS description Z", "ps description Z")
    # EZA stats use same HP/ATK/DEF max fields — no separate EZA stat fields found
    card["eza_max_hp"]        = extract_field(wikitext, "EZA HP", "eza hp", "HP_eza", "hp_eza")
    card["eza_max_atk"]       = extract_field(wikitext, "EZA ATK", "eza atk", "ATK_eza", "atk_eza")
    card["eza_max_def"]       = extract_field(wikitext, "EZA DEF", "eza def", "DEF_eza", "def_eza")


    # Links - wiki stores all links in single "Link_skill" field, pipe separated
    link_skill = extract_field(wikitext, "Link_skill", "Link skill", "link_skill", "links")
    if link_skill:
        # Split by newlines or commas if multiple
        raw_links = re.split(r'\n|,', link_skill)
        card["links"] = "|".join([l.strip() for l in raw_links if l.strip()])
    else:
        # Fallback: try numbered link fields
        links = re.findall(r'\|\s*link\s*\d+\s*=\s*([^\|\}\n]+)', wikitext, re.IGNORECASE)
        card["links"] = "|".join([clean_wiki(l) for l in links if clean_wiki(l).strip()])

    # Categories - wiki stores in single "Category" field
    category = extract_field(wikitext, "Category", "category", "categories")
    if category:
        raw_cats = re.split(r'\n|,', category)
        seen = set()
        clean_cats = []
        for c in raw_cats:
            val = c.strip()
            if val and val not in seen:
                seen.add(val)
                clean_cats.append(val)
        card["categories"] = "|".join(clean_cats)
    else:
        cats = re.findall(r'\|\s*categor(?:y|ies)\s*\d*\s*=\s*([^\|\}]+)', wikitext, re.IGNORECASE)
        seen = set()
        clean_cats = []
        for c in cats:
            val = clean_wiki(c)
            if val and val not in seen:
                seen.add(val)
                clean_cats.append(val)
        card["categories"] = "|".join(clean_cats)

    # Image
    thumb_match = re.search(r'\|\s*thumb apng\s*=\s*(https?://\S+)', wikitext, re.IGNORECASE)
    if not thumb_match:
        thumb_match = re.search(r'\|\s*thumb\s*=\s*(https?://\S+)', wikitext, re.IGNORECASE)
    if not thumb_match:
        thumb_match = re.search(r'\|\s*artwork apng\s*=\s*(https?://\S+)', wikitext, re.IGNORECASE)
    card["image"] = thumb_match.group(1).strip() if thumb_match else ""

    card["wiki_url"] = f"https://dbz-dokkanbattle.fandom.com/wiki/{page_title.replace(' ', '_')}"

    return card

# ======================
# SYNC LOGIC
# ======================
def is_card_page(wikitext: str) -> bool:
    """Check if this wikitext is actually a card page"""
    return "{{Characters" in wikitext or "rarity" in wikitext.lower()

async def sync_card(session: aiohttp.ClientSession, conn: sqlite3.Connection, title: str):
    """Fetch and store a single card"""
    wikitext = await get_wikitext(session, title)
    if not wikitext or not is_card_page(wikitext):
        return False

    card = parse_wikitext(wikitext, title)

    # Skip pages with no useful data
    if not card.get("rarity") and not card.get("type"):
        return False

    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO cards (
            page_title, title, name, type, rarity, cost, max_level,
            base_hp, base_atk, base_def, max_hp, max_atk, max_def,
            leader_skill, super_attack, sa_name, passive_skill,
            links, categories, image, wiki_url, synced_at,
            eza_leader_skill, eza_super_attack, eza_sa_name, eza_passive_skill,
            eza_max_hp, eza_max_atk, eza_max_def
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        card["page_title"], card.get("title"), card.get("name"),
        card.get("type"), card.get("rarity"), card.get("cost"), card.get("max_level"),
        card.get("base_hp"), card.get("base_atk"), card.get("base_def"),
        card.get("max_hp"), card.get("max_atk"), card.get("max_def"),
        card.get("leader_skill"), card.get("super_attack"), card.get("sa_name"),
        card.get("passive_skill"), card.get("links"), card.get("categories"),
        card.get("image"), card.get("wiki_url"),
        datetime.utcnow().isoformat(),
        card.get("eza_leader_skill"), card.get("eza_super_attack"), card.get("eza_sa_name"),
        card.get("eza_passive_skill"), card.get("eza_max_hp"), card.get("eza_max_atk"), card.get("eza_max_def")
    ))
    conn.commit()
    return True

async def get_recently_modified_titles(session: aiohttp.ClientSession, hours: int = 24) -> list:
    """Get card page titles modified on the wiki in the last N hours"""
    url = "https://dbz-dokkanbattle.fandom.com/api.php"
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    titles = []
    rccontinue = None

    while True:
        params = {
            "action": "query",
            "list": "recentchanges",
            "rcstart": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rcend": cutoff,
            "rclimit": "500",
            "rcnamespace": "0",
            "rctype": "edit|new",
            "rcprop": "title",
            "format": "json",
        }
        if rccontinue:
            params["rccontinue"] = rccontinue

        try:
            async with session.get(url, params=params) as resp:
                data = await resp.json()
                changes = data.get("query", {}).get("recentchanges", [])
                for change in changes:
                    title = change.get("title", "")
                    if title and ":" not in title:  # skip File:, Category: etc
                        titles.append(title)
                if "continue" in data:
                    rccontinue = data["continue"].get("rccontinue")
                else:
                    break
        except Exception as e:
            print(f"⚠️  Error fetching recent changes: {e}")
            break

    return list(set(titles))

async def sync_all(update_only: bool = False, resync: bool = False):
    conn = init_db()
    print(f"🗄️  Database: {DB_PATH}")
    if update_only:
        print(f"🔄 Mode: Update (new cards + recently edited + upcoming schedule)")
    elif resync:
        print(f"🔄 Mode: Resync all (updates all fields, community teams preserved)")
    else:
        print(f"🔄 Mode: Full sync")
    print(f"⏰ Started: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    async with aiohttp.ClientSession() as session:
        all_titles = await get_all_card_titles(session)

        if update_only:
            c = conn.cursor()
            existing = {row[0] for row in c.execute("SELECT page_title FROM cards")}

            # New cards not in DB yet
            new_titles = [t for t in all_titles if t not in existing]
            print(f"  🆕 {len(new_titles)} new cards found")

            # Recently edited cards on the wiki (last 24 hours)
            print(f"  🔍 Checking wiki for recent edits...")
            recent_titles = await get_recently_modified_titles(session, hours=24)
            # Only keep ones that are actual card pages
            all_titles_set = set(all_titles)
            recent_card_titles = [t for t in recent_titles if t in all_titles_set]
            print(f"  ✏️  {len(recent_card_titles)} recently edited cards found")

            # Combine — deduplicate
            titles = list(set(new_titles + recent_card_titles))
            print(f"  📝 {len(titles)} total cards to sync\n")
        elif resync:
            titles = all_titles
            print(f"  📝 Re-syncing all {len(titles)} cards (community teams safe)\n")
        else:
            titles = all_titles

        total   = len(titles)
        synced  = 0
        skipped = 0
        failed  = 0

        # Process in batches
        for i in range(0, total, BATCH_SIZE):
            batch = titles[i:i + BATCH_SIZE]
            tasks = [sync_card(session, conn, title) for title in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for title, result in zip(batch, results):
                if isinstance(result, Exception):
                    print(f"  ❌ Error on '{title}': {result}")
                    failed += 1
                elif result:
                    synced += 1
                else:
                    skipped += 1

            progress = min(i + BATCH_SIZE, total)
            print(f"  Progress: {progress}/{total} | ✅ Synced: {synced} | ⏭️ Skipped: {skipped} | ❌ Failed: {failed}")

            await asyncio.sleep(DELAY)

        # Sync schedule while session is still open
        await sync_schedule(session, conn)

    # Final stats
    c = conn.cursor()
    total_in_db = c.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    conn.close()

    print(f"\n✅ Sync complete!")
    print(f"   Cards synced this run : {synced}")
    print(f"   Cards skipped         : {skipped}")
    print(f"   Errors                : {failed}")
    print(f"   Total cards in DB     : {total_in_db}")
    print(f"⏰ Finished: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")

# ======================
# SCHEDULE SYNC
# ======================
async def sync_schedule(session: aiohttp.ClientSession, conn: sqlite3.Connection):
    """Fetch upcoming cards from the wiki and save to schedule table"""
    print("\n📅 Syncing upcoming cards schedule...")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT,
            name        TEXT,
            type        TEXT,
            rarity      TEXT,
            image       TEXT,
            wiki_url    TEXT,
            synced_at   TEXT
        )
    """)
    conn.commit()

    params = {
        "action": "parse",
        "page": "Upcoming Cards",
        "prop": "wikitext",
        "formatversion": "2",
        "format": "json"
    }

    try:
        async with session.get(WIKI_API, params=params, headers=HEADERS) as resp:
            data = await resp.json(content_type=None)
            wikitext = data.get("parse", {}).get("wikitext", "")
    except Exception as e:
        print(f"  ❌ Failed to fetch schedule: {e}")
        return 0

    if not wikitext:
        print("  ❌ No wikitext returned for Upcoming Cards page")
        return 0

    card_entries = re.findall(r'\[\[([^\|\]]+?)(?:\|[^\]]+)?\]\]', wikitext)
    skip_prefixes = ["File:", "Image:", "Category:", "Template:", "User:", "Talk:"]
    card_titles = []
    seen = set()
    for entry in card_entries:
        entry = entry.strip()
        if any(entry.startswith(p) for p in skip_prefixes):
            continue
        if entry in seen:
            continue
        seen.add(entry)
        card_titles.append(entry)

    if not card_titles:
        print("  ⚠️  No card entries found in Upcoming Cards page")
        return 0

    print(f"  Found {len(card_titles)} upcoming card entries")
    conn.execute("DELETE FROM schedule")
    conn.row_factory = sqlite3.Row
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    synced = 0

    for title in card_titles[:50]:
        existing = conn.execute(
            "SELECT title, name, type, rarity, image, wiki_url FROM cards WHERE page_title = ? OR title = ?",
            (title, title)
        ).fetchone()

        if existing:
            conn.execute("""
                INSERT INTO schedule (title, name, type, rarity, image, wiki_url, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                existing["title"] or title,
                existing["name"],
                existing["type"],
                existing["rarity"],
                existing["image"],
                existing["wiki_url"],
                now
            ))
        else:
            wiki_url = f"https://dbz-dokkanbattle.fandom.com/wiki/{title.replace(' ', '_')}"
            conn.execute("""
                INSERT INTO schedule (title, wiki_url, synced_at)
                VALUES (?, ?, ?)
            """, (title, wiki_url, now))
        synced += 1

    conn.commit()
    print(f"  ✅ {synced} upcoming cards saved to schedule table")
    return synced

# ======================
# RUN
# ======================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dokkan card database sync")
    parser.add_argument("--update", action="store_true", help="Only sync new cards not already in DB")
    parser.add_argument("--resync", action="store_true", help="Re-sync ALL cards and update all fields (keeps community teams)")
    args = parser.parse_args()

    asyncio.run(sync_all(update_only=args.update, resync=args.resync))