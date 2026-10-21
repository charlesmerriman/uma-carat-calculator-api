# The Discord bot worker

The always-on half of the site's Discord bot: it answers moderators' clicks on report
cards and the `/mod` commands. Django sends the cards and edits them; this process
listens (Discord only delivers clicks over a connection that stays open, which a web
server can't hold). Plan and decisions: `discord-bot-plan.md` in the private notes repo.

It never imports Django. It reaches the site over HTTPS through the bot API
(`../calculatorapi/views/bot_api.py`) with the shared secret `BOT_API_SECRET`.

## Run it locally

Use the **dev** bot and the practice server, never the community one. A second process
on the real bot's token would answer real moderators' clicks.

```bash
cd backend/bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # once
# Django on :8000 with the same BOT_API_SECRET in backend/.env
BOT_ENV_FILE=~/.config/uma-discord/bot.env .venv/bin/python -m bot
```

`bot.env` needs `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_MODERATOR_ROLE_ID`,
`DISCORD_MOD_QUEUE_CHANNEL_ID`, `API_BASE_URL` (`http://localhost:8000` locally) and
`BOT_API_SECRET`. A missing one stops the worker with a line naming it.

Run it from this directory: `python -m bot` from the api root finds the outer folder,
not the package.

## The team board (Step 6a)

`/request`, the message menus **Make this a request** and **Send to team board**, and
filling a missing priority and area on new `#calc-team-board` posts (`bot/team.py`). On
only while `DISCORD_TEAM_BOARD_ID`, `DISCORD_TEAM_ROLE_ID` and
`DISCORD_WEBSITE_FEEDBACK_ID` are all set. Tags are matched by NAME.

`actions/requests_sync.py` is not the worker: GitHub runs it (`.github/workflows/requests-sync.yml`,
also called from the web repo) when a PR into develop merges, to move the team-board posts
the PR's description links to "In progress". Standard library only; `--dry-run` writes nothing.

## The board (Step 6b)

`bot/board.py`: the ordered queue (stored on the site, `TeamQueueEntry`; membership follows
the Planned / In progress tags, repaired hourly), the pinned **Up next** message in
`#calc-ops`, `/queue show | move | top`, the Monday 09:00 UTC summary, and the status
mirror onto the public post a team copy came from. On only while the team settings AND
`DISCORD_OPS_CHANNEL_ID` are set. Pinning needs the bot's Pin Messages (or Manage
Messages) in `#calc-ops`; without it Up next is posted unpinned.

## Tests and lint

```bash
.venv/bin/python -m unittest discover -s tests -t .
.venv/bin/pip install pylint==3.3.9 && .venv/bin/pylint bot actions tests
```

Fakes stand in for Discord and the site (`tests/fakes.py`); the client's tests use a
throwaway server on 127.0.0.1. Nothing reaches the network.

## In production

A `workers:` component on the DigitalOcean app, `source_dir: /bot`,
`run_command: python -m bot`, `apps-s-1vcpu-0.5gb` ($5/month). It has no URL and takes
no requests; DigitalOcean restarts it if it exits.
