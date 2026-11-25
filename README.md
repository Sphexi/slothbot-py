# slothbot

A Discord bot that manages slowmode automatically.

slothbot counts messages per channel and raises or lowers Discord's native slowmode to hold each channel near a message rate you set. It is controlled entirely through slash commands and stores its state as JSON files in a data directory.

## Quickstart

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Set up a Discord application first (see [Discord setup](#discord-setup)).

```console
git clone https://github.com/Sphexi/slothbot-py.git
cd slothbot-py
uv sync
echo "SB_TOKEN=<your bot token>" > .env
uv run slothbot
```

`uv run python -m slothbot` starts the same entry point.

On first start the bot registers its slash commands globally, which can take a while to appear in Discord. For instant updates while developing, set `SB_GUILD` to a server ID to sync to that server only.

## Discord setup

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and create a **New Application**.
2. Under **Bot**, reset and copy the token. This is `SB_TOKEN`.
3. Leave the **Privileged Gateway Intents** (Presence, Server Members, Message Content) switched off. slothbot uses two gateway intents, `Guilds` and `Guild Messages`. Both are non-privileged, so the app needs no intent review from Discord, even in large servers. The bot counts messages but never reads their content.
4. Under **OAuth2 > URL Generator**, select the scopes `bot` and `applications.commands`, and the bot permissions **Manage Channels**, **View Channel** and **Read Message History**. Manage Channels is what lets the bot set slowmode. The bot cannot receive messages from a channel it cannot see, so without View Channel it silently counts nothing there.
5. Open the generated URL to invite the bot to a server.

## Commands

slothbot has no message prefix. Every command is a slash command and works in servers only.

Commands marked "Moderator" can be used by members with the Administrator permission or a role registered with `/moderator add`. Commands marked "Administrator" require the Administrator permission. Commands marked "Anyone" have no restriction.

| Command | Options | Access | Description |
|---|---|---|---|
| `/ratelimit` | `limit`, `channel` (optional) | Moderator | Set the target messages per minute for a channel or thread. `0` removes the limit; 1 to 5 are rejected (see below). `channel` defaults to the current channel. |
| `/clear` | `channel` (optional) | Moderator | Clear a channel's or thread's message counts. Does not remove a configured rate limit; use `/ratelimit 0` for that. |
| `/buckets` | `channel` (optional) | Moderator | Show a channel's or thread's limit and its recent message counts in 10-second buckets. |
| `/active` | none | Moderator | Show the five most active channels and threads. |
| `/count-bots` | `value` | Moderator | Choose whether messages from bots and webhooks are counted. |
| `/configuration` | none | Moderator | Show the server's moderator roles, ignored roles, rate limits and bot-counting setting. |
| `/about` | none | Anyone | Show source link and uptime. |
| `/ignore add` | `role` | Moderator | Stop counting messages from members with a role. |
| `/ignore remove` | `role` | Moderator | Count messages from a previously ignored role again. |
| `/ignore list` | none | Moderator | List ignored roles. |
| `/moderator add` | `role` | Administrator | Register a role as a moderator role. |
| `/moderator remove` | `role` | Administrator | Unregister a moderator role. |

### Minimum limit

The lowest limit is 6 messages per minute. Messages are counted in 10-second buckets, so a single message already reads as 6 per minute, and any lower limit would be exceeded permanently by one message.

### Threads

`/ratelimit`, `/clear` and `/buckets` accept a thread as `channel`, as well as a text or announcement channel. A thread is counted and limited on its own. Its messages do not count toward the parent channel, so a limit on `#general` does not throttle conversation that moves into a thread, and a thread gets its own slowmode. Set a limit on the thread to control it.

## Configuration

Settings are read from environment variables with the prefix `SB_`, and from a `.env` file in the working directory.

| Variable | Required | Default | Description |
|---|---|---|---|
| `SB_TOKEN` | Yes | none | Discord bot token. |
| `SB_DATA` | No | `data` | Directory holding `limits.json`, `moderator.json`, `counter.json`, `ignored_roles.json` and the heartbeat file. Set it for a local run to keep state somewhere other than `./data`; leave it unset under Docker. |
| `SB_LOG_LEVEL` | No | `info` | Root log level: `debug`, `info`, `warning` or `error`. |
| `SB_GUILD` | No | unset | Sync slash commands to this server ID only, instead of globally. |
| `SB_HEARTBEAT_FILE` | No | `<SB_DATA>/heartbeat` | Path of the liveness file read by the container healthcheck. |

## Running in Docker

`docker-compose.yml` runs the published image, `ghcr.io/sphexi/slothbot:latest`, which is built and pushed by the Docker workflow on every push to `main`. Each build is also tagged with the short commit SHA (`sha-<short>`).

```console
cp .env.example .env
# set SB_TOKEN in .env
docker compose up -d
docker compose logs -f
```

State lives in `/var/data` inside the container. Compose mounts the named volume `slothbot-data` there; keep a volume mounted at that path or counts, limits, moderator roles and ignored roles are lost when the container is replaced.

The image sets `SB_DATA=/var/data`. Leave `SB_DATA` unset in `.env` under Docker, because a value there overrides it and writes state outside the volume.

The compose file pins nothing by default. For a reproducible deployment, pin the image to a digest or to a `sha-<commit>` tag instead of `latest`.

The container runs with a read-only root filesystem, all Linux capabilities dropped and `no-new-privileges`. Logs rotate at 10 MB across 3 files. Only the `/var/data` volume is writable.

### Healthcheck

The image declares a healthcheck that runs every 30 seconds. While the bot's gateway connection is ready, it touches a heartbeat file (`<SB_DATA>/heartbeat`, or `SB_HEARTBEAT_FILE`) every 15 seconds. The check fails if the file is missing or older than 60 seconds.

If the bot loses its Discord connection but the process stays up, the file goes stale and `docker ps` shows the container as `unhealthy` after three consecutive failures. The 30-second start period gives the bot time to connect before failures count.

## Development

```console
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

CI runs `ruff check`, `ruff format --check` and `pytest` on every push and pull request to `main`. A `pip-audit` scan of the locked runtime dependencies runs alongside them and weekly on a schedule.

Dependencies are pinned to exact versions in `pyproject.toml`, with hashes in `uv.lock`. The Docker base images are pinned by digest and GitHub Actions by commit SHA.

## License

MIT. See [LICENSE](LICENSE).
