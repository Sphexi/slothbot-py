"""Slash command definitions."""

import logging
import re
import time
from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import Protocol, cast

import discord
from discord import app_commands

from slothbot.counter import ChannelKey
from slothbot.durations import humanize_seconds
from slothbot.limiter import MIN_LIMIT_PER_MINUTE
from slothbot.permissions import is_authorised
from slothbot.rate import Rate
from slothbot.services import Services
from slothbot.storage import RoleSet

log = logging.getLogger(__name__)

CHECK = "✓"
SOURCE_URL = "https://github.com/Sphexi/slothbot-py"
STACK_URL = "https://github.com/Rapptz/discord.py"
ACTIVE_LIMIT = 5

SlowmodeChannel = discord.TextChannel | discord.Thread
BUCKETS_PER_ROW = 6
AUTOCOMPLETE_LIMIT = 25
EMBED_FIELD_LIMIT = 1024
MESSAGE_LIMIT = 2000

_ROLE_REFERENCE = re.compile(r"<@&(\d+)>|(\d+)")
_OMITTED = "…and {count} more"


class UserError(app_commands.AppCommandError):
    """A failure whose message is shown to the invoker as an ephemeral reply."""


class _HasServices(Protocol):
    services: Services


def _services(interaction: discord.Interaction) -> Services:
    return cast(_HasServices, interaction.client).services


def _guild_id(interaction: discord.Interaction) -> int:
    if interaction.guild_id is None:
        raise UserError("This command can only be used in a server.")
    return interaction.guild_id


def _channel_id(interaction: discord.Interaction, channel: SlowmodeChannel | None) -> int:
    guild_id = _guild_id(interaction)
    if channel is not None:
        if channel.guild.id != guild_id:
            raise UserError("That channel is not in this server.")
        return channel.id
    if interaction.channel_id is None:
        raise UserError("Could not determine the channel.")
    return interaction.channel_id


def _member_role_ids(interaction: discord.Interaction) -> set[int]:
    user = interaction.user
    return {role.id for role in user.roles} if isinstance(user, discord.Member) else set()


