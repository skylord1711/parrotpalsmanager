import secrets
import hashlib
import hmac
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exception_handlers import http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from jinja2 import Environment, FileSystemLoader
import httpx
from config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI

import database

from datetime import datetime, timezone

app = FastAPI()

import os
template_dir = os.path.join(os.path.dirname(__file__), "templates")
static_dir = os.path.join(os.path.dirname(__file__), "static")
env = Environment(loader=FileSystemLoader(template_dir))
app.mount("/static", StaticFiles(directory=static_dir), name="static")

SIGNING_KEY = secrets.token_hex(32)

DISCORD_API = "https://discord.com/api/v10"

def make_signed_cookie(data: str) -> str:
    sig = hmac.new(SIGNING_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}.{sig}"

def verify_signed_cookie(signed: str) -> str | None:
    try:
        data, sig = signed.rsplit(".", 1)
        expected = hmac.new(SIGNING_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected):
            return data
    except Exception:
        pass
    return None

async def render_error(request: Request, title: str, message: str, status: int = 400):
    user = await get_user(request)
    t = env.get_template("error.html")
    return HTMLResponse(t.render(request=request, user=user, title=title, message=message, status=status), status_code=status)

@app.exception_handler(StarletteHTTPException)
async def custom_http_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return await render_error(request, "404 - Not Found", "The page you're looking for doesn't exist.", 404)
    if exc.status_code == 400:
        return await render_error(request, "400 - Bad Request", str(exc.detail) if exc.detail else "Invalid request.", 400)
    return await http_exception_handler(request, exc)

bot_available = False
bot_instance = None
try:
    from bot import bot as discord_bot
    bot_instance = discord_bot
    bot_available = True
except Exception:
    pass

def get_bot():
    return bot_instance

def bot_is_ready():
    b = get_bot()
    try:
        return b.is_ready() if b else False
    except Exception:
        return False

def bot_guilds():
    b = get_bot()
    return list(b.guilds) if b else []

def bot_get_guild(guild_id):
    b = get_bot()
    return b.get_guild(int(guild_id)) if b else None

def bot_latency():
    b = get_bot()
    if b and b.is_ready():
        try:
            ms = b.latency * 1000
            return round(ms) if not (ms != ms) else 0
        except Exception:
            return 0
    return 0

def bot_user():
    b = get_bot()
    return b.user if b else None

def bot_total_members():
    total = 0
    for g in bot_guilds():
        try:
            total += g.member_count
        except Exception:
            pass
    return total

def render(name, **ctx):
    t = env.get_template(name)
    return HTMLResponse(t.render(**ctx))

@app.on_event("startup")
async def startup():
    try:
        await database.setup()
        await database.cleanup_sessions()
    except Exception:
        pass

@app.get("/")
async def home(request: Request):
    user = await get_user(request)
    guild_count = len(bot_guilds())
    member_count = bot_total_members()
    return render("home.html",
        request=request, user=user,
        bot_ready=bot_is_ready(),
        guild_count=guild_count,
        member_count=member_count,
        bot_latency=bot_latency(),
        bot_user=bot_user(),
        bot_available=bot_available,
    )

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "guilds": len(bot_guilds()) if bot_is_ready() else 0,
        "bot_ready": bot_is_ready(),
        "bot_available": bot_available,
        "latency_ms": bot_latency(),
    }

@app.get("/api/debug")
async def debug():
    b = get_bot()
    if not b:
        return {"error": "bot not available"}
    cmd_names = []
    if hasattr(b, 'tree') and b.tree:
        try:
            for cmd in b.tree._commands.values():
                cmd_names.append(getattr(cmd, 'name', str(cmd)))
        except:
            pass
    return {
        "ready": b.is_ready() if hasattr(b, 'is_ready') else False,
        "extensions": list(b.extensions.keys()) if hasattr(b, 'extensions') else [],
        "cogs": list(b.cogs.keys()) if hasattr(b, 'cogs') else [],
        "tree_commands": cmd_names,
    }

@app.get("/api/status")
async def api_status():
    return JSONResponse({
        "ready": bot_is_ready(),
        "available": bot_available,
        "guilds": len(bot_guilds()),
        "latency": bot_latency(),
        "members": bot_total_members(),
    })

