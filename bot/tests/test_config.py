"""Settings: every missing or malformed one named at start-up, never a value."""

import os
import tempfile
import unittest

from bot.config import ConfigError, load

GOOD = {
    "DISCORD_BOT_TOKEN": "secret-token",
    "DISCORD_GUILD_ID": "1410431883912941619",
    "DISCORD_MODERATOR_ROLE_ID": "1413131483102707853",
    "DISCORD_MOD_QUEUE_CHANNEL_ID": "1562201700507586681",
    "API_BASE_URL": "https://umacaratcalculator.com/api/",
    "BOT_API_SECRET": "secret-key",
}


class ConfigTests(unittest.TestCase):

    def test_a_full_environment(self):
        config = load(GOOD)
        self.assertEqual(config.guild_id, 1410431883912941619)
        self.assertEqual(config.api_base_url, "https://umacaratcalculator.com/api")

    def test_secrets_stay_out_of_repr(self):
        text = repr(load(GOOD))
        self.assertNotIn("secret-token", text)
        self.assertNotIn("secret-key", text)

    def test_missing_settings_are_named(self):
        with self.assertRaises(ConfigError) as caught:
            load({**GOOD, "BOT_API_SECRET": "", "DISCORD_GUILD_ID": ""})
        self.assertIn("BOT_API_SECRET", str(caught.exception))
        self.assertIn("DISCORD_GUILD_ID", str(caught.exception))

    def test_an_id_must_be_digits_and_its_value_is_not_echoed(self):
        with self.assertRaises(ConfigError) as caught:
            load({**GOOD, "DISCORD_MODERATOR_ROLE_ID": "@Moderator"})
        self.assertIn("DISCORD_MODERATOR_ROLE_ID", str(caught.exception))
        self.assertNotIn("@Moderator", str(caught.exception))

    def test_the_env_file_fills_in_and_the_environment_wins(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
            handle.write("# a comment\n\n")
            for key, value in GOOD.items():
                handle.write(f'{key}="{value}"\n')
        try:
            config = load({"BOT_ENV_FILE": handle.name, "DISCORD_GUILD_ID": "42"})
        finally:
            os.unlink(handle.name)
        self.assertEqual(config.token, "secret-token")
        self.assertEqual(config.guild_id, 42)

    def test_an_unreadable_env_file(self):
        with self.assertRaises(ConfigError):
            load({"BOT_ENV_FILE": "/nonexistent/bot.env"})
