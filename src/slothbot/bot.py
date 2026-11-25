"""Discord client wiring: intents, event handling, background loops and command sync."""

import logging
import time

import discord
from discord import app_commands
from discord.ext import tasks

from slothbot import commands
from slothbot.config import Settings
from slothbot.counter import BUCKET_SECONDS, ChannelKey, MessageCounter
from slothbot.limiter import ChannelGone, ChannelUnavailable, Limiter, SlowmodeForbidden
from slothbot.services import Services
from slothbot.storage import RoleSet

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15
CYCLE_STALE_BUCKETS = 3


def build_intents() -> discord.Intents:
    """Intents the bot needs; neither is privileged, so no portal approval is required."""
    intents = discord.Intents.none()
    intents.guilds = True
    intents.guild_messages = True
    return intents


class SlothBot(discord.Client):
    """Gateway client that counts messages and adjusts slowmode."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(
            intents=build_intents(),
            max_messages=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.settings = settings
        self._last_cycle: float | None = None
        self.tree = app_commands.CommandTree(self)

        moderator = RoleSet(settings.data / "moderator.json")
        ignored_roles = RoleSet(settings.data / "ignored_roles.json")
        counter = MessageCounter(settings.data / "counter.json", ignored_roles)
        limiter = Limiter(counter, self._set_slowmode, settings.data / "limits.json")
        self.services = Services(counter, limiter, moderator, ignored_roles)
        commands.register(self.tree)

    async def setup_hook(self) -> None:
        if self.settings.guild is not None:
            guild = discord.Object(id=self.settings.guild)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self._cycle.start()
        self._heartbeat.start()

    async def on_message(self, message: discord.Message) -> None:
        author = message.author
        role_ids = {role.id for role in author.roles} if isinstance(author, discord.Member) else ()
        self.services.counter.record(
            message.guild.id if message.guild else None,
            message.channel.id,
            message.webhook_id is not None or author.bot,
            role_ids,
        )

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self.services.limiter.drop_guild(guild.id)
        self.services.counter.clear_guild(guild.id)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        self._forget_channel(ChannelKey(channel.guild.id, channel.id))

    async def on_thread_delete(self, thread: discord.Thread) -> None:
        self._forget_channel(ChannelKey(thread.guild.id, thread.id))

    def _forget_channel(self, key: ChannelKey) -> None:
        self.services.limiter.drop_channel(key)
        self.services.counter.clear(key)

    async def _set_slowmode(self, key: ChannelKey, seconds: int) -> None:
        guild = self.get_guild(key.guild_id)
        # An unavailable guild resolves no channels, so a missing channel only means deletion
        # once the guild itself is in cache and marked available.
        if guild is None or guild.unavailable:
            raise ChannelUnavailable(f"guild {key.guild_id} is unavailable")
        channel = guild.get_channel_or_thread(key.channel_id)
        if channel is None:
            raise ChannelGone(f"channel {key.channel_id} no longer exists")
        if getattr(channel, "slowmode_delay", None) == seconds:
            return
        try:
            await channel.edit(slowmode_delay=seconds)  # type: ignore[union-attr]
        except discord.NotFound as exc:
            raise ChannelGone(f"channel {key.channel_id} no longer exists") from exc
        except discord.Forbidden as exc:
            raise SlowmodeForbidden(f"no permission to edit channel {key.channel_id}") from exc

    @tasks.loop(seconds=BUCKET_SECONDS)
    async def _cycle(self) -> None:
        self.services.counter.rotate()
        await self.services.limiter.tick()
        self._last_cycle = time.monotonic()

    def _cycle_is_fresh(self) -> bool:
        if self._last_cycle is None:
            return False
        return time.monotonic() - self._last_cycle <= CYCLE_STALE_BUCKETS * BUCKET_SECONDS

    @_cycle.before_loop
    async def _before_cycle(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(seconds=HEARTBEAT_SECONDS)
    async def _heartbeat(self) -> None:
        if not self.is_ready() or self.is_closed() or not self._cycle_is_fresh():
            return
        path = self.settings.heartbeat_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        except OSError:
            log.warning("Could not touch heartbeat file %s", path, exc_info=True)

    @_heartbeat.before_loop
    async def _before_heartbeat(self) -> None:
        await self.wait_until_ready()
