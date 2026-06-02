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

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_data TEXT,
            guilds TEXT,
            access_token TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ticket_config (
            guild_id TEXT PRIMARY KEY,
            enabled INTEGER DEFAULT 0,
            panel_channel_id TEXT DEFAULT '',
            panel_message_id TEXT DEFAULT '',
            category_id TEXT DEFAULT '',
            closed_category_id TEXT DEFAULT '',
            log_channel_id TEXT DEFAULT '',
            support_roles TEXT DEFAULT '[]',
            ticket_number INTEGER DEFAULT 0,
            panel_type TEXT DEFAULT 'buttons',
            close_confirmation INTEGER DEFAULT 1,
            archive_enabled INTEGER DEFAULT 0,
            transcript_enabled INTEGER DEFAULT 1,
            dm_on_close INTEGER DEFAULT 1,
            max_tickets_per_user INTEGER DEFAULT 5,
            cooldown_seconds INTEGER DEFAULT 30,
            delete_seconds INTEGER DEFAULT 3,
            panels TEXT DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS active_tickets (
            channel_id TEXT PRIMARY KEY,
            guild_id TEXT,
            user_id TEXT,
            ticket_number INTEGER,
            ticket_type TEXT DEFAULT 'Support',
            claimed_by TEXT DEFAULT '',
            status TEXT DEFAULT 'open',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            closed_at DATETIME,
            closed_by TEXT
        );

        CREATE TABLE IF NOT EXISTS ticket_blacklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT,
            target_id TEXT,
            target_type TEXT,
            reason TEXT DEFAULT ''
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

async def save_session(session_token: str, user_data: dict, guilds: list, access_token: str):
    import json
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO sessions (token, user_data, guilds, access_token, created_at) VALUES (?, ?, ?, ?, datetime('now'))",
        (session_token, json.dumps(user_data), json.dumps(guilds), access_token)
    )
    await db.commit()
    await db.close()

async def get_session(session_token: str):
    import json
    db = await get_db()
    cursor = await db.execute("SELECT user_data, guilds, access_token FROM sessions WHERE token = ?", (session_token,))
    row = await cursor.fetchone()
    await db.close()
    if row:
        return {
            "user": json.loads(row[0]),
            "guilds": json.loads(row[1]),
            "token": row[2]
        }
    return None

async def delete_session(session_token: str):
    db = await get_db()
    await db.execute("DELETE FROM sessions WHERE token = ?", (session_token,))
    await db.commit()
    await db.close()

async def cleanup_sessions():
    db = await get_db()
    await db.execute("DELETE FROM sessions WHERE created_at < datetime('now', '-1 day')")
    await db.commit()
    await db.close()

async def get_all_guilds():
    db = await get_db()
    cursor = await db.execute("SELECT id FROM guilds")
    rows = await cursor.fetchall()
    await db.close()
    return [r[0] for r in rows]

# ─── Ticket System ──────────────────────────────────────────

async def get_ticket_config(guild_id: str):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM ticket_config WHERE guild_id = ?", (guild_id,))
    row = await cursor.fetchone()
    await db.close()
    if row:
        import json
        return {
            "guild_id": row[0],
            "enabled": bool(row[1]),
            "panel_channel_id": row[2] or "",
            "panel_message_id": row[3] or "",
            "category_id": row[4] or "",
            "closed_category_id": row[5] or "",
            "log_channel_id": row[6] or "",
            "support_roles": json.loads(row[7]) if row[7] else [],
            "ticket_number": row[8] or 0,
            "panel_type": row[9] or "buttons",
            "close_confirmation": bool(row[10]),
            "archive_enabled": bool(row[11]),
            "transcript_enabled": bool(row[12]),
            "dm_on_close": bool(row[13]),
            "max_tickets_per_user": row[14] or 5,
            "cooldown_seconds": row[15] or 30,
            "delete_seconds": row[16] or 3,
            "panels": json.loads(row[17]) if row[17] else []
        }
    return None

