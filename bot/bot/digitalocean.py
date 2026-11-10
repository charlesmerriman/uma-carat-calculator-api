"""
Read-only DigitalOcean calls for the health alerts and the deploy news.

The token must be a READ-ONLY one made for the bot (custom scopes: app read,
monitoring read), never the admin's rebuild token, which can change the app.
Every call here is a GET. A failure returns None and is logged: an alert
without resource numbers is still an alert.
"""

import asyncio
import logging
import time

import aiohttp

logger = logging.getLogger(__name__)

API = "https://api.digitalocean.com/v2"
TIMEOUT_SECONDS = 10
# The API component, whose CPU and memory the alerts quote.
API_COMPONENT = "calculatorproject"
METRICS = ("cpu_percentage", "memory_percentage", "restart_count")


class DigitalOcean:

    def __init__(self, token, app_id):
        self._token = token
        self.app_id = app_id
        self._session = None

    async def start(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
            headers={"Authorization": f"Bearer {self._token}"},
        )

    async def close(self):
        if self._session is not None:
            await self._session.close()

    async def _get(self, path, params=None):
        try:
            async with self._session.get(f"{API}{path}", params=params) as response:
                if response.status != 200:
                    logger.warning("DigitalOcean GET %s answered %s", path, response.status)
                    return None
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("DigitalOcean GET %s failed: %r", path, exc)
            return None

    async def deployments(self, count=5):
        """The app's latest deployments, newest first, or None."""
        body = await self._get(f"/apps/{self.app_id}/deployments", {"per_page": count})
        return body.get("deployments", []) if body else None

    async def peaks(self, minutes=5):
        """The API component's highest CPU % and memory % and its restart count
        over the last `minutes`, as {metric: number}. Missing ones are left out."""
        found = {}
        for metric in METRICS:
            points = await self.series(metric, minutes)
            if points:
                found[metric] = max(value for _, value in points)
        return found

    async def series(self, metric, minutes):
        """One of METRICS for the API component over the last `minutes`, as
        [(unix seconds, value)] oldest first (DigitalOcean samples about every
        2 minutes). With more than one instance, each moment keeps the highest
        value, the one that would trip an alert. None when the call fails, so
        a caller can tell "no answer" from "no samples" ([])."""
        end = int(time.time())
        params = {"app_id": self.app_id, "app_component": API_COMPONENT, "start": end - minutes * 60, "end": end}
        body = await self._get(f"/monitoring/metrics/apps/{metric}", params)
        if body is None:
            return None
        merged = {}
        for series in (body.get("data") or {}).get("result") or []:
            for stamp, value in series.get("values") or []:
                merged[int(stamp)] = max(merged.get(int(stamp), float("-inf")), float(value))
        return sorted(merged.items())
