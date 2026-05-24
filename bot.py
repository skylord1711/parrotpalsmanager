import discord
from discord.ext import commands
import database
from config import DISCORD_TOKEN
import asyncio
import datetime

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.moderation = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def get_tiktok_url(guild_id: str) -> str:
    guild_data = await database.get_guild(guild_id)
    if guild_data and guild_data.get("tiktok_url"):
        return guild_data["tiktok_url"]
    return "https://www.tiktok.com/@streamer"

@bot.event
async def on_ready():
    print(f"{bot.user} is online in {len(bot.guilds)} servers")
    for guild in bot.guilds:
        db_guild = await database.get_guild(str(guild.id))
        if not db_guild:
            await database.set_guild(str(guild.id), prefix="!", welcome_enabled=0)
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Sync error: {e}")

@bot.event
async def on_member_join(member):
    guild = await database.get_guild(str(member.guild.id))
    if guild and guild["welcome_enabled"] and guild["welcome_channel"]:
        channel = member.guild.get_channel(int(guild["welcome_channel"]))
        if channel:
            msg = guild["welcome_message"] or "Welcome {user} to the server!"
            msg = msg.replace("{user}", member.mention).replace("{server}", member.guild.name)
            await channel.send(msg)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    guild = await database.get_guild(str(message.guild.id))
    prefix = guild["prefix"] if guild else "!"
    if message.content.startswith(prefix):
        cmd = message.content[len(prefix):].strip().split()[0].lower()
        commands = await database.get_commands(str(message.guild.id))
        for c in commands:
            if c["name"] == cmd:
                await message.channel.send(c["response"])
                return
    await bot.process_commands(message)

@bot.tree.command(name="go-live", description="Announce TikTok stream is live")
async def go_live(interaction: discord.Interaction, message: str = ""):
    tiktok_url = await get_tiktok_url(str(interaction.guild_id))
    embed = discord.Embed(
        title="LIVE NOW!",
        description=f"{interaction.user.mention} is live on TikTok!" + (f"\n\n*{message}*" if message else ""),
        color=discord.Color.from_rgb(255, 0, 0),
        url=tiktok_url
    )
    embed.add_field(name="Watch", value=f"[Click here]({tiktok_url})", inline=False)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.set_footer(text="Go show some love!")
    await interaction.response.send_message("@everyone", embed=embed)

@bot.tree.command(name="socials", description="Show streamer social links")
async def socials(interaction: discord.Interaction):
    tiktok_url = await get_tiktok_url(str(interaction.guild_id))
    embed = discord.Embed(title="Streamer Socials", color=discord.Color.green())
    embed.add_field(name="TikTok", value=tiktok_url, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! `{round(bot.latency * 1000)}ms`", ephemeral=True)

@bot.tree.command(name="kick", description="Kick a member")
@commands.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.kick(reason=reason)
    embed = discord.Embed(title="Kicked", color=discord.Color.orange())
    embed.add_field(name="User", value=member.mention)
    embed.add_field(name="Reason", value=reason)
    await interaction.response.send_message(embed=embed)
    guild = await database.get_guild(str(interaction.guild_id))
    if guild and guild["mod_log_channel"]:
        ch = interaction.guild.get_channel(int(guild["mod_log_channel"]))
        if ch:
            await ch.send(embed=embed)

@bot.tree.command(name="ban", description="Ban a member")
@commands.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    await member.ban(reason=reason)
    embed = discord.Embed(title="Banned", color=discord.Color.red())
    embed.add_field(name="User", value=member.mention)
    embed.add_field(name="Reason", value=reason)
    await interaction.response.send_message(embed=embed)
    guild = await database.get_guild(str(interaction.guild_id))
    if guild and guild["mod_log_channel"]:
        ch = interaction.guild.get_channel(int(guild["mod_log_channel"]))
        if ch:
            await ch.send(embed=embed)

@bot.tree.command(name="warn", description="Warn a member")
@commands.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
    await database.add_warning(str(interaction.guild_id), str(member.id), str(interaction.user.id), reason)
    warnings = await database.get_warnings(str(interaction.guild_id), str(member.id))
    embed = discord.Embed(title="Warning Issued", color=discord.Color.yellow())
    embed.add_field(name="User", value=member.mention)
    embed.add_field(name="Reason", value=reason)
    embed.add_field(name="Total Warnings", value=str(len(warnings)))
    await interaction.response.send_message(embed=embed)
    try:
        await member.send(f"You were warned in **{interaction.guild.name}**: {reason}")
    except:
        pass

@bot.tree.command(name="warnings", description="List warnings for a member")
@commands.has_permissions(moderate_members=True)
async def warnings(interaction: discord.Interaction, member: discord.Member):
    warns = await database.get_warnings(str(interaction.guild_id), str(member.id))
    if not warns:
        await interaction.response.send_message(f"{member.mention} has no warnings!", ephemeral=True)
        return
    embed = discord.Embed(title=f"Warnings for {member.display_name}", color=discord.Color.yellow())
    for w in warns:
        mod = interaction.guild.get_member(int(w["moderator_id"]))
        mod_name = mod.display_name if mod else "Unknown"
        embed.add_field(name=f"#{w['id']} - {w['timestamp'][:10]}", value=f"Reason: {w['reason']}\nBy: {mod_name}", inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="unwarn", description="Remove a warning by ID")
@commands.has_permissions(moderate_members=True)
async def unwarn(interaction: discord.Interaction, warning_id: int):
    await database.remove_warning(warning_id, str(interaction.guild_id))
    await interaction.response.send_message(f"Removed warning #{warning_id}", ephemeral=True)

@bot.tree.command(name="clear", description="Delete recent messages")
@commands.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, count: int):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=min(count, 100))
    await interaction.followup.send(f"Deleted {len(deleted)} messages", ephemeral=True)

@bot.tree.command(name="slowmode", description="Set slowmode in current channel")
@commands.has_permissions(manage_channels=True)
async def slowmode(interaction: discord.Interaction, seconds: int):
    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message(f"Slowmode set to {seconds}s", ephemeral=True)

@bot.tree.command(name="timeout", description="Timeout a member")
@commands.has_permissions(moderate_members=True)
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason"):
    duration = datetime.timedelta(minutes=minutes)
    await member.timeout(duration, reason=reason)
    await interaction.response.send_message(f"Timed out {member.mention} for {minutes} minutes", ephemeral=True)

@bot.tree.command(name="commands", description="List all commands")
async def cmdlist(interaction: discord.Interaction):
    embed = discord.Embed(title="Bot Commands", color=discord.Color.blue())
    embed.add_field(name="/go-live", value="Announce stream", inline=True)
    embed.add_field(name="/socials", value="Show social links", inline=True)
    embed.add_field(name="/ping", value="Bot latency", inline=True)
    embed.add_field(name="/kick", value="Kick a member", inline=True)
    embed.add_field(name="/ban", value="Ban a member", inline=True)
    embed.add_field(name="/warn", value="Warn a member", inline=True)
    embed.add_field(name="/warnings", value="View warnings", inline=True)
    embed.add_field(name="/unwarn", value="Remove warning", inline=True)
    embed.add_field(name="/clear", value="Delete messages", inline=True)
    embed.add_field(name="/slowmode", value="Set slowmode", inline=True)
    embed.add_field(name="/timeout", value="Timeout member", inline=True)
    embed.add_field(name="/commands", value="This list", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.command()
async def sync(ctx):
    if ctx.author.id == bot.owner_id:
        synced = await bot.tree.sync()
        await ctx.send(f"Synced {len(synced)} commands")
