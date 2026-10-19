"""The link-preview drawing kit (share_card.py): fonts per character, and text
that fits its room."""

from calculatorapi import share_card
from calculatorapi.tests.base import CalculatorTestCase


class ShareCardTextTests(CalculatorTestCase):

    def test_characters_outfit_lacks_go_to_the_fallback(self):
        runs = share_card._runs("3rd Anniversary ★3 Select")  # pylint: disable=protected-access
        self.assertEqual(
            runs, [("3rd Anniversary ", False), ("★", True), ("3 Select", False)]
        )
        self.assertEqual(share_card._runs("Daiwa Scarlet ♡")[-1], ("♡", True))  # pylint: disable=protected-access

    def test_both_font_files_load(self):
        """A missing or unreadable file would fail every picture; this fails first."""
        self.assertGreater(share_card.text_width("Uma ★ ♡", 40), 0)

    def test_fit_cuts_at_a_word_with_an_ellipsis(self):
        text = "Champions Meeting 21 Capricorn Cup"
        width = share_card.text_width("Champions Meeting 21", 30)
        fitted = share_card.fit(text, 30, width + 30)
        self.assertEqual(fitted, "Champions Meeting 21…")
        self.assertEqual(share_card.fit(text, 30, 5000), text)

    def test_wrap_keeps_to_its_lines(self):
        text = " ".join(["Narita Brian (Blaze)"] * 12)
        lines = share_card.wrap(text, 34, 600, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("…"))
        for line in lines:
            self.assertLessEqual(share_card.text_width(line, 34), 600)