async def _reply(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    ephemeral: bool = False,
) -> None:
    kwargs = {
        "content": content,
        "embed": embed if embed is not None else discord.utils.MISSING,
        "ephemeral": ephemeral,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


def _role_mention(role_id: int) -> str:
    return f"<@&{role_id}>"


def _channel_mention(channel_id: int) -> str:
    return f"<#{channel_id}>"


def _fit_lines(lines: Sequence[str], limit: int) -> str:
    """Join lines, dropping the tail with a count of omissions if the text would pass limit."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    reserve = len(_OMITTED.format(count=len(lines))) + 1
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = len(line) + (1 if kept else 0)
        if used + cost > limit - reserve:
            break
        kept.append(line)
        used += cost
    return "\n".join([*kept, _OMITTED.format(count=len(lines) - len(kept))])


def _role_lines(role_ids: Collection[int], limit: int) -> str:
    lines = [f"{_role_mention(role_id)} ({role_id})" for role_id in sorted(role_ids)]
    return _fit_lines(lines, limit) or "None"


def parse_role_id(text: str) -> int:
    """Extract a role id from a bare id or a role mention."""
    match = _ROLE_REFERENCE.fullmatch(text.strip())
    if match is None:
        raise UserError("Provide a role from the list, a role mention or a role id.")
    return int(match.group(1) or match.group(2))


def format_buckets(limit: Rate | None, buckets: list[Rate]) -> str:
    """Code block showing the limit line and a grid of bucket counts, one row per minute."""
    if limit is None:
        first = "Channel is not rate limited\n"
    else:
        first = f"Limit: {limit.humanize()} ({limit.per_second * 10:.1f} per 10s)\n"
    lines = [first, "Buckets:   0s 10s 20s 30s 40s 50s"]
    for row, start in enumerate(range(0, len(buckets), BUCKETS_PER_ROW)):
        counts = " ".join(f"{b.count:3d}" for b in buckets[start : start + BUCKETS_PER_ROW])
        lines.append(f"{row:7d}m {counts}")
    return "```\n" + "\n".join(lines) + "```"


async def require_moderator(interaction: discord.Interaction) -> bool:
    guild_id = _guild_id(interaction)
    allowed = is_authorised(
        is_administrator=interaction.permissions.administrator,
        member_role_ids=_member_role_ids(interaction),
        moderator_role_ids=_services(interaction).moderator.get(guild_id),
    )
    if not allowed:
        raise UserError("You need the Administrator permission or a moderator role to do that.")
    return True


async def require_administrator(interaction: discord.Interaction) -> bool:
    if not interaction.permissions.administrator:
        raise UserError("Only administrators can do that.")
    return True


async def _stored_role_choices(
    interaction: discord.Interaction,
    current: str,
    roles: RoleSet,
    check: Callable[[discord.Interaction], Awaitable[bool]],
) -> list[app_commands.Choice[str]]:
    try:
        await check(interaction)
    except UserError:
        return []
    guild = interaction.guild
    needle = current.strip().lower()
    choices: list[app_commands.Choice[str]] = []
    for role_id in sorted(roles.get(_guild_id(interaction))):
        role = guild.get_role(role_id) if guild else None
        label = f"@{role.name}" if role else f"deleted role {role_id}"
        if needle in label.lower() or needle in str(role_id):
            choices.append(app_commands.Choice(name=label[:100], value=str(role_id)))
    return choices[:AUTOCOMPLETE_LIMIT]


@app_commands.command(name="ratelimit", description="Limit messages per minute in a channel")
@app_commands.describe(
    limit="Messages per minute; 0 removes the limit",
    channel="Channel to limit; defaults to this channel",
)
@app_commands.guild_only()
@app_commands.check(require_moderator)
async def ratelimit(
    interaction: discord.Interaction,
    limit: app_commands.Range[int, 0],
    channel: SlowmodeChannel | None = None,
) -> None:
    key = ChannelKey(_guild_id(interaction), _channel_id(interaction, channel))
    mention = _channel_mention(key.channel_id)
    limiter = _services(interaction).limiter
    if limit == 0:
        await interaction.response.defer()
        if await limiter.clear(key):
            await _reply(interaction, f"{CHECK} Rate limit for {mention} cleared")
        else:
            await _reply(
                interaction,
                f"Rate limit for {mention} removed, but its slowmode could not be reset;"
                " check the bot's Manage Channel permission there",
            )
        return
    if limit < MIN_LIMIT_PER_MINUTE:
        raise UserError(
            f"The lowest supported limit is {MIN_LIMIT_PER_MINUTE} messages per minute."
        )
    rate = Rate.per_minute(limit)
    await limiter.set(key, rate)
    await _reply(interaction, f"{CHECK} Rate limit of {rate.humanize()} set for {mention}")


@app_commands.command(name="clear", description="Clear a channel's message counts")
@app_commands.describe(channel="Channel to clear; defaults to this channel")
@app_commands.guild_only()
@app_commands.check(require_moderator)
async def clear_counts(
    interaction: discord.Interaction, channel: SlowmodeChannel | None = None
) -> None:
    key = ChannelKey(_guild_id(interaction), _channel_id(interaction, channel))
    _services(interaction).counter.clear(key)
    await _reply(
        interaction,
        f"{CHECK} Message counts cleared for {_channel_mention(key.channel_id)};"
        " its slowmode will fall away on the next update",
    )


@app_commands.command(name="buckets", description="Show a channel's recent message counts")
@app_commands.describe(channel="Channel to inspect; defaults to this channel")
@app_commands.guild_only()
@app_commands.check(require_moderator)
async def buckets(interaction: discord.Interaction, channel: SlowmodeChannel | None = None) -> None:
    key = ChannelKey(_guild_id(interaction), _channel_id(interaction, channel))
    services = _services(interaction)
    output = format_buckets(services.limiter.get(key), services.counter.buckets(key))
    await _reply(interaction, output)


@app_commands.command(name="active", description="Show the most active channels")
@app_commands.guild_only()
@app_commands.check(require_moderator)
async def active(interaction: discord.Interaction) -> None:
    counts = _services(interaction).counter.total_counts(_guild_id(interaction))
    if not counts:
        await _reply(interaction, "There are currently no active channels")
        return
    top = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:ACTIVE_LIMIT]
    await _reply(interaction, "\n".join(f"{_channel_mention(c)} ({n})" for c, n in top))


@app_commands.command(name="count-bots", description="Choose whether bot messages are counted")
@app_commands.describe(value="True to count messages from bots and webhooks")
@app_commands.guild_only()
@app_commands.check(require_moderator)
async def count_bots(interaction: discord.Interaction, value: bool) -> None:
    _services(interaction).counter.set_counting_bots(_guild_id(interaction), value)
    await _reply(
        interaction,
        "Bot messages now are counted" if value else "Bot messages aren't now counted",
    )


@app_commands.command(name="configuration", description="Show this server's configuration")
@app_commands.guild_only()
@app_commands.check(require_moderator)
async def configuration(interaction: discord.Interaction) -> None:
    guild_id = _guild_id(interaction)
    services = _services(interaction)
    limits = services.limiter.for_guild(guild_id)
    limit_lines = _fit_lines(
        [f"{_channel_mention(c)} ({c}): {rate.humanize()}" for c, rate in sorted(limits.items())],
        EMBED_FIELD_LIMIT,
    )
    embed = discord.Embed(title="Configuration")
    _set_avatar(embed, interaction)
    embed.add_field(
        name="Moderation Roles",
        value=_role_lines(services.moderator.get(guild_id), EMBED_FIELD_LIMIT),
        inline=False,
    )
    embed.add_field(
        name="Ignored Roles",
        value=_role_lines(services.ignored_roles.get(guild_id), EMBED_FIELD_LIMIT),
        inline=False,
    )
    embed.add_field(name="Rate Limits", value=limit_lines or "None", inline=False)
    embed.add_field(
        name="Count bot messages",
        value="Yes" if services.counter.is_counting_bots(guild_id) else "No",
        inline=False,
    )
    await _reply(interaction, embed=embed)


def _set_avatar(embed: discord.Embed, interaction: discord.Interaction) -> None:
    user = interaction.client.user
    if user is not None:
        embed.set_thumbnail(url=user.display_avatar.url)


@app_commands.command(name="about", description="Show information about slothbot")
@app_commands.guild_only()
async def about(interaction: discord.Interaction) -> None:
    uptime = int(time.monotonic() - _services(interaction).started_at)
    embed = discord.Embed(
        title="slothbot", description="A Discord bot to auto manage slow mode on channels"
    )
    _set_avatar(embed, interaction)
    embed.add_field(name="Source", value=f"[GitHub]({SOURCE_URL})")
    embed.add_field(name="Uptime", value=humanize_seconds(uptime))
    embed.add_field(name="Stack", value=f"[discord.py]({STACK_URL})")
    await _reply(interaction, embed=embed)


ignore = app_commands.Group(
    name="ignore", description="Manage roles whose messages are not counted", guild_only=True
)


@ignore.command(name="add", description="Stop counting messages from members with a role")
@app_commands.describe(role="Role to ignore")
@app_commands.check(require_moderator)
async def ignore_add(interaction: discord.Interaction, role: discord.Role) -> None:
    changed = _services(interaction).ignored_roles.add(_guild_id(interaction), role.id)
    if changed:
        message = f"{CHECK} Messages from {_role_mention(role.id)} will no longer be counted"
    else:
        message = f"Messages from {_role_mention(role.id)} are already not counted"
    await _reply(interaction, message)


@ignore.command(name="remove", description="Count messages from members with a role again")
@app_commands.describe(role="Ignored role to stop ignoring")
@app_commands.check(require_moderator)
async def ignore_remove(interaction: discord.Interaction, role: str) -> None:
    role_id = parse_role_id(role)
    if not _services(interaction).ignored_roles.remove(_guild_id(interaction), role_id):
        raise UserError(f"{_role_mention(role_id)} is not an ignored role.")
    await _reply(interaction, f"Messages from {_role_mention(role_id)} will be counted again")


@ignore_remove.autocomplete("role")
async def _ignore_role_choices(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await _stored_role_choices(
        interaction, current, _services(interaction).ignored_roles, require_moderator
    )


@ignore.command(name="list", description="List ignored roles")
@app_commands.check(require_moderator)
async def ignore_list(interaction: discord.Interaction) -> None:
    roles = _services(interaction).ignored_roles.get(_guild_id(interaction))
    await _reply(
        interaction, _role_lines(roles, MESSAGE_LIMIT) if roles else "No roles are ignored"
    )


moderator = app_commands.Group(
    name="moderator", description="Manage moderator roles", guild_only=True
)


@moderator.command(name="add", description="Register a role as a moderator role")
@app_commands.describe(role="Role to register")
@app_commands.check(require_administrator)
async def moderator_add(interaction: discord.Interaction, role: discord.Role) -> None:
    _services(interaction).moderator.add(_guild_id(interaction), role.id)
    await _reply(
        interaction, f"{CHECK} Successfully added {_role_mention(role.id)} as moderator role"
    )


@moderator.command(name="remove", description="Unregister a moderator role")
@app_commands.describe(role="Moderator role to remove")
@app_commands.check(require_administrator)
async def moderator_remove(interaction: discord.Interaction, role: str) -> None:
    role_id = parse_role_id(role)
    if not _services(interaction).moderator.remove(_guild_id(interaction), role_id):
        raise UserError(f"{_role_mention(role_id)} is not a moderator role.")
    await _reply(interaction, f"Successfully removed {_role_mention(role_id)} from moderator roles")


@moderator_remove.autocomplete("role")
async def _moderator_role_choices(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    return await _stored_role_choices(
        interaction, current, _services(interaction).moderator, require_administrator
    )


async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    """Report command failures to the invoker without leaking internals."""
    if isinstance(error, UserError):
        message = str(error)
    elif isinstance(error, app_commands.NoPrivateMessage):
        message = "This command can only be used in a server."
    else:
        cause = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        log.error("Command failed", exc_info=cause)
        message = (
            "That change could not be saved, so it was not applied."
            if isinstance(cause, OSError)
            else "Something went wrong running that command."
        )
    try:
        await _reply(interaction, message, ephemeral=True)
    except discord.HTTPException:
        log.warning("Could not report command failure to the invoker", exc_info=True)


def register(tree: app_commands.CommandTree) -> None:
    """Add every command to the tree and install the error handler."""
    for command in (
        ratelimit,
        clear_counts,
        buckets,
        active,
        count_bots,
        configuration,
        about,
        ignore,
        moderator,
    ):
        tree.add_command(command)
    tree.error(on_error)
