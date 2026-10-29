"""
The worker's client for the site's bot API (the api repo's views/bot_api.py).

The worker never imports Django and never touches the database: it asks the
site over HTTPS like any other client, at the public hostname, with the shared
secret in X-Bot-Key. That keeps the site's one-process cache rule intact and
means a bot crash can't take the site down with it.

aiohttp, not requests: discord.py runs everything on one asyncio event loop,
and a blocking requests call inside a handler would freeze every other event
(Discord's heartbeat included) until it returned.

WHAT A CALLER GETS BACK
-----------------------
An Answer (status code and JSON body) for anything the site meant to say:
200, 201, 400, 404, 409. SiteUnavailable for everything else (no connection,
a timeout, a 5xx, or a 403, which means our key and the site's disagree), so
a handler has exactly one "try again later" case.
"""

import asyncio
import logging
from dataclasses import dataclass

import aiohttp

logger = logging.getLogger(__name__)

HEADER = "X-Bot-Key"
# Long enough for a slow site on a banner launch day; every caller has already
# acknowledged Discord (which allows 15 minutes for the follow-up) before it
# calls here.
TIMEOUT_SECONDS = 10
# GET bot/digest is the exception: once a day, with nobody waiting on it, and
# on a cold cache it rebuilds the whole analytics report, which took longer
# than 10 s on 2026-10-22 and lost that day's digest. Just under gunicorn's
# own 30 s (gunicorn.conf.py), past which waiting longer can't help.
DIGEST_TIMEOUT_SECONDS = 28
USER_AGENT = "UmaCaratCalculatorBot (https://umacaratcalculator.com, 1.0)"


class SiteUnavailable(Exception):
    """The site didn't give an answer we can use. Already logged."""


@dataclass(frozen=True)
class Answer:
    status: int
    data: dict

    @property
    def ok(self):
        return 200 <= self.status < 300


class SiteApi:

    def __init__(self, base_url, secret):
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._session = None

    async def start(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
            headers={HEADER: self._secret, "User-Agent": USER_AGENT},
        )

    async def close(self):
        if self._session is not None:
            await self._session.close()

    async def _request(self, method, path, body=None, timeout=None):
        url = f"{self._base_url}/{path}"
        # None keeps the session's TIMEOUT_SECONDS.
        extra = {"timeout": aiohttp.ClientTimeout(total=timeout)} if timeout else {}
        try:
            async with self._session.request(method, url, json=body, **extra) as response:
                try:
                    data = await response.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    data = {}
                status = response.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Site %s %s failed: %r", method, path, exc)
            raise SiteUnavailable from exc
        if status == 403:
            # Never a moderator's fault: the worker's BOT_API_SECRET doesn't
            # match the site's. Loud, because nothing works until it's fixed.
            logger.error("Site refused the bot key on %s %s: check BOT_API_SECRET on both sides", method, path)
            raise SiteUnavailable
        if status >= 500 or status in (401, 429):
            logger.warning("Site %s %s answered %s", method, path, status)
            raise SiteUnavailable
        return Answer(status, data if isinstance(data, dict) else {})

    async def reports(self):
        return await self._request("GET", "bot/reports")

    async def item(self, kind, ref):
        return await self._request("GET", f"bot/items/{kind}/{ref}")

    async def moderate(self, kind, ref, action, moderator):
        return await self._request(
            "POST", f"bot/items/{kind}/{ref}/moderate", {"action": action, "moderator": moderator}
        )

    async def undo(self, kind, ref, moderator):
        return await self._request("POST", f"bot/items/{kind}/{ref}/undo", {"moderator": moderator})

    async def unblock(self, kind, ref, moderator):
        return await self._request("POST", f"bot/items/{kind}/{ref}/unblock", {"moderator": moderator})

    # The worker's own never-twice records (views/bot_api.py, "promote:<thread>").

    async def claim(self, key, channel_id):
        return await self._request("POST", "bot/messages", {"key": key, "channel_id": str(channel_id)})

    async def record(self, key, message_id):
        return await self._request("PATCH", f"bot/messages/{key}", {"message_id": str(message_id)})

    async def release(self, key):
        return await self._request("DELETE", f"bot/messages/{key}")

    async def find_record(self, message_id):
        """The worker record whose message is `message_id` (a team copy's id)."""
        return await self._request("GET", f"bot/messages?message_id={message_id}")

    async def upcoming(self, days=2):
        """Banner and event moments due soon, from the Timeline's own dates."""
        return await self._request("GET", f"bot/upcoming?days={days}")

    async def digest(self):
        """Yesterday's numbers for the daily digest (aggregates only)."""
        return await self._request("GET", "bot/digest", timeout=DIGEST_TIMEOUT_SECONDS)

    async def health(self, minutes=5):
        """What the API was busy with over the last `minutes`, for the health
        alert: the busiest routes by total time (patterns, never paths), how
        many requests are in progress, and whether the guest payload is cached.
        On the usual timeout: the alert asks once and doesn't wait longer for
        an API that is the thing being slow."""
        return await self._request("GET", f"bot/health?minutes={minutes}")

    async def database(self):
        """The database's numbers for the Monday post: size, running totals,
        and the queries that took the most time (aggregates and query shapes)."""
        return await self._request("GET", "bot/database")

    # The team board's ordered queue (the site's team_queue.py).

    async def queue(self):
        return await self._request("GET", "bot/queue")

    async def queue_add(self, thread_id):
        return await self._request("POST", "bot/queue", {"thread_id": str(thread_id)})

    async def queue_remove(self, thread_id):
        return await self._request("DELETE", f"bot/queue/{thread_id}")

    async def queue_move(self, thread_id, position):
        return await self._request("POST", f"bot/queue/{thread_id}/move", {"position": position})

    async def queue_sync(self, thread_ids):
        return await self._request("POST", "bot/queue/sync", {"thread_ids": [str(tid) for tid in thread_ids]})

    async def add_term(self, term, match, moderator):
        return await self._request(
            "POST", "bot/blocked-terms", {"term": term, "match": match, "moderator": moderator}
        )
