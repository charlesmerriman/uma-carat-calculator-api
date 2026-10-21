"""What moderators read: escaped names, cut text, and the site's copy rules."""

import pathlib
import unittest

from bot import format as fmt


class FormatTests(unittest.TestCase):

    def test_names_cannot_format_or_ping(self):
        text = fmt.person({"display_name": "**big** @everyone", "handle": "user_1"})
        self.assertNotIn("**big**", text)
        self.assertNotIn("@everyone", text)

    def test_reported_text_stays_in_its_block(self):
        block = fmt.quoted("```\n# heading\n```")
        self.assertTrue(block.startswith("```\n") and block.endswith("\n```"))
        self.assertEqual(block.count("```"), 2)

    def test_long_text_is_cut(self):
        self.assertLessEqual(len(fmt.quoted("x" * 5000)), fmt.TEXT_LIMIT + 10)

    def test_moderation_and_errors_stay_out_of_the_voice(self):
        from bot import persona  # pylint: disable=import-outside-toplevel
        voiced = {value for name, value in vars(persona).items() if name.isupper() and isinstance(value, str)}
        voiced |= set(persona.FLOURISHES.values())
        for plain in (fmt.NEED_ROLE, fmt.SITE_DOWN, fmt.GONE, fmt.CONFIRM_BLOCK, *fmt.DONE.values()):
            self.assertNotIn(plain, voiced)

    def test_no_em_or_en_dashes_anywhere_the_bot_speaks(self):
        root = pathlib.Path(fmt.__file__).parent
        for path in root.glob("*.py"):
            with self.subTest(path=path.name):
                self.assertNotIn("—", path.read_text(encoding="utf-8"))
                self.assertNotIn("–", path.read_text(encoding="utf-8"))

    def test_an_item_embed(self):
        embed = fmt.item_embed({
            "kind": "comment", "text": "rude", "status": "Visible", "author": None,
            "open_reports": [
                {"reason": "Harassment", "note": "see this", "reporter": {"display_name": "", "handle": "u2"}},
            ],
        })
        self.assertEqual(embed.title, "Comment reported")
        self.assertEqual(embed.fields[2].name, "1 open report")
        self.assertIn("no display name (u2)", embed.fields[2].value)
