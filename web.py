import secrets
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
import httpx
from config import CLIENT_ID, CLIENT_SECRET, REDIRECT_URI

from bot import bot
import database

from datetime import datetime

app = FastAPI()

import os
template_dir = os.path.join(os.path.dirname(__file__), "templates")
static_dir = os.path.join(os.path.dirname(__file__), "static")
env = Environment(loader=FileSystemLoader(template_dir))
app.mount("/static", StaticFiles(directory=static_dir), name="static")

sessions = {}

DISCORD_API = "https://discord.com/api/v10"

def render(name, **ctx):
    t = env.get_template(name)
    return HTMLResponse(t.render(**ctx))

@app.get("/")
async def home(request: Request):
    user = get_user(request)
    return render("home.html", request=request, user=user, bot=bot)

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "guilds": len(bot.guilds) if bot.is_ready() else 0,
        "bot_ready": bot.is_ready(),
    }

@app.get("/commands", response_class=HTMLResponse)
async def commands_page(request: Request):
    user = get_user(request)
    return render("commands.html", request=request, user=user)

@app.get("/login")
async def login():
    state = secrets.token_urlsafe(16)
    sessions[state] = {}
    url = (
        f"{DISCORD_API}/oauth2/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify+guilds"
        f"&state={state}"
    )
    return RedirectResponse(url)

@app.get("/callback")
async def callback(code: str, state: str, response: Response):
    if state not in sessions:
        raise HTTPException(400, "Invalid state")
    del sessions[state]
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
            raise HTTPException(400, "Failed to get token")
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
    sessions[session_token] = {"user": user_data, "guilds": guilds, "token": token_data["access_token"]}
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="session", value=session_token, httponly=True, max_age=86400)
    return response

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_user(request)
    if not user:
        return RedirectResponse("/login")
    guilds = get_user_guilds(request)
    admin_guilds = []
    for g in guilds:
        perms = int(g.get("permissions", 0))
        if perms & 0x20:
            admin_guilds.append(g)
    bot_guild_ids = set(str(g.id) for g in bot.guilds)
    return render("dashboard.html", request=request, user=user, admin_guilds=admin_guilds, bot_guild_ids=bot_guild_ids, client_id=CLIENT_ID)

@app.get("/dashboard/{guild_id}", response_class=HTMLResponse)
async def guild_settings(request: Request, guild_id: str):
    user = get_user(request)
    if not user:
        return RedirectResponse("/login")
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return HTMLResponse("Bot not in this server", status_code=404)
    settings = await database.get_guild(guild_id)
    if not settings:
        settings = {"prefix": "!", "welcome_enabled": 0, "welcome_channel": "", "welcome_message": "", "mod_log_channel": "", "tiktok_url": ""}
    return render("guild.html", request=request, user=user, guild=guild, settings=settings)

@app.post("/dashboard/{guild_id}")
async def save_settings(request: Request, guild_id: str):
    user = get_user(request)
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
    user = get_user(request)
    if not user:
        return RedirectResponse("/login")
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return HTMLResponse("Bot not in this server", status_code=404)
    warns = await database.get_warnings(guild_id)
    return render("moderation.html", request=request, user=user, guild=guild, warnings=warns)

@app.get("/dashboard/{guild_id}/commands", response_class=HTMLResponse)
async def guild_commands(request: Request, guild_id: str):
    user = get_user(request)
    if not user:
        return RedirectResponse("/login")
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return HTMLResponse("Bot not in this server", status_code=404)
    cmds = await database.get_commands(guild_id)
    return render("commands.html", request=request, user=user, guild=guild, commands=cmds)

@app.post("/dashboard/{guild_id}/commands/add")
async def add_command(request: Request, guild_id: str):
    user = get_user(request)
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
    user = get_user(request)
    if not user:
        return RedirectResponse("/login")
    await database.delete_command(cmd_id, guild_id)
    return RedirectResponse(f"/dashboard/{guild_id}/commands", status_code=303)

@app.get("/logout")
async def logout(response: Response):
    response = RedirectResponse("/")
    response.delete_cookie("session")
    return response

def get_user(request: Request):
    session_token = request.cookies.get("session")
    if session_token and session_token in sessions:
        return sessions[session_token]["user"]
    return None

def get_user_guilds(request: Request):
    session_token = request.cookies.get("session")
    if session_token and session_token in sessions:
        return sessions[session_token].get("guilds", [])
    return []
