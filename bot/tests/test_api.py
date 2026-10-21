"""The site client against a throwaway aiohttp server on 127.0.0.1: what it
sends, and which answers become SiteUnavailable."""

import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.api import SiteApi, SiteUnavailable


class SiteApiTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.seen = []
        self.status = 200
        self.body = {"items": []}

        async def handler(request):
            self.seen.append({
                "method": request.method,
                "path": request.path,
                "key": request.headers.get("X-Bot-Key"),
                "json": await request.json() if request.can_read_body else None,
            })
            return web.json_response(self.body, status=self.status)

        app = web.Application()
        app.router.add_route("*", "/api/{tail:.*}", handler)
        self.server = TestServer(app)
        await self.server.start_server()
        self.api = SiteApi(str(self.server.make_url("/api/")), "the-key")
        await self.api.start()

    async def asyncTearDown(self):
        await self.api.close()
        await self.server.close()

    async def test_sends_the_key_and_the_body(self):
        answer = await self.api.moderate("post", "aB3dE5fG", "keep", "oguri_fan (1234567)")
        self.assertTrue(answer.ok)
        self.assertEqual(len(self.seen), 1)
        seen = self.seen[0]
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["path"], "/api/bot/items/post/aB3dE5fG/moderate")
        self.assertEqual(seen["key"], "the-key")
        self.assertEqual(seen["json"], {"action": "keep", "moderator": "oguri_fan (1234567)"})

    async def test_meant_answers_come_back(self):
        for status in (400, 404, 409):
            with self.subTest(status=status):
                self.status, self.body = status, {"detail": "x"}
                answer = await self.api.item("post", "aB3dE5fG")
                self.assertEqual((answer.status, answer.data), (status, {"detail": "x"}))
                self.assertFalse(answer.ok)

    async def test_failures_are_unavailable(self):
        for status in (403, 429, 500, 503):
            with self.subTest(status=status):
                self.status = status
                with self.assertRaises(SiteUnavailable):
                    await self.api.reports()

    async def test_no_connection_is_unavailable(self):
        await self.server.close()
        with self.assertRaises(SiteUnavailable):
            await self.api.reports()
