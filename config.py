import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TIKTOK_URL = os.getenv("TIKTOK_URL", "https://www.tiktok.com/@streamer")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8080/callback")
DATABASE = "bot.db"
OWNER_ID = os.getenv("OWNER_ID")
