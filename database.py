import aiosqlite
from config import DATABASE

async def get_db():
    return await aiosqlite.connect(DATABASE)

async def setup():
    db = await get_db()
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS guilds (
            id TEXT PRIMARY KEY,
            prefix TEXT DEFAULT '!',
            welcome_enabled INTEGER DEFAULT 0,
            welcome_channel TEXT,
            welcome_message TEXT,
            mod_log_channel TEXT,
            auto_mod_enabled INTEGER DEFAULT 0,
            muted_role TEXT,
            tiktok_url TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT,
            user_id TEXT,
            moderator_id TEXT,
            reason TEXT DEFAULT 'No reason given',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS custom_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT,
            name TEXT,
            response TEXT
        );
    """)
    await db.commit()
    await db.close()

async def get_guild(guild_id: str):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM guilds WHERE id = ?", (guild_id,))
    row = await cursor.fetchone()
    await db.close()
    if row:
        return {
            "id": row[0], "prefix": row[1],
            "welcome_enabled": bool(row[2]), "welcome_channel": row[3],
            "welcome_message": row[4], "mod_log_channel": row[5],
            "auto_mod_enabled": bool(row[6]), "muted_role": row[7],
            "tiktok_url": row[8] if len(row) > 8 else ""
        }
    return None

async def set_guild(guild_id: str, **kwargs):
    db = await get_db()
    existing = await get_guild(guild_id)
    if existing:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [guild_id]
        await db.execute(f"UPDATE guilds SET {sets} WHERE id = ?", vals)
    else:
        cols = ["id"] + list(kwargs.keys())
        vals = [guild_id] + list(kwargs.values())
        placeholders = ",".join("?" for _ in cols)
        await db.execute(f"INSERT INTO guilds ({','.join(cols)}) VALUES ({placeholders})", vals)
    await db.commit()
    await db.close()

async def add_warning(guild_id: str, user_id: str, moderator_id: str, reason: str = "No reason given"):
    db = await get_db()
    await db.execute(
        "INSERT INTO warnings (guild_id, user_id, moderator_id, reason) VALUES (?, ?, ?, ?)",
        (guild_id, user_id, moderator_id, reason)
    )
    await db.commit()
    await db.close()

async def get_warnings(guild_id: str, user_id: str = None):
    db = await get_db()
    if user_id:
        cursor = await db.execute(
            "SELECT id, user_id, moderator_id, reason, timestamp FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY timestamp DESC",
            (guild_id, user_id)
        )
    else:
        cursor = await db.execute(
            "SELECT id, user_id, moderator_id, reason, timestamp FROM warnings WHERE guild_id = ? ORDER BY timestamp DESC",
            (guild_id,)
        )
    rows = await cursor.fetchall()
    await db.close()
    return [{"id": r[0], "user_id": r[1], "moderator_id": r[2], "reason": r[3], "timestamp": r[4]} for r in rows]

async def remove_warning(warning_id: int, guild_id: str):
    db = await get_db()
    await db.execute("DELETE FROM warnings WHERE id = ? AND guild_id = ?", (warning_id, guild_id))
    await db.commit()
    await db.close()

async def add_command(guild_id: str, name: str, response: str):
    db = await get_db()
    await db.execute(
        "INSERT INTO custom_commands (guild_id, name, response) VALUES (?, ?, ?)",
        (guild_id, name, response)
    )
    await db.commit()
    await db.close()

async def get_commands(guild_id: str):
    db = await get_db()
    cursor = await db.execute("SELECT id, name, response FROM custom_commands WHERE guild_id = ?", (guild_id,))
    rows = await cursor.fetchall()
    await db.close()
    return [{"id": r[0], "name": r[1], "response": r[2]} for r in rows]

async def delete_command(cmd_id: int, guild_id: str):
    db = await get_db()
    await db.execute("DELETE FROM custom_commands WHERE id = ? AND guild_id = ?", (cmd_id, guild_id))
    await db.commit()
    await db.close()

async def get_all_guilds():
    db = await get_db()
    cursor = await db.execute("SELECT id FROM guilds")
    rows = await cursor.fetchall()
    await db.close()
    return [r[0] for r in rows]
