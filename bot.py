# Imports

import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import sqlite3
import asyncio
import datetime
import re
import random
import os
import math
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta

# Config

DB_PATH = "botdata.sqlite"
DEFAULT_TICKET_PERM_ROLE_NAME = "Administator"  # role name to give ticket access if present

# Intents

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Database Helpers

def ensure_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id INTEGER PRIMARY KEY,
        welcome_text TEXT,
        welcome_channel INTEGER,
        goodbye_text TEXT,
        goodbye_channel INTEGER,
        ticket_channel INTEGER,
        ticket_message INTEGER,
        ticket_category INTEGER,
        ticket_button_label TEXT
        autorole_id INTEGER  -- new column
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS giveaways (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER,
        channel_id INTEGER,
        message_id INTEGER,
        prize TEXT,
        ends_at TEXT,
        winners_count INTEGER,
        host_id INTEGER,
        created_at TEXT,
        ended INTEGER DEFAULT 0
    )""")
    conn.commit()
    conn.close()

def get_setting(guild_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT welcome_text, welcome_channel, goodbye_text, goodbye_channel, ticket_channel, ticket_message, ticket_category, ticket_button_label FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "welcome_text": row[0],
            "welcome_channel": row[1],
            "goodbye_text": row[2],
            "goodbye_channel": row[3],
            "ticket_channel": row[4],
            "ticket_message": row[5],
            "ticket_category": row[6],
            "ticket_button_label": row[7] or "Open Ticket"
        }
    else:
        return None

def set_setting_value(guild_id: int, key: str, value):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT guild_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
    if cur.fetchone():
        cur.execute(f"UPDATE guild_settings SET {key} = ? WHERE guild_id = ?", (value, guild_id))
    else:
        defaults = {
            "welcome_text": None,
            "welcome_channel": None,
            "goodbye_text": None,
            "goodbye_channel": None,
            "ticket_channel": None,
            "ticket_message": None,
            "ticket_category": None,
            "ticket_button_label": "Open Ticket"
        }
        defaults[key] = value
        cur.execute("""
        INSERT INTO guild_settings (guild_id, welcome_text, welcome_channel, goodbye_text, goodbye_channel, ticket_channel, ticket_message, ticket_category, ticket_button_label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (guild_id, defaults["welcome_text"], defaults["welcome_channel"], defaults["goodbye_text"], defaults["goodbye_channel"], defaults["ticket_channel"], defaults["ticket_message"], defaults["ticket_category"], defaults["ticket_button_label"]))
    conn.commit()
    conn.close()