async def set_ticket_config(guild_id: str, **kwargs):
    import json
    db = await get_db()
    processed = {}
    for k, v in kwargs.items():
        if k in ("support_roles", "panels") and isinstance(v, (list, tuple)):
            processed[k] = json.dumps(v)
        else:
            processed[k] = v
    existing = await get_ticket_config(guild_id)
    if existing:
        sets = ", ".join(f"{k} = ?" for k in processed)
        vals = list(processed.values()) + [guild_id]
        await db.execute(f"UPDATE ticket_config SET {sets} WHERE guild_id = ?", vals)
    else:
        cols = ["guild_id"] + list(processed.keys())
        vals = [guild_id] + list(processed.values())
        placeholders = ",".join("?" for _ in cols)
        await db.execute(f"INSERT INTO ticket_config ({','.join(cols)}) VALUES ({placeholders})", vals)
    await db.commit()
    await db.close()

async def create_ticket(channel_id: str, guild_id: str, user_id: str, ticket_number: int, ticket_type: str = "Support"):
    db = await get_db()
    await db.execute(
        "INSERT INTO active_tickets (channel_id, guild_id, user_id, ticket_number, ticket_type, status) VALUES (?, ?, ?, ?, ?, 'open')",
        (channel_id, guild_id, user_id, ticket_number, ticket_type)
    )
    await db.commit()
    await db.close()

async def get_active_ticket(channel_id: str):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM active_tickets WHERE channel_id = ?", (channel_id,))
    row = await cursor.fetchone()
    await db.close()
    if row:
        return {
            "channel_id": row[0],
            "guild_id": row[1],
            "user_id": row[2],
            "ticket_number": row[3],
            "ticket_type": row[4],
            "claimed_by": row[5] or "",
            "status": row[6],
            "created_at": row[7],
            "closed_at": row[8],
            "closed_by": row[9] or ""
        }
    return None

async def update_ticket(channel_id: str, **kwargs):
    db = await get_db()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [channel_id]
    await db.execute(f"UPDATE active_tickets SET {sets} WHERE channel_id = ?", vals)
    await db.commit()
    await db.close()

async def delete_active_ticket(channel_id: str):
    db = await get_db()
    await db.execute("DELETE FROM active_tickets WHERE channel_id = ?", (channel_id,))
    await db.commit()
    await db.close()

async def get_user_open_tickets(guild_id: str, user_id: str):
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM active_tickets WHERE guild_id = ? AND user_id = ? AND status = 'open'",
        (guild_id, user_id)
    )
    count = (await cursor.fetchone())[0]
    await db.close()
    return count

async def get_guild_open_tickets(guild_id: str):
    db = await get_db()
    cursor = await db.execute(
        "SELECT * FROM active_tickets WHERE guild_id = ? AND status = 'open'", (guild_id,)
    )
    rows = await cursor.fetchall()
    await db.close()
    return [{
        "channel_id": r[0], "guild_id": r[1], "user_id": r[2],
        "ticket_number": r[3], "ticket_type": r[4], "claimed_by": r[5] or "",
        "status": r[6], "created_at": r[7], "closed_at": r[8], "closed_by": r[9] or ""
    } for r in rows]

async def increment_ticket_number(guild_id: str):
    cfg = await get_ticket_config(guild_id)
    num = (cfg["ticket_number"] if cfg else 0) + 1
    await set_ticket_config(guild_id, ticket_number=num)
    return num

async def add_blacklist(guild_id: str, target_id: str, target_type: str, reason: str = ""):
    db = await get_db()
    await db.execute(
        "INSERT OR IGNORE INTO ticket_blacklist (guild_id, target_id, target_type, reason) VALUES (?, ?, ?, ?)",
        (guild_id, target_id, target_type, reason)
    )
    await db.commit()
    await db.close()

async def remove_blacklist(guild_id: str, target_id: str):
    db = await get_db()
    await db.execute("DELETE FROM ticket_blacklist WHERE guild_id = ? AND target_id = ?", (guild_id, target_id))
    await db.commit()
    await db.close()

async def is_blacklisted(guild_id: str, target_id: str):
    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM ticket_blacklist WHERE guild_id = ? AND target_id = ?", (guild_id, target_id)
    )
    count = (await cursor.fetchone())[0]
    await db.close()
    return count > 0

async def get_blacklist(guild_id: str):
    db = await get_db()
    cursor = await db.execute(
        "SELECT target_id, target_type, reason FROM ticket_blacklist WHERE guild_id = ?", (guild_id,)
    )
    rows = await cursor.fetchall()
    await db.close()
    return [{"target_id": r[0], "target_type": r[1], "reason": r[2] or ""} for r in rows]
