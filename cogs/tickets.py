import discord
from discord.ext import commands
import database
import json
import asyncio
import datetime
import io

COOLDOWN = {}

TRANSCRIPT_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #36393f; color: #dcddde; margin: 0; padding: 20px; }
.msg { padding: 8px 16px; margin: 4px 0; display: flex; gap: 12px; align-items: flex-start; }
.msg:hover { background: rgba(255,255,255,0.03); border-radius: 4px; }
.avatar { width: 40px; height: 40px; border-radius: 50%; flex-shrink: 0; }
.msg-body { flex: 1; min-width: 0; }
.author { color: #fff; font-weight: 600; font-size: 0.95rem; display: inline; }
.timestamp { color: #72767d; font-size: 0.7rem; margin-left: 8px; }
.content { margin-top: 2px; font-size: 0.95rem; line-height: 1.4; word-wrap: break-word; }
.attachment { color: #00b0f4; text-decoration: none; }
.attachment:hover { text-decoration: underline; }
.header { text-align: center; padding: 20px; color: #b9bbbe; border-bottom: 1px solid #40444b; margin-bottom: 16px; }
.header h2 { margin: 0 0 4px; color: #fff; font-size: 1.2rem; }
.header p { margin: 0; font-size: 0.85rem; }
.system-msg { color: #72767d; font-size: 0.85rem; font-style: italic; text-align: center; padding: 4px; }
"""

STYLE_MAP = {
    "Primary": discord.ButtonStyle.primary,
    "Secondary": discord.ButtonStyle.secondary,
    "Success": discord.ButtonStyle.success,
    "Danger": discord.ButtonStyle.danger,
}

def default_panels():
    return [
        {"enabled": True, "label": "Support", "emoji": "🎫", "style": "Primary", "category_id": "", "channel_name_template": "ticket-{username}", "description": "Open a support ticket", "ticket_name": "Support"},
        {"enabled": True, "label": "Purchase", "emoji": "🛒", "style": "Success", "category_id": "", "channel_name_template": "purchase-{username}", "description": "Purchase inquiries", "ticket_name": "Purchase"},
        {"enabled": False, "label": "Report", "emoji": "🚨", "style": "Danger", "category_id": "", "channel_name_template": "report-{username}", "description": "Report a user", "ticket_name": "Report"},
        {"enabled": False, "label": "Apply", "emoji": "📝", "style": "Primary", "category_id": "", "channel_name_template": "apply-{username}", "description": "Staff applications", "ticket_name": "Apply"},
        {"enabled": False, "label": "Other", "emoji": "❓", "style": "Secondary", "category_id": "", "channel_name_template": "other-{username}", "description": "Other inquiries", "ticket_name": "Other"},
    ]

class TicketSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        try:
            await self._register_panels()
        except Exception as e:
            print(f"TicketSystem cog_load error: {e}")

    async def _respond(self, ctx_or_interaction, content=None, embed=None, view=None, ephemeral=False):
        if isinstance(ctx_or_interaction, discord.Interaction):
            if ctx_or_interaction.response.is_done():
                await ctx_or_interaction.followup.send(content=content, embed=embed, view=view, ephemeral=ephemeral)
            else:
                await ctx_or_interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=ephemeral)
        else:
            await ctx_or_interaction.send(content=content, embed=embed, view=view)

    async def _register_panels(self):
        guilds = await database.get_all_guilds()
        for gid in guilds:
            cfg = await database.get_ticket_config(gid)
            if cfg and cfg["enabled"] and cfg["panel_message_id"] and cfg["panel_channel_id"]:
                channel = self.bot.get_channel(int(cfg["panel_channel_id"]))
                if channel:
                    try:
                        msg = await channel.fetch_message(int(cfg["panel_message_id"]))
                        view = self._build_panel_view(gid, cfg)
                        if view:
                            self.bot.add_view(view, message_id=msg.id)
                    except:
                        pass

    def _build_panel_view(self, guild_id: str, cfg: dict):
        panels = cfg.get("panels", [])
        enabled_panels = [p for p in panels if p.get("enabled")]
        if not enabled_panels:
            return None
        if cfg.get("panel_type") == "select":
            view = TicketSelectView(guild_id, cfg, self.bot)
        else:
            view = TicketButtonView(guild_id, cfg, self.bot)
        return view

    async def _get_or_create_config(self, guild_id: str):
        cfg = await database.get_ticket_config(guild_id)
        if not cfg:
            import json
            await database.set_ticket_config(
                guild_id,
                enabled=False,
                panels=json.dumps(default_panels()),
                support_roles=json.dumps([])
            )
            cfg = await database.get_ticket_config(guild_id)
        return cfg

    def _has_support_role(self, member: discord.Member, cfg: dict) -> bool:
        role_ids = cfg.get("support_roles", [])
        if not role_ids:
            return member.guild_permissions.administrator
        for rid in role_ids:
            if member.get_role(int(rid)):
                return True
        return member.guild_permissions.administrator

    def _parse_style(self, style_str: str) -> discord.ButtonStyle:
        return STYLE_MAP.get(style_str, discord.ButtonStyle.primary)

    def _channel_name(self, template: str, member: discord.Member, ticket_number: int) -> str:
        name = template.replace("{username}", member.name.lower().replace(" ", "-"))
        name = name.replace("{user-id}", str(member.id))
        name = name.replace("{user-tag}", str(member).replace(" ", "-"))
        name = name.replace("{total-tickets}", str(ticket_number))
        name = name.replace("{category}", "ticket")
        name = name[:95]
        return name

    async def _send_log(self, guild: discord.Guild, cfg: dict, embed: discord.Embed, file=None):
        if not cfg.get("log_channel_id"):
            return
        channel = guild.get_channel(int(cfg["log_channel_id"]))
        if channel:
            kwargs = {"embed": embed}
            if file:
                kwargs["file"] = file
            await channel.send(**kwargs)

    async def _generate_transcript(self, channel: discord.TextChannel, ticket_type: str, ticket_number: int, created_at: str, closed_by: str):
        messages = []
        async for msg in channel.history(limit=None, oldest_first=True):
            if msg.author.bot and msg.content == "":
                continue
            author_name = str(msg.author)
            avatar_url = msg.author.display_avatar.url
            content = msg.content or ""
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            attachments = [a.url for a in msg.attachments]
            messages.append((author_name, avatar_url, content, timestamp, attachments))
        msg_html = ""
        for author_name, avatar_url, content, timestamp, attachments in messages:
            attach_html = ""
            if attachments:
                attach_html = '<br>'.join(f'<a class="attachment" href="{a}" target="_blank">📎 Attachment</a>' for a in attachments)
            content_escaped = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            msg_html += (
                f'<div class="msg">'
                f'<img class="avatar" src="{avatar_url}" alt="" loading="lazy">'
                f'<div class="msg-body">'
                f'<span class="author">{author_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")}</span>'
                f'<span class="timestamp">{timestamp}</span>'
                f'<div class="content">{content_escaped.replace(chr(10), "<br>")}{attach_html}</div>'
                f'</div></div>'
            )
        total = len(messages)
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Transcript - #{channel.name}</title>
<style>{TRANSCRIPT_CSS}</style>
</head>
<body>
<div class="header">
<h2>🎫 Ticket Transcript - {ticket_type} #{ticket_number}</h2>
<p>Channel: #{channel.name} | Created: {created_at} | Closed by: {closed_by} | Messages: {total}</p>
</div>
{msg_html}
</body>
</html>"""
        return io.BytesIO(html.encode("utf-8")), f"transcript-{channel.name}.html"

    # ─── Panel View ─────────────────────────────────────────

    async def _send_panel(self, ctx_or_interaction, channel: discord.TextChannel = None):
        guild_id = str(ctx_or_interaction.guild_id if hasattr(ctx_or_interaction, 'guild_id') else ctx_or_interaction.guild.id)
        cfg = await self._get_or_create_config(guild_id)
        if not cfg["enabled"]:
            await self._respond(ctx_or_interaction, "Tickets are not enabled. Configure them in the web dashboard first.", ephemeral=True)
            return
        target = channel or ctx_or_interaction.channel
        embed = discord.Embed(
            title="🎫 Create a Ticket",
            description="Click a button below to open a ticket. Our team will assist you shortly.",
            color=discord.Color.from_rgb(233, 69, 96)
        )
        view = self._build_panel_view(guild_id, cfg)
        if not view:
            await self._respond(ctx_or_interaction, "No ticket panels are configured. Add panels via the web dashboard.", ephemeral=True)
            return
        msg = await target.send(embed=embed, view=view)
        await database.set_ticket_config(guild_id, panel_channel_id=str(target.id), panel_message_id=str(msg.id))
        self.bot.add_view(view, message_id=msg.id)
        await self._respond(ctx_or_interaction, f"Panel sent to {target.mention}", ephemeral=True)

    # ─── Commands (defined in bot.py via @bot.tree.command) ──

    # ─── Interaction Listener ───────────────────────────────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("ticket_panel:"):
            await self._handle_panel_button(interaction, cid)
        elif cid.startswith("ticket_select:"):
            await self._handle_select(interaction)
        elif cid == "ticket_close":
            await self._handle_close_button(interaction)
        elif cid == "ticket_claim":
            await self._handle_claim_button(interaction)
        elif cid == "ticket_unclaim":
            await self._handle_unclaim_button(interaction)
        elif cid == "ticket_confirm_close":
            await self._handle_confirm_close(interaction)
        elif cid == "ticket_cancel_close":
            await self._handle_cancel_close(interaction)

    async def _handle_panel_button(self, interaction: discord.Interaction, cid: str):
        parts = cid.split(":")
        if len(parts) < 3:
            return
        guild_id = parts[1]
        idx = int(parts[2])
        cfg = await self._get_or_create_config(guild_id)
        panels = cfg.get("panels", [])
        if idx >= len(panels):
            await interaction.response.send_message("This panel is no longer configured.", ephemeral=True)
            return
        panel = panels[idx]
        await self._create_ticket(interaction, guild_id, cfg, panel)

    async def _handle_select(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        values = interaction.data.get("values", [])
        if not values:
            return
        idx = int(values[0])
        cfg = await self._get_or_create_config(guild_id)
        panels = cfg.get("panels", [])
        if idx >= len(panels):
            await interaction.response.send_message("This panel is no longer configured.", ephemeral=True)
            return
        panel = panels[idx]
        await self._create_ticket(interaction, guild_id, cfg, panel)

    async def _create_ticket(self, interaction: discord.Interaction, guild_id: str, cfg: dict, panel: dict):
        user = interaction.user
        guild = interaction.guild

        if await database.is_blacklisted(guild_id, str(user.id)):
            await interaction.response.send_message("You are blacklisted from creating tickets.", ephemeral=True)
            return

        now = datetime.datetime.now().timestamp()
        if user.id in COOLDOWN:
            remaining = COOLDOWN[user.id] - now
            if remaining > 0:
                await interaction.response.send_message(f"Please wait {int(remaining)}s before creating another ticket.", ephemeral=True)
                return

        open_count = await database.get_user_open_tickets(guild_id, str(user.id))
        max_tickets = cfg.get("max_tickets_per_user", 5)
        if open_count >= max_tickets:
            await interaction.response.send_message(f"You already have {open_count} open tickets (max {max_tickets}).", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        category_id = panel.get("category_id") or cfg.get("category_id") or ""
        category = None
        if category_id:
            category = guild.get_channel(int(category_id))

        ticket_number = await database.increment_ticket_number(guild_id)
        ticket_type = panel.get("ticket_name", "Support")
        channel_name = self._channel_name(
            panel.get("channel_name_template", "ticket-{username}"),
            user, ticket_number
        )

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True, manage_messages=True),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
        }
        for rid_str in cfg.get("support_roles", []):
            role = guild.get_role(int(rid_str))
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                topic=f"Ticket #{ticket_number} - {ticket_type} - {user}",
                overwrites=overwrites
            )
        except Exception as e:
            await interaction.followup.send(f"Failed to create ticket channel: {e}", ephemeral=True)
            return

        await database.create_ticket(
            str(channel.id), guild_id, str(user.id), ticket_number, ticket_type
        )

        COOLDOWN[user.id] = now + cfg.get("cooldown_seconds", 30)

        embed = discord.Embed(
            title=f"{ticket_type} Ticket #{ticket_number}",
            description=f"Welcome {user.mention}! Support staff will be with you shortly.\n\nPlease describe your issue and wait patiently.",
            color=discord.Color.from_rgb(233, 69, 96)
        )
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Type", value=ticket_type, inline=True)
        embed.add_field(name="Ticket", value=f"#{ticket_number}", inline=True)
        embed.set_footer(text=f"ID: {channel.id}")
        embed.timestamp = datetime.datetime.now()

        action_view = TicketActionView(guild_id, cfg)
        msg = await channel.send(embed=embed, view=action_view)
        await msg.pin()
        await channel.send(f"👋 {user.mention}, welcome to your ticket! Staff will assist you shortly.")

        log_embed = discord.Embed(title="Ticket Created", color=discord.Color.green())
        log_embed.add_field(name="User", value=user.mention)
        log_embed.add_field(name="Type", value=ticket_type)
        log_embed.add_field(name="Channel", value=channel.mention)
        log_embed.add_field(name="Ticket", value=f"#{ticket_number}")
        await self._send_log(guild, cfg, log_embed)

        await interaction.followup.send(f"✅ Ticket created: {channel.mention}", ephemeral=True)

    async def _handle_close_button(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        cfg = await self._get_or_create_config(guild_id)
        ticket = await database.get_active_ticket(str(interaction.channel_id))
        if not ticket or ticket["status"] != "open":
            await interaction.response.send_message("This is not an open ticket.", ephemeral=True)
            return
        await self._do_close(interaction, ticket, cfg)

    async def _do_close(self, ctx_or_interaction, ticket: dict, cfg: dict):
        guild = ctx_or_interaction.guild
        channel = ctx_or_interaction.channel
        user = ctx_or_interaction.user if hasattr(ctx_or_interaction, 'user') else ctx_or_interaction.author

        is_staff = self._has_support_role(user, cfg)
        is_creator = str(user.id) == ticket["user_id"]
        if not is_staff and not is_creator:
            await self._respond(ctx_or_interaction, "You don't have permission to close this ticket.", ephemeral=True)
            return

        if cfg.get("close_confirmation"):
            confirm_view = TicketConfirmView()
            embed = discord.Embed(
                title="Close Ticket?",
                description="Are you sure you want to close this ticket? This action can be undone by reopening.",
                color=discord.Color.orange()
            )
            embed.add_field(name="Closed by", value=user.mention)
            await self._respond(ctx_or_interaction, embed=embed, view=confirm_view)
            return

        await self._respond(ctx_or_interaction, "Closing ticket...", ephemeral=True)
        await self._finalize_close(ctx_or_interaction, ticket, cfg, user)

    async def _finalize_close(self, ctx_or_interaction, ticket: dict, cfg: dict, closer: discord.Member):
        channel = ctx_or_interaction.channel
        guild = ctx_or_interaction.guild

        await database.update_ticket(str(channel.id), status="closed", closed_by=str(closer.id), closed_at=datetime.datetime.now().isoformat())

        embed = discord.Embed(title="Ticket Closed", color=discord.Color.orange())
        embed.add_field(name="Closed by", value=closer.mention)
        embed.add_field(name="Ticket", value=f"{ticket['ticket_type']} #{ticket['ticket_number']}")
        await channel.send(embed=embed)

        transcript_file = None
        if cfg.get("transcript_enabled"):
            try:
                buf, fname = await self._generate_transcript(
                    channel, ticket["ticket_type"], ticket["ticket_number"],
                    ticket.get("created_at", "Unknown"), str(closer)
                )
                transcript_file = discord.File(buf, filename=fname)
            except:
                pass

        log_embed = discord.Embed(title="Ticket Closed", color=discord.Color.orange())
        log_embed.add_field(name="Ticket", value=f"#{channel.name} ({ticket['ticket_type']} #{ticket['ticket_number']})")
        log_embed.add_field(name="User", value=f"<@{ticket['user_id']}>")
        log_embed.add_field(name="Closed by", value=closer.mention)
        if ticket.get("claimed_by"):
            log_embed.add_field(name="Claimed by", value=f"<@{ticket['claimed_by']}>")
        await self._send_log(guild, cfg, log_embed, transcript_file)

        if cfg.get("dm_on_close"):
            try:
                creator = guild.get_member(int(ticket["user_id"]))
                if creator:
                    dm_embed = discord.Embed(
                        title=f"Ticket {ticket['ticket_type']} #{ticket['ticket_number']} Closed",
                        description=f"Your ticket in **{guild.name}** has been closed.",
                        color=discord.Color.orange()
                    )
                    dm_embed.add_field(name="Closed by", value=str(closer))
                    dm_kwargs = {"embed": dm_embed}
                    if transcript_file:
                        buf, fname = await self._generate_transcript(
                            channel, ticket["ticket_type"], ticket["ticket_number"],
                            ticket.get("created_at", "Unknown"), str(closer)
                        )
                        dm_kwargs["file"] = discord.File(buf, filename=fname)
                    await creator.send(**dm_kwargs)
            except:
                pass

        delete_seconds = cfg.get("delete_seconds", 3)
        countdown = await channel.send(f"🔒 Deleting this channel in {delete_seconds} seconds...")
        await asyncio.sleep(delete_seconds)
        await database.delete_active_ticket(str(channel.id))
        await channel.delete(reason=f"Ticket closed by {closer}")

    async def _handle_claim_button(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        cfg = await self._get_or_create_config(guild_id)
        ticket = await database.get_active_ticket(str(interaction.channel_id))
        if not ticket or ticket["status"] != "open":
            await interaction.response.send_message("This is not an open ticket.", ephemeral=True)
            return
        await self._do_claim(interaction, ticket, cfg)

    async def _do_claim(self, ctx_or_interaction, ticket: dict, cfg: dict):
        user = ctx_or_interaction.user if hasattr(ctx_or_interaction, 'user') else ctx_or_interaction.author
        guild = ctx_or_interaction.guild
        channel = ctx_or_interaction.channel

        if not self._has_support_role(user, cfg):
            await self._respond(ctx_or_interaction, "You don't have permission to claim tickets.", ephemeral=True)
            return

        if ticket.get("claimed_by"):
            await self._respond(ctx_or_interaction, f"This ticket is already claimed by <@{ticket['claimed_by']}>.", ephemeral=True)
            return

        await database.update_ticket(str(channel.id), claimed_by=str(user.id))

        embed = discord.Embed(title="Ticket Claimed", color=discord.Color.blue())
        embed.add_field(name="Claimed by", value=user.mention)
        embed.add_field(name="Ticket", value=f"{ticket['ticket_type']} #{ticket['ticket_number']}")
        msg = None
        async for m in channel.history(limit=20):
            if m.author == guild.me and m.embeds and m.pinned:
                msg = m
                break
        if msg:
            new_embed = msg.embeds[0]
            new_embed.add_field(name="Claimed by", value=user.mention, inline=True)
            new_view = TicketActionView(str(guild.id), cfg, claimed=True)
            await msg.edit(embed=new_embed, view=new_view)

        await self._respond(ctx_or_interaction, embed=embed)

        log_embed = discord.Embed(title="Ticket Claimed", color=discord.Color.blue())
        log_embed.add_field(name="Ticket", value=f"#{channel.name} ({ticket['ticket_type']} #{ticket['ticket_number']})")
        log_embed.add_field(name="Claimed by", value=user.mention)
        await self._send_log(guild, cfg, log_embed)

    async def _handle_unclaim_button(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        cfg = await self._get_or_create_config(guild_id)
        ticket = await database.get_active_ticket(str(interaction.channel_id))
        if not ticket or ticket["status"] != "open":
            await interaction.response.send_message("This is not an open ticket.", ephemeral=True)
            return
        await self._do_unclaim(interaction, ticket, cfg)

    async def _do_unclaim(self, ctx_or_interaction, ticket: dict, cfg: dict):
        user = ctx_or_interaction.user if hasattr(ctx_or_interaction, 'user') else ctx_or_interaction.author
        guild = ctx_or_interaction.guild
        channel = ctx_or_interaction.channel

        if ticket.get("claimed_by") != str(user.id):
            await self._respond(ctx_or_interaction, "You don't have permission to unclaim this ticket.", ephemeral=True)
            return

        await database.update_ticket(str(channel.id), claimed_by="")

        embed = discord.Embed(title="Ticket Unclaimed", color=discord.Color.default())
        embed.add_field(name="Unclaimed by", value=user.mention)
        embed.add_field(name="Ticket", value=f"{ticket['ticket_type']} #{ticket['ticket_number']}")

        msg = None
        async for m in channel.history(limit=20):
            if m.author == guild.me and m.embeds and m.pinned:
                msg = m
                break
        if msg:
            new_embed = msg.embeds[0]
            new_view = TicketActionView(str(guild.id), cfg, claimed=False)
            await msg.edit(embed=new_embed, view=new_view)

        await self._respond(ctx_or_interaction, embed=embed)

        log_embed = discord.Embed(title="Ticket Unclaimed", color=discord.Color.default())
        log_embed.add_field(name="Ticket", value=f"#{channel.name} ({ticket['ticket_type']} #{ticket['ticket_number']})")
        log_embed.add_field(name="Unclaimed by", value=user.mention)
        await self._send_log(guild, cfg, log_embed)

    async def _handle_confirm_close(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        cfg = await self._get_or_create_config(guild_id)
        ticket = await database.get_active_ticket(str(interaction.channel_id))
        if not ticket or ticket["status"] != "open":
            await interaction.response.send_message("This ticket is no longer open.", ephemeral=True)
            return
        await interaction.response.defer()
        await self._finalize_close(interaction, ticket, cfg, interaction.user)

    async def _handle_cancel_close(self, interaction: discord.Interaction):
        await interaction.response.send_message("Close cancelled.", ephemeral=True)
        try:
            await interaction.message.delete()
        except:
            pass


class TicketButtonView(discord.ui.View):
    def __init__(self, guild_id: str, cfg: dict, bot):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.bot = bot
        panels = cfg.get("panels", [])
        style_map = {
            "Primary": discord.ButtonStyle.primary,
            "Secondary": discord.ButtonStyle.secondary,
            "Success": discord.ButtonStyle.success,
            "Danger": discord.ButtonStyle.danger,
        }
        for i, panel in enumerate(panels):
            if not panel.get("enabled"):
                continue
            style = style_map.get(panel.get("style", "Primary"), discord.ButtonStyle.primary)
            emoji = panel.get("emoji") or None
            btn = discord.ui.Button(
                style=style,
                label=panel.get("label", "Support"),
                emoji=emoji,
                custom_id=f"ticket_panel:{guild_id}:{i}"
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, idx: int):
        async def callback(interaction: discord.Interaction):
            cog = self.bot.get_cog("TicketSystem")
            if cog:
                await cog._handle_panel_button(interaction, f"ticket_panel:{self.guild_id}:{idx}")
        return callback


class TicketSelectView(discord.ui.View):
    def __init__(self, guild_id: str, cfg: dict, bot):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.bot = bot
        panels = cfg.get("panels", [])
        options = []
        for i, panel in enumerate(panels):
            if not panel.get("enabled"):
                continue
            emoji = panel.get("emoji") or None
            opt = discord.SelectOption(
                label=panel.get("label", "Support"),
                value=str(i),
                description=panel.get("description", ""),
                emoji=emoji
            )
            options.append(opt)
        if options:
            select = discord.ui.Select(
                custom_id=f"ticket_select:{guild_id}",
                placeholder="Choose a ticket type...",
                options=options
            )
            select.callback = self._make_select_callback()
            self.add_item(select)

    def _make_select_callback(self):
        async def callback(interaction: discord.Interaction):
            cog = self.bot.get_cog("TicketSystem")
            if cog:
                await cog._handle_select(interaction)
        return callback


class TicketActionView(discord.ui.View):
    def __init__(self, guild_id: str, cfg: dict, claimed: bool = False):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        style_map = {
            "Primary": discord.ButtonStyle.primary,
            "Secondary": discord.ButtonStyle.secondary,
            "Success": discord.ButtonStyle.success,
            "Danger": discord.ButtonStyle.danger,
        }
        close_btn = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Close",
            emoji="🔒",
            custom_id="ticket_close"
        )
        self.add_item(close_btn)

        if claimed:
            unclaim_btn = discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label="Unclaim",
                emoji="↩️",
                custom_id="ticket_unclaim"
            )
            self.add_item(unclaim_btn)
        else:
            claim_btn = discord.ui.Button(
                style=discord.ButtonStyle.success,
                label="Claim",
                emoji="👋",
                custom_id="ticket_claim"
            )
            self.add_item(claim_btn)


class TicketConfirmView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Confirm Close", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_confirm_close")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("TicketSystem")
        if cog:
            await cog._handle_confirm_close(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="❌", custom_id="ticket_cancel_close")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Close cancelled.", ephemeral=True)
        try:
            await interaction.message.delete()
        except:
            pass


async def setup(bot):
    await bot.add_cog(TicketSystem(bot))