def create_giveaway(guild_id, channel_id, message_id, prize, ends_at_iso, winners_count, host_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.datetime.utcnow().isoformat()
    cur.execute("INSERT INTO giveaways (guild_id, channel_id, message_id, prize, ends_at, winners_count, host_id, created_at, ended) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (guild_id, channel_id, message_id, prize, ends_at_iso, winners_count, host_id, now))
    gid = cur.lastrowid
    conn.commit()
    conn.close()
    return gid

def set_autorole(guild_id: int, role_id: int):
    set_setting_value(guild_id, "autorole_id", role_id)

def get_autorole(guild_id: int):
    settings = get_setting(guild_id)
    if settings:
        role_id = settings.get("autorole_id")
        return role_id
    return None

def get_active_giveaways():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, guild_id, channel_id, message_id, prize, ends_at, winners_count, host_id, ended FROM giveaways WHERE ended = 0")
    rows = cur.fetchall()
    conn.close()
    return rows

def mark_giveaway_ended(gid):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE giveaways SET ended = 1 WHERE id = ?", (gid,))
    conn.commit()
    conn.close()

def get_giveaway_by_id(gid):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, guild_id, channel_id, message_id, prize, ends_at, winners_count, host_id, ended FROM giveaways WHERE id = ?", (gid,))
    row = cur.fetchone()
    conn.close()
    return row

# Utilities

def parse_duration_to_timedelta(s: str) -> timedelta:
    pattern = r"(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?"
    m = re.fullmatch(pattern, s.strip().lower())
    if not m:
        raise ValueError("Invalid duration format. Use like '1d2h', '2h30m', '45m', etc.")
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    seconds = int(m.group(4) or 0)
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

def human_dt_from_iso(s: str) -> str:
    dt = datetime.datetime.fromisoformat(s)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

# Views / UI
class TicketPanelView(ui.View):
    def __init__(self, bot_ref, label="Open Ticket", *, timeout=None):
        super().__init__(timeout=timeout)
        self.bot = bot_ref
        self.add_item(TicketOpenButton(label))

class TicketOpenButton(ui.Button):
    def __init__(self, label="Open Ticket"):
        super().__init__(label=label, style=discord.ButtonStyle.primary, emoji="📩")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        settings = get_setting(guild.id) or {}
        # Create channel name
        name = f"ticket-{interaction.user.id}"
        existing = discord.utils.get(guild.text_channels, name=name)
        if existing:
            await interaction.followup.send("You already have an open ticket: " + existing.mention, ephemeral=True)
            return
        # Determine category if set
        category = None
        if settings and settings.get("ticket_category"):
            try:
                category = guild.get_channel(int(settings["ticket_category"]))
            except Exception:
                category = None
        # create channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        }
        staff_role = discord.utils.find(lambda r: r.name == DEFAULT_TICKET_PERM_ROLE_NAME, guild.roles)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        channel = await guild.create_text_channel(name=name, category=category, overwrites=overwrites, reason=f"Ticket opened by {interaction.user}")
        close_view = ui.View()
        close_view.add_item(TicketCloseButton())
        await channel.send(f"Hello {interaction.user.mention}! A staff member will be with you shortly.", view=close_view)
        await interaction.followup.send(f"Ticket created: {channel.mention}", ephemeral=True)

class TicketCloseButton(ui.Button):
    def __init__(self):
        super().__init__(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="❌")

    async def callback(self, interaction: discord.Interaction):
        ch = interaction.channel
        if not ch.name.startswith("ticket-") and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("This button can only be used in ticket channels or by staff.", ephemeral=True)
            return
        await interaction.response.send_message("Closing ticket in 5 seconds...", ephemeral=True)
        await asyncio.sleep(5)
        try:
            await ch.delete(reason=f"Ticket closed by {interaction.user}")
        except Exception as e:
            await interaction.followup.send("Failed to delete channel: " + str(e), ephemeral=True)

# Giveaways background task

@tasks.loop(seconds=20.0)
async def giveaway_checker():
    rows = get_active_giveaways()
    now = datetime.datetime.utcnow()
    for row in rows:
        gid, guild_id, channel_id, message_id, prize, ends_at_iso, winners_count, host_id, ended = row
        ends_at = datetime.datetime.fromisoformat(ends_at_iso)
        if now >= ends_at:
            try:
                guild = bot.get_guild(guild_id)
                if not guild:
                    guild = await bot.fetch_guild(guild_id)
                channel = guild.get_channel(channel_id)
                if not channel:
                    channel = await bot.fetch_channel(channel_id)
                message = await channel.fetch_message(message_id)
                users = set()
                for reaction in message.reactions:
                    async for u in reaction.users():
                            if not u.bot:
                                users.add(u)
                users = list(users)
                if not users:
                    await channel.send(f"No valid entries for giveaway **{prize}** (id {gid}). No winners.")
                else:
                    winners_count = max(1, int(winners_count))
                    winners_count = min(winners_count, len(users))
                    winners = random.sample(users, winners_count)
                    mentions = ", ".join(w.mention for w in winners)
                    await channel.send(f"🎉 **GIVEAWAY ENDED** 🎉\nPrize: **{prize}**\nWinners ({winners_count}): {mentions}")
                mark_giveaway_ended(gid)
            except Exception as e:
                print("Error ending giveaway", gid, e)

# @bot.Events

@bot.event
async def on_member_join(member: discord.Member):
    settings = get_setting(member.guild.id)

    # Welcome messages
    if settings and settings.get("welcome_text") and settings.get("welcome_channel"):
        ch = member.guild.get_channel(int(settings["welcome_channel"]))
        if ch:
            text = settings["welcome_text"].replace("{user}", member.mention).replace("{name}", member.name).replace("{guild}", member.guild.name)
            try:
                await ch.send(text)
            except:
                pass

    # Autoroles
    if settings and settings.get("autorole_id"):
        role = member.guild.get_role(int(settings["autorole_id"]))
        if role:
            try:
                await member.add_roles(role, reason="Autorole on join")
            except Exception as e:
                print(f"Failed to add autorole: {e}")

@bot.event
async def on_member_join(member: discord.Member):
    settings = get_setting(member.guild.id)
    if settings and settings.get("welcome_text") and settings.get("welcome_channel"):
        ch = member.guild.get_channel(int(settings["welcome_channel"]))
        if ch:
            text = settings["welcome_text"].replace("{user}", member.mention).replace("{name}", member.name).replace("{guild}", member.guild.name)
            try:
                await ch.send(text)
            except:
                pass

@bot.event
async def on_member_remove(member: discord.Member):
    settings = get_setting(member.guild.id)
    if settings and settings.get("goodbye_text") and settings.get("goodbye_channel"):
        ch = member.guild.get_channel(int(settings["goodbye_channel"]))
        if ch:
            text = settings["goodbye_text"].replace("{user}", f"{member.name}#{member.discriminator}").replace("{guild}", member.guild.name)
            try:
                await ch.send(text)
            except:
                pass

# Slash Cmds

# Welcome / Byeee
@tree.command(name="setwelcome", description="Set the welcome message (supports {user}, {name}, {guild}).")
@app_commands.describe(channel="Channel to send welcome messages in", message="Message text")
@app_commands.checks.has_permissions(administrator=True)
async def setwelcome(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    set_setting_value(interaction.guild.id, "welcome_channel", str(channel.id))
    set_setting_value(interaction.guild.id, "welcome_text", message)
    await interaction.response.send_message(f"✅ Welcome message set for {channel.mention}", ephemeral=True)

@tree.command(name="setgoodbye", description="Set the goodbye message (supports {user}, {guild}).")
@app_commands.describe(channel="Channel to send goodbye messages in", message="Message text")
@app_commands.checks.has_permissions(administrator=True)
async def setgoodbye(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    set_setting_value(interaction.guild.id, "goodbye_channel", str(channel.id))
    set_setting_value(interaction.guild.id, "goodbye_text", message)
    await interaction.response.send_message(f"✅ Goodbye message set for {channel.mention}", ephemeral=True)

# Autorole Cmd
@tree.command(name="setautorole", description="Set a role to assign automatically when someone joins")
@app_commands.describe(role="Role to assign")
@app_commands.checks.has_permissions(administrator=True)
async def setautorole(interaction: discord.Interaction, role: discord.Role):
    set_autorole(interaction.guild.id, role.id)
    await interaction.response.send_message(f"✅ Autorole set to {role.name}", ephemeral=True)

# Ticket Panel Setups
@tree.command(name="setticketpanel", description="Create a ticket panel with customizable open button.")
@app_commands.describe(channel="Channel to post the ticket panel", title="Panel title", description="Panel description", category="Optional category", button_label="Button text")
@app_commands.checks.has_permissions(manage_guild=True)
async def setticketpanel(interaction: discord.Interaction, channel: discord.TextChannel, title: str, description: str, category: discord.CategoryChannel = None, button_label: str = "Open Ticket"):
    await interaction.response.defer(ephemeral=True)
    view = TicketPanelView(bot, label=button_label)
    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    msg = await channel.send(embed=embed, view=view)
    set_setting_value(interaction.guild.id, "ticket_channel", str(channel.id))
    set_setting_value(interaction.guild.id, "ticket_message", str(msg.id))
    set_setting_value(interaction.guild.id, "ticket_category", str(category.id) if category else None)
    set_setting_value(interaction.guild.id, "ticket_button_label", button_label)
    await interaction.followup.send(f"✅ Ticket panel deployed in {channel.mention}.", ephemeral=True)

# Giveaway shi
@tree.command(name="startgiveaway", description="Start a giveaway.")
@app_commands.describe(channel="Channel to post", prize="Prize text", duration="Duration like '1h30m'", winners="Number of winners")
@app_commands.checks.has_permissions(manage_guild=True)
async def startgiveaway(interaction: discord.Interaction, channel: discord.TextChannel, prize: str, duration: str, winners: int = 1):
    await interaction.response.defer(ephemeral=True)
    try:
        td = parse_duration_to_timedelta(duration)
    except Exception as e:
        await interaction.followup.send("Invalid duration: " + str(e), ephemeral=True)
        return
    ends_at = datetime.datetime.utcnow() + td
    ends_iso = ends_at.isoformat()
    embed = discord.Embed(title="🎉 Giveaway 🎉", description=prize)
    embed.add_field(name="Ends at (UTC)", value=human_dt_from_iso(ends_iso))
    embed.add_field(name="Winners", value=str(winners))
    embed.set_footer(text=f"Hosted by {interaction.user}")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("🎉")
    gid = create_giveaway(interaction.guild.id, channel.id, msg.id, prize, ends_iso, winners, interaction.user.id)
    await interaction.followup.send(f"✅ Giveaway started (id {gid}) in {channel.mention}.", ephemeral=True)

@tree.command(name="endgiveaway", description="End a giveaway now.")
@app_commands.describe(giveaway_id="ID of the giveaway")
@app_commands.checks.has_permissions(manage_guild=True)
async def endgiveaway(interaction: discord.Interaction, giveaway_id: int):
    row = get_giveaway_by_id(giveaway_id)
    if not row:
        await interaction.response.send_message("Giveaway not found.", ephemeral=True)
        return
    gid, guild_id, channel_id, message_id, prize, ends_at_iso, winners_count, host_id, ended = row
    if ended:
        await interaction.response.send_message("Giveaway already ended.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        users = set()
        for reaction in message.reactions:
            async for u in reaction.users():
                if not u.bot:
                    users.add(u)
        users = list(users)
        if not users:
            await channel.send(f"No valid entries for giveaway **{prize}** (id {gid}). No winners.")
        else:
            winners_count = max(1, int(winners_count))
            winners_count = min(winners_count, len(users))
            winners = random.sample(users, winners_count)
            mentions = ", ".join(w.mention for w in winners)
            await channel.send(f"🎉 **GIVEAWAY ENDED** 🎉\nPrize: **{prize}**\nWinners ({winners_count}): {mentions}")
        mark_giveaway_ended(gid)
        await interaction.followup.send(f"✅ Giveaway {gid} ended.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send("Error ending giveaway: " + str(e), ephemeral=True)

# Super tuff discord mod commands 😁

@tree.command(name="roleinfo", description="Get info about a role")
@app_commands.describe(role="Role to check")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    embed = discord.Embed(title=f"Role info: {role.name}", color=role.color)
    embed.add_field(name="ID", value=role.id)
    embed.add_field(name="Members", value=len(role.members))
    embed.add_field(name="Hoisted?", value=role.hoist)
    embed.add_field(name="Mentionable?", value=role.mentionable)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="roleadd", description="Add a role to a member")
@app_commands.describe(user="User to give role", role="Role to give")
@app_commands.checks.has_permissions(manage_roles=True)
async def roleadd(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    await user.add_roles(role, reason=f"By {interaction.user}")
    await interaction.response.send_message(f"✅ Added {role.name} to {user.display_name}", ephemeral=True)

@tree.command(name="roleremove", description="Remove a role from a member")
@app_commands.describe(user="User to remove role from", role="Role to remove")
@app_commands.checks.has_permissions(manage_roles=True)
async def roleremove(interaction: discord.Interaction, user: discord.Member, role: discord.Role):
    await user.remove_roles(role, reason=f"By {interaction.user}")
    await interaction.response.send_message(f"✅ Removed {role.name} from {user.display_name}", ephemeral=True)

@tree.command(name="muteall", description="Mute everyone in a voice channel")
@app_commands.describe(channel="Voice channel to mute")
@app_commands.checks.has_permissions(moderate_members=True)
async def muteall(interaction: discord.Interaction, channel: discord.VoiceChannel):
    count = 0
    for member in channel.members:
        try:
            await member.edit(mute=True)
            count += 1
        except:
            pass
    await interaction.response.send_message(f"🔇 Muted {count} members in {channel.name}", ephemeral=True)

@tree.command(name="purge", description="Delete X number of messages")
@app_commands.describe(amount="Number of messages to delete")
@app_commands.checks.has_permissions(manage_messages=True)
async def purge(interaction: discord.Interaction, amount: int):
    if amount < 1:
        await interaction.response.send_message("❌ Amount must be at least 1.", ephemeral=True)
        return
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.response.send_message(f"🗑️ Deleted {len(deleted)} messages.", ephemeral=True)

@tree.command(name="warn", description="Warn a member")
@app_commands.describe(user="User to warn", reason="Reason for warning")
@app_commands.checks.has_permissions(kick_members=True)
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
    # store warnings in DB
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            guild_id INTEGER,
            user_id INTEGER,
            moderator_id INTEGER,
            reason TEXT,
            timestamp TEXT
        )
    """)
    cur.execute("INSERT INTO warnings (guild_id, user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
                (interaction.guild.id, user.id, interaction.user.id, reason, datetime.datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    await interaction.response.send_message(f"⚠️ {user.mention} has been warned for: {reason}", ephemeral=True)

@tree.command(name="warnings", description="Check warnings of a member")
@app_commands.describe(user="User to check")
@app_commands.checks.has_permissions(kick_members=True)
async def warnings(interaction: discord.Interaction, user: discord.Member):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT moderator_id, reason, timestamp FROM warnings WHERE guild_id=? AND user_id=?", (interaction.guild.id, user.id))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await interaction.response.send_message(f"{user.mention} has no warnings.", ephemeral=True)
        return
    embed = discord.Embed(title=f"Warnings for {user}", color=discord.Color.orange())
    for i, (mod_id, reason, timestamp) in enumerate(rows, start=1):
        mod = interaction.guild.get_member(mod_id)
        embed.add_field(name=f"#{i}", value=f"By: {mod.mention if mod else mod_id}\nReason: {reason}\nTime: {timestamp}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="tempmute", description="Temporarily mute a user")
@app_commands.describe(user="User to mute", minutes="Duration in minutes")
@app_commands.checks.has_permissions(moderate_members=True)
async def tempmute(interaction: discord.Interaction, user: discord.Member, minutes: int):
    await user.edit(mute=True, reason=f"Tempmuted by {interaction.user}")
    await interaction.response.send_message(f"🔇 {user.mention} muted for {minutes} minutes.", ephemeral=True)
    await asyncio.sleep(minutes * 60)
    try:
        await user.edit(mute=False)
        await interaction.followup.send(f"🔊 {user.mention} has been unmuted.", ephemeral=True)
    except:
        pass

@tree.command(name="rolehas", description="Check if a user has a role")
@app_commands.describe(member="Member to check", role="Role to check for")
async def rolehas(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    has_role = role in member.roles
    await interaction.response.send_message(f"{member.mention} {'has' if has_role else 'does not have'} the role {role.name}.", ephemeral=True)

@tree.command(name="rolelist", description="List all roles a user has")
@app_commands.describe(member="Member to list roles for")
async def rolelist(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    roles = [r.name for r in member.roles if r.name != "@everyone"]
    await interaction.response.send_message(f"{member.mention} roles: {', '.join(roles) if roles else 'No roles'}", ephemeral=True)

@tree.command(name="rolealladd", description="Give a role to everyone")
@app_commands.describe(role="Role to give to all members")
@app_commands.checks.has_permissions(manage_roles=True)
async def rolealladd(interaction: discord.Interaction, role: discord.Role):
    count = 0
    for member in interaction.guild.members:
        if role not in member.roles:
            try:
                await member.add_roles(role, reason=f"Role added to all by {interaction.user}")
                count += 1
            except:
                pass
    await interaction.response.send_message(f"✅ Role {role.name} added to {count} members.", ephemeral=True)

@tree.command(name="roleallremove", description="Remove a role from everyone")
@app_commands.describe(role="Role to remove from all members")
@app_commands.checks.has_permissions(manage_roles=True)
async def roleallremove(interaction: discord.Interaction, role: discord.Role):
    count = 0
    for member in interaction.guild.members:
        if role in member.roles:
            try:
                await member.remove_roles(role, reason=f"Role removed from all by {interaction.user}")
                count += 1
            except:
                pass
    await interaction.response.send_message(f"✅ Role {role.name} removed from {count} members.", ephemeral=True)

# Random ass cool commands
@tree.command(name="pick", description="Pick a random choice from multiple options")
@app_commands.describe(options="Comma-separated list of options")
async def pick(interaction: discord.Interaction, options: str):
    choices = [x.strip() for x in options.split(",") if x.strip()]
    if not choices:
        await interaction.response.send_message("No options provided.", ephemeral=True)
        return
    await interaction.response.send_message(f"🎯 I pick: **{random.choice(choices)}**", ephemeral=True)


@tree.command(name="mock", description="Mock text in spongebob style")
@app_commands.describe(text="Text to mock")
async def mock(interaction: discord.Interaction, text: str):
    mocked = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text))
    await interaction.response.send_message(f"🗣️ {mocked}", ephemeral=True)


@tree.command(name="ascii", description="Convert text to ASCII art (simple)")
@app_commands.describe(text="Text to convert")
async def ascii(interaction: discord.Interaction, text: str):
    art = "```\n" + " ".join(c for c in text.upper()) + "\n```"
    await interaction.response.send_message(art, ephemeral=True)


# 
@tree.command(name="calc", description="Calculate an expression")
async def calc(interaction: discord.Interaction, expression: str):
    await interaction.response.defer(ephemeral=True)  # acknowledge first
    try:
        result = eval(expression)
        await interaction.followup.send(f"🧮 Result: {result}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)


@tree.command(name="hexcolor", description="Get a random hex color or convert to color")
@app_commands.describe(hex="Optional hex code like #ff0000")
async def hexcolor(interaction: discord.Interaction, hex: str = None):
    if hex:
        try:
            val = int(hex.replace("#", ""), 16)
            await interaction.response.send_message(f"🎨 Hex {hex} -> RGB {((val>>16)&255, (val>>8)&255, val&255)}", ephemeral=True)
        except:
            await interaction.response.send_message("Invalid hex", ephemeral=True)
    else:
        val = random.randint(0, 0xFFFFFF)
        await interaction.response.send_message(f"🎨 Random hex: #{val:06X}", ephemeral=True)


@tree.command(name="weather", description="Get fake weather report for fun")
@app_commands.describe(location="City or place")
async def weather(interaction: discord.Interaction, location: str):
    temps = random.randint(-10, 40)
    desc = random.choice(["Sunny", "Cloudy", "Rainy", "Stormy", "Windy"])
    await interaction.response.send_message(f"☀️ Weather in {location}: {temps}°C, {desc}", ephemeral=True)


@tree.command(name="fact", description="Get a random fact")
async def fact(interaction: discord.Interaction):
    facts = [
        "Honey never spoils.",
        "Bananas are berries, but strawberries aren't.",
        "Octopuses have three hearts."
    ]
    await interaction.response.send_message(f"📚 {random.choice(facts)}", ephemeral=True)

# Utility commands

# Reminder
@tree.command(name="remindme", description="Set a reminder")
async def remindme(interaction: discord.Interaction, minutes: int, message: str):
    await interaction.response.send_message(
        f"⏰ Reminder set for {minutes} minutes from now.", ephemeral=True
    )
    await asyncio.sleep(minutes * 60)
    await interaction.followup.send(f"⏰ Reminder: {message}", ephemeral=True)


@tree.command(name="avatar", description="Get user's avatar")
@app_commands.describe(user="User to get avatar of")
async def avatar(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    embed = discord.Embed(title=f"{user}'s Avatar")
    embed.set_image(url=user.display_avatar.url)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="serverroles", description="List all roles in the server")
async def serverroles(interaction: discord.Interaction):
    roles = [role.name for role in interaction.guild.roles]
    await interaction.response.send_message(f"📜 Roles in this server:\n{', '.join(roles)}", ephemeral=True)


@tree.command(name="poll", description="Create a poll")
async def poll(interaction: discord.Interaction, question: str):
    await interaction.response.defer()  # acknowledge immediately
    msg = await interaction.followup.send(f"📊 Poll: {question}")  # must NOT be ephemeral
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")


@tree.command(name="servericon", description="Get the server icon")
async def servericon(interaction: discord.Interaction):
    icon = interaction.guild.icon
    if icon:
        await interaction.response.send_message(f"🖼️ Server icon:", embed=discord.Embed().set_image(url=icon.url), ephemeral=True)
    else:
        await interaction.response.send_message("This server has no icon.", ephemeral=True)


@tree.command(name="uptime", description="Check bot uptime")
async def uptime(interaction: discord.Interaction):
    now = datetime.datetime.utcnow()
    delta = now - bot.launch_time
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    await interaction.response.send_message(f"⏱️ Uptime: {hours}h {minutes}m {seconds}s", ephemeral=True)

# Sigma cool fun commands
@tree.command(name="8ball", description="Ask the magic 8-ball a question")
@app_commands.describe(question="Your question")
async def eight_ball(interaction: discord.Interaction, question: str):
    responses = [
        "Yes", "No", "Maybe", "Definitely", "I don't think so", "Absolutely", "Ask again later"
    ]
    await interaction.response.send_message(f"🎱 Question: {question}\nAnswer: **{random.choice(responses)}**", ephemeral=True)


@tree.command(name="compliment", description="Get a random compliment")
async def compliment(interaction: discord.Interaction):
    compliments = [
        "You're amazing!", "You're awesome!", "You light up the room!", "You have a great sense of humor!",
        "You're a fantastic friend!"
    ]
    await interaction.response.send_message(f"💖 {random.choice(compliments)}", ephemeral=True)


@tree.command(name="joke", description="Get a random joke")
async def joke(interaction: discord.Interaction):
    jokes = [
        "Why did the chicken cross the road? To get to the other side!",
        "I told my computer I needed a break, and it said 'No problem, I'll go to sleep.'",
        "Why don’t skeletons fight each other? They don’t have the guts."
    ]
    await interaction.response.send_message(f"😂 {random.choice(jokes)}", ephemeral=True)


@tree.command(name="dadjoke", description="Get a random dad joke")
async def dadjoke(interaction: discord.Interaction):
    dad_jokes = [
        "I'm reading a book on anti-gravity. It's impossible to put down!",
        "Why did the scarecrow win an award? Because he was outstanding in his field.",
        "I would tell you a joke about construction, but I'm still working on it."
    ]
    await interaction.response.send_message(f"😎 {random.choice(dad_jokes)}", ephemeral=True)


@tree.command(name="inspire", description="Get a random inspirational quote")
async def inspire(interaction: discord.Interaction):
    quotes = [
        "The best way to get started is to quit talking and begin doing. – Walt Disney",
        "Don’t let yesterday take up too much of today. – Will Rogers",
        "You learn more from failure than from success."
    ]
    await interaction.response.send_message(f"🌟 {random.choice(quotes)}", ephemeral=True)


# Fun / Util commands
@tree.command(name="ping", description="Bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! {round(bot.latency*1000)}ms", ephemeral=True)

@tree.command(name="coinflip", description="Flip a coin")
async def coinflip(interaction: discord.Interaction):
    await interaction.response.send_message(f"🎲 You got **{'Heads' if random.choice([True, False]) else 'Tails'}**!", ephemeral=True)

@tree.command(name="roll", description="Roll a dice (1-100)")
async def roll(interaction: discord.Interaction):
    await interaction.response.send_message(f"🎲 You rolled **{random.randint(1,100)}**", ephemeral=True)

@tree.command(name="randomnum", description="Get a random number in a range")
@app_commands.describe(min="Minimum", max="Maximum")
async def randomnum(interaction: discord.Interaction, min: int, max: int):
    if min > max:
        min, max = max, min
    await interaction.response.send_message(f"🎲 Random number: **{random.randint(min,max)}**", ephemeral=True)

@tree.command(name="say", description="Bot repeats your message")
@app_commands.describe(message="Message to say")
async def say(interaction: discord.Interaction, message: str):
    await interaction.response.send_message(message)

@tree.command(name="serverinfo", description="Get server info")
async def serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=g.name, color=discord.Color.blurple())
    embed.add_field(name="ID", value=g.id)
    embed.add_field(name="Members", value=g.member_count)
    embed.add_field(name="Owner", value=g.owner)
    embed.add_field(name="Channels", value=len(g.channels))
    embed.set_thumbnail(url=g.icon.url if g.icon else discord.Embed.Empty)
    await interaction.response.send_message(embed=embed)

@tree.command(name="userinfo", description="Get info about a user")
@app_commands.describe(user="Member to get info for")
async def userinfo(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    embed = discord.Embed(title=f"{user}", color=discord.Color.green())
    embed.add_field(name="ID", value=user.id)
    embed.add_field(name="Joined", value=user.joined_at.strftime("%Y-%m-%d %H:%M:%S"))
    embed.add_field(name="Created", value=user.created_at.strftime("%Y-%m-%d %H:%M:%S"))
    embed.add_field(name="Bot?", value=user.bot)
    embed.set_thumbnail(url=user.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="roles", description="List all server roles")
async def roles(interaction: discord.Interaction):
    rls = [r.name for r in sorted(interaction.guild.roles, key=lambda x: x.position, reverse=True) if r.name != "@everyone"]
    await interaction.response.send_message("Roles:\n" + "\n".join(rls), ephemeral=True)

@tree.command(name="clear", description="Delete X number of messages")
@app_commands.describe(amount="Number of messages to delete")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int = 5):
    if amount < 1:
        await interaction.response.send_message("❌ Amount must be at least 1.", ephemeral=True)
        return
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.response.send_message(f"✅ Deleted {len(deleted)} messages.", ephemeral=True)

# Error handlers (I never have error cos i am pro sigma coder)
@setwelcome.error
@setgoodbye.error
@setticketpanel.error
@startgiveaway.error
@endgiveaway.error
async def on_command_error(interaction: discord.Interaction, error):
    err = getattr(error, "original", error)
    await interaction.response.send_message(f"Error: {str(err)}", ephemeral=True)


import os
token = "Put your bot token here"
bot.run(token)
print(f"Logged in as {bot.user} (ID: {bot.user})")

# Run the bot 🏃‍♂️🏃‍♂️🏃‍♂️🏃‍♂️🏃‍♂️
if __name__ == "__main__":
    ensure_db()
    bot.launch_time = datetime.now(timezone.utc)  # Track bot start time for uptime

    # Start the giveaway checker background task after the bot is ready
    @bot.event
    async def on_ready():
        print(f"Logged in as {bot.user} (ID: {bot.user.id})")
        try:
            synced = await tree.sync()
            print(f"✅ Synced {len(synced)} global command(s)")
        except Exception as e:
            print(f"❌ Failed to sync commands: {e}")

    # Start giveaway checker loop
    if not giveaway_checker.is_running():
        giveaway_checker.start()

# Please follow me on tiktok im poor and i spend alot of time doing this 🥺
# Tiktok: @snowycss
# Discord: lilbozo_900 (Snowy) | User ID: 1388625676973899776
# Add me on discord if you need smth i am on it alot 😭

# bye

