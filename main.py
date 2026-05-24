import asyncio
import threading
import uvicorn
from bot import bot
from web import app
from database import setup
from config import DISCORD_TOKEN

async def run():
    await setup()
    t = threading.Thread(target=uvicorn.run, args=(app,), kwargs={"host": "0.0.0.0", "port": 8080, "log_level": "info"}, daemon=True)
    t.start()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(run())
