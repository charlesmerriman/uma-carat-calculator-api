"""calculatorapi/data/character_colours.json: the game's two colours per character."""

import json
import sqlite3
from pathlib import Path

from django.test import SimpleTestCase

from calculatorapi import character_colours
from calculatorapi.tests.test_snapshot_scripts import load_script

extract_master_snapshot = load_script("extract_master_snapshot")

SNAPSHOTS = Path(__file__).resolve().parents[2] / "scripts" / "data"


class CharacterColoursTests(SimpleTestCase):
    def test_every_value_is_a_plain_hex_colour(self):
        """They are written into a style attribute, so nothing else may get through."""
        colours = character_colours.table()
        self.assertGreater(len(colours), 90)
        for character, pair in colours.items():
            self.assertTrue(character.isdigit())
            self.assertEqual(len(pair), 2)
            for value in pair:
                self.assertRegex(value, r"^#[0-9A-F]{6}$")

    def test_the_committed_file_is_the_two_snapshots_merged(self):
        """A snapshot refresh that changes a colour fails here until the API's copy is rewritten."""
        merged = extract_master_snapshot.merged_character_colours(
            [SNAPSHOTS / "master_snapshot", SNAPSHOTS / "jp_snapshot"]
        )
        self.assertEqual(merged, json.loads(character_colours.COLOURS_FILE.read_text(encoding="utf-8")))

    def test_one_characters_pair_or_none(self):
        self.assertEqual(character_colours.colours_for(1006), ["#3A7AD2", "#ECE7E7"])  # Oguri Cap
        self.assertIsNone(character_colours.colours_for(999999))
        self.assertIsNone(character_colours.colours_for(None))

    def test_an_unset_white_main_is_left_out(self):
        """The game leaves a character it never draws a training screen for at FFFFFF."""
        self.assertFalse([pair for pair in character_colours.table().values() if pair[0] == "#FFFFFF"])


class ExtractCharactersTests(SimpleTestCase):
    """scripts/extract_master_snapshot.py reading chara_data."""

    def read(self, rows):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE chara_data (id INTEGER, ui_color_main TEXT, ui_color_sub TEXT)")
        conn.executemany("INSERT INTO chara_data VALUES (?, ?, ?)", rows)
        return extract_master_snapshot.extract_characters(conn)

    def test_a_pair_per_character_upper_cased_with_its_hash(self):
        self.assertEqual(
            self.read([(1068, "5a6065", "fc6d31"), (1006, "3A7AD2", "ECE7E7")]),
            [{"id": 1006, "colour_main": "#3A7AD2", "colour_sub": "#ECE7E7"},
             {"id": 1068, "colour_main": "#5A6065", "colour_sub": "#FC6D31"}],
        )

    def test_an_unset_row_is_skipped_and_a_bad_value_stops_the_run(self):
        self.assertEqual(self.read([(9002, "FFFFFF", "FFFFFF"), (9003, "", "")]), [])
        with self.assertRaises(SystemExit):
            self.read([(1001, "red;}", "FFDEF9")])