@app.get("/commands", response_class=HTMLResponse)
async def commands_page(request: Request):
    user = await get_user(request)
    return render("commands.html", request=request, user=user,
        bot_ready=bot_is_ready(), bot_latency=bot_latency(),
        bot_user=bot_user(), bot_available=bot_available)

@app.get("/login")
async def login(response: Response):
    state = secrets.token_urlsafe(16)
    signed = make_signed_cookie(state)
    response = RedirectResponse(
        f"{DISCORD_API}/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify+guilds"
        f"&state={state}"
    )
    response.set_cookie(key="oauth_state", value=signed, httponly=True, max_age=300, path="/")
    return response

@app.get("/callback")
async def callback(code: str, state: str, request: Request):
    stored = request.cookies.get("oauth_state")
    if not stored:
        return await render_error(request, "Login Expired", "Your login session expired. Please try logging in again.", 400)
    decoded = verify_signed_cookie(stored)
    if not decoded or decoded != state:
        return await render_error(request, "Login Failed", "Security check failed. This can happen if you logged in from a different browser or session. Please try again.", 400)

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        token_data = token_resp.json()
        if "access_token" not in token_data:
            return await render_error(request, "Login Failed", "Discord did not return a valid token. Please try again.", 400)
        user_resp = await client.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"}
        )
        user_data = user_resp.json()
        guilds_resp = await client.get(
            f"{DISCORD_API}/users/@me/guilds",
            headers={"Authorization": f"Bearer {token_data['access_token']}"}
        )
        guilds = guilds_resp.json()

    session_token = secrets.token_urlsafe(32)
    await database.save_session(session_token, user_data, guilds, token_data["access_token"])

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="session", value=session_token, httponly=True, max_age=86400)
    response.delete_cookie("oauth_state", path="/")
    return response

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    guilds = await get_user_guilds(request)
    admin_guilds = []
    for g in guilds:
        perms = int(g.get("permissions", 0))
        if perms & 0x20:
            admin_guilds.append(g)
    bot_guild_ids = set(str(g.id) for g in bot_guilds())
    return render("dashboard.html", request=request, user=user,
        admin_guilds=admin_guilds, bot_guild_ids=bot_guild_ids,
        client_id=CLIENT_ID, bot_ready=bot_is_ready(),
        bot_available=bot_available, bot_latency=bot_latency(),
        bot_user=bot_user())

@app.get("/dashboard/{guild_id}", response_class=HTMLResponse)
async def guild_settings(request: Request, guild_id: str):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    guild = bot_get_guild(guild_id)
    if not guild:
        return render("guild.html", request=request, user=user,
            guild=None, settings=None, missing=True,
            bot_ready=bot_is_ready(), bot_latency=bot_latency(),
            bot_user=bot_user(), bot_available=bot_available)
    settings = await database.get_guild(guild_id)
    if not settings:
        settings = {"prefix": "!", "welcome_enabled": 0, "welcome_channel": "", "welcome_message": "", "mod_log_channel": "", "tiktok_url": ""}
    return render("guild.html", request=request, user=user,
        guild=guild, settings=settings, missing=False,
        bot_ready=bot_is_ready(), bot_latency=bot_latency(),
        bot_user=bot_user(), bot_available=bot_available)

@app.post("/dashboard/{guild_id}")
async def save_settings(request: Request, guild_id: str):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    form = await request.form()
    prefix = form.get("prefix", "!")
    welcome_enabled = 1 if form.get("welcome_enabled") else 0
    welcome_channel = form.get("welcome_channel", "")
    welcome_message = form.get("welcome_message", "")
    mod_log_channel = form.get("mod_log_channel", "")
    tiktok_url = form.get("tiktok_url", "")
    await database.set_guild(
        guild_id,
        prefix=prefix,
        welcome_enabled=welcome_enabled,
        welcome_channel=welcome_channel,
        welcome_message=welcome_message,
        mod_log_channel=mod_log_channel,
        tiktok_url=tiktok_url,
    )
    return RedirectResponse(f"/dashboard/{guild_id}?saved=1", status_code=303)

