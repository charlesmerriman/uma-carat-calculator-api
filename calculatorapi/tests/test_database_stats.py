"""The database's own numbers for Oguri Bot: calculatorapi/database_stats.py and
GET /bot/database.

The tests run on SQLite, which has none of Postgres's statistics views, so they
hold the SQLite answer (None everywhere), the pure helpers, and the routes. The
SQL itself was run read-only against production's Postgres 17 on 2026-10-29.
"""

from django.test import override_settings

from calculatorapi import database_stats
from .base import CalculatorTestCase

BOT_KEY = "bot-test-key"


class DatabaseStatsTests(CalculatorTestCase):

    def test_every_read_is_none_off_postgres(self):
        self.assertIsNone(database_stats.activity())
        self.assertIsNone(database_stats.overview())
        self.assertIsNone(database_stats.top_queries())

    def test_the_cache_hit_rate(self):
        self.assertEqual(database_stats.cache_hit_pct(1_998_766_949, 1_783_655), 99.91)
        self.assertIsNone(database_stats.cache_hit_pct(0, 0))

    def test_a_query_is_flattened_and_cut(self):
        self.assertEqual(
            database_stats.query_text('SELECT "id"\n    FROM   "calculatorapi_plan"\nWHERE "id" = $1'),
            'SELECT "id" FROM "calculatorapi_plan" WHERE "id" = $1',
        )
        long = database_stats.query_text("SELECT " + ", ".join(f"col{n}" for n in range(100)))
        self.assertEqual(len(long), database_stats.QUERY_TEXT_LIMIT)
        self.assertTrue(long.endswith("…"))
        self.assertEqual(database_stats.query_text(None), "")


@override_settings(BOT_API_SECRET=BOT_KEY)
class BotDatabaseTests(CalculatorTestCase):

    def get(self, route, key=BOT_KEY):
        headers = {"HTTP_X_BOT_KEY": key} if key is not None else {}
        return self.client.get(route, **headers)

    def test_locked_like_every_bot_route(self):
        self.assertEqual(self.get("/bot/database", key=None).status_code, 403)
        self.assertEqual(self.get("/bot/database", key="wrong").status_code, 403)
        with self.settings(BOT_API_SECRET=""):
            self.assertEqual(self.get("/bot/database").status_code, 404)

    def test_the_weekly_numbers_answer_even_without_them(self):
        response = self.get("/bot/database")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"overview": None, "top_queries": None})

    def test_the_health_snapshot_carries_the_database(self):
        body = self.get("/bot/health").json()
        self.assertIn("database", body)
        self.assertIsNone(body["database"])