@app.get("/dashboard/{guild_id}/moderation", response_class=HTMLResponse)
async def guild_moderation(request: Request, guild_id: str):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    guild = bot_get_guild(guild_id)
    if not guild:
        return HTMLResponse("Bot not in this server", status_code=404)
    warns = await database.get_warnings(guild_id)
    return render("moderation.html", request=request, user=user,
        guild=guild, warnings=warns,
        bot_ready=bot_is_ready(), bot_latency=bot_latency(),
        bot_user=bot_user(), bot_available=bot_available)

@app.get("/dashboard/{guild_id}/commands", response_class=HTMLResponse)
async def guild_commands(request: Request, guild_id: str):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    guild = bot_get_guild(guild_id)
    if not guild:
        return HTMLResponse("Bot not in this server", status_code=404)
    cmds = await database.get_commands(guild_id)
    return render("guild_commands.html", request=request, user=user,
        guild=guild, commands=cmds,
        bot_ready=bot_is_ready(), bot_latency=bot_latency(),
        bot_user=bot_user(), bot_available=bot_available)

@app.post("/dashboard/{guild_id}/commands/add")
async def add_command(request: Request, guild_id: str):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    form = await request.form()
    name = form.get("name", "").strip().lower()
    response = form.get("response", "").strip()
    if name and response:
        await database.add_command(guild_id, name, response)
    return RedirectResponse(f"/dashboard/{guild_id}/commands", status_code=303)

@app.post("/dashboard/{guild_id}/commands/delete/{cmd_id}")
async def delete_command(request: Request, guild_id: str, cmd_id: int):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    await database.delete_command(cmd_id, guild_id)
    return RedirectResponse(f"/dashboard/{guild_id}/commands", status_code=303)

@app.get("/logout")
async def logout(request: Request, response: Response):
    session_token = request.cookies.get("session")
    if session_token:
        await database.delete_session(session_token)
    response = RedirectResponse("/")
    response.delete_cookie("session")
    return response

@app.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    user = await get_user(request)
    guild_count = len(bot_guilds())
    member_count = bot_total_members()
    guilds_list = bot_guilds()
    top_guilds = sorted(guilds_list, key=lambda g: g.member_count if hasattr(g, 'member_count') else 0, reverse=True)[:10]
    return render("stats.html", request=request, user=user,
        guild_count=guild_count, member_count=member_count,
        top_guilds=top_guilds, bot_ready=bot_is_ready(),
        bot_latency=bot_latency(), bot_user=bot_user(),
        bot_available=bot_available)

async def get_user(request: Request):
    session_token = request.cookies.get("session")
    if session_token:
        session = await database.get_session(session_token)
        if session:
            return session["user"]
    return None

async def get_user_guilds(request: Request):
    session_token = request.cookies.get("session")
    if session_token:
        session = await database.get_session(session_token)
        if session:
            return session.get("guilds", [])
    return []

# ─── Ticket Dashboard Routes ──────────────────────────────

@app.get("/dashboard/{guild_id}/tickets", response_class=HTMLResponse)
async def guild_tickets(request: Request, guild_id: str):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    guild = bot_get_guild(guild_id)
    if not guild:
        return HTMLResponse("Bot not in this server", status_code=404)
    cfg = await database.get_ticket_config(guild_id)
    if not cfg:
        cfg = {
            "guild_id": guild_id, "enabled": False,
            "panel_channel_id": "", "panel_message_id": "",
            "category_id": "", "closed_category_id": "",
            "log_channel_id": "", "support_roles": [],
            "ticket_number": 0, "panel_type": "buttons",
            "close_confirmation": True, "archive_enabled": False,
            "transcript_enabled": True, "dm_on_close": True,
            "max_tickets_per_user": 5, "cooldown_seconds": 30,
            "delete_seconds": 3, "panels": []
        }
    active_tickets = await database.get_guild_open_tickets(guild_id)
    blacklist = await database.get_blacklist(guild_id)
    return render("guild_tickets.html", request=request, user=user,
        guild=guild, cfg=cfg, active_tickets=active_tickets, blacklist=blacklist,
        bot_ready=bot_is_ready(), bot_latency=bot_latency(),
        bot_user=bot_user(), bot_available=bot_available)

@app.post("/dashboard/{guild_id}/tickets")
async def save_ticket_settings(request: Request, guild_id: str):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    form = await request.form()
    import json

    enabled = 1 if form.get("enabled") else 0
    panel_type = form.get("panel_type", "buttons")
    category_id = form.get("category_id", "")
    closed_category_id = form.get("closed_category_id", "")
    log_channel_id = form.get("log_channel_id", "")
    close_confirmation = 1 if form.get("close_confirmation") else 0
    archive_enabled = 1 if form.get("archive_enabled") else 0
    transcript_enabled = 1 if form.get("transcript_enabled") else 0
    dm_on_close = 1 if form.get("dm_on_close") else 0
    max_tickets_per_user = int(form.get("max_tickets_per_user", 5))
    cooldown_seconds = int(form.get("cooldown_seconds", 30))
    delete_seconds = int(form.get("delete_seconds", 3))

    support_roles_str = form.get("support_roles", "[]")
    try:
        support_roles = json.loads(support_roles_str) if support_roles_str else []
    except:
        support_roles = []

    panels_str = form.get("panels", "[]")
    try:
        panels = json.loads(panels_str) if panels_str else []
    except:
        panels = []

    await database.set_ticket_config(
        guild_id,
        enabled=enabled,
        panel_type=panel_type,
        category_id=category_id,
        closed_category_id=closed_category_id,
        log_channel_id=log_channel_id,
        support_roles=json.dumps(support_roles),
        close_confirmation=close_confirmation,
        archive_enabled=archive_enabled,
        transcript_enabled=transcript_enabled,
        dm_on_close=dm_on_close,
        max_tickets_per_user=max_tickets_per_user,
        cooldown_seconds=cooldown_seconds,
        delete_seconds=delete_seconds,
        panels=json.dumps(panels)
    )
    return RedirectResponse(f"/dashboard/{guild_id}/tickets?saved=1", status_code=303)

@app.post("/dashboard/{guild_id}/tickets/regenerate-panel")
async def regenerate_ticket_panel(request: Request, guild_id: str):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    guild = bot_get_guild(guild_id)
    if guild:
        bot_instance = get_bot()
        if bot_instance and bot_instance.is_ready():
            cog = bot_instance.get_cog("TicketSystem")
            if cog:
                cfg = await database.get_ticket_config(guild_id)
                if cfg and cfg["enabled"] and cfg["panel_channel_id"]:
                    channel = guild.get_channel(int(cfg["panel_channel_id"]))
                    if channel:
                        if cfg["panel_message_id"]:
                            try:
                                old_msg = await channel.fetch_message(int(cfg["panel_message_id"]))
                                await old_msg.delete()
                            except:
                                pass
                        from cogs.tickets import TicketButtonView, TicketSelectView
                        import discord
                        embed = discord.Embed(
                            title="🎫 Create a Ticket",
                            description="Click a button below to open a ticket. Our team will assist you shortly.",
                            color=discord.Color.from_rgb(233, 69, 96)
                        )
                        if cfg["panel_type"] == "select":
                            view = TicketSelectView(guild_id, cfg, bot_instance)
                        else:
                            view = TicketButtonView(guild_id, cfg, bot_instance)
                        msg = await channel.send(embed=embed, view=view)
                        await database.set_ticket_config(guild_id, panel_channel_id=str(channel.id), panel_message_id=str(msg.id))
                        bot_instance.add_view(view, message_id=msg.id)
    return RedirectResponse(f"/dashboard/{guild_id}/tickets?saved=1", status_code=303)

@app.post("/dashboard/{guild_id}/tickets/blacklist/add")
async def add_ticket_blacklist(request: Request, guild_id: str):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    form = await request.form()
    target_id = form.get("target_id", "").strip()
    reason = form.get("reason", "").strip()
    if target_id:
        await database.add_blacklist(guild_id, target_id, "user", reason)
    return RedirectResponse(f"/dashboard/{guild_id}/tickets?saved=1", status_code=303)

@app.post("/dashboard/{guild_id}/tickets/blacklist/remove/{target_id}")
async def remove_ticket_blacklist(request: Request, guild_id: str, target_id: str):
    user = await get_user(request)
    if not user:
        return RedirectResponse("/login")
    await database.remove_blacklist(guild_id, target_id)
    return RedirectResponse(f"/dashboard/{guild_id}/tickets?saved=1", status_code=303)
