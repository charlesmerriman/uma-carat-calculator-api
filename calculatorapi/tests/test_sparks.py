"""Sparks: the extractor's slice of the game's succession_factor table, and the import that fills the Spark table."""

import json
import sqlite3
import tempfile
from pathlib import Path

from calculatorapi.models import Spark
from calculatorapi.tests.base import CalculatorTestCase
from calculatorapi.tests.test_skills import import_game_data, write_snapshot
from calculatorapi.tests.test_snapshot_scripts import extractor


def spark_db():
    """An in-memory mdb holding only what extract_sparks reads."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE text_data (category INTEGER, "index" INTEGER, text TEXT);
        CREATE TABLE succession_factor (factor_id INTEGER, factor_type INTEGER, rarity INTEGER);
        INSERT INTO succession_factor VALUES (103, 1, 3);
        INSERT INTO succession_factor VALUES (3402, 2, 2);
        INSERT INTO succession_factor VALUES (9000101, 9, 1);
        INSERT INTO text_data VALUES (147, 103, 'Speed');
        INSERT INTO text_data VALUES (147, 3402, 'Long');
        INSERT INTO text_data VALUES (47, 103, 'Not a spark name');
    """)
    return conn


def snapshot_spark(spark_id=103, kind="stat", stars=3, name="Speed"):
    return {"id": spark_id, "kind": kind, "stars": stars, "name": name}


class SparkExtractTests(CalculatorTestCase):
    def test_stars_are_the_rarity_and_a_new_type_keeps_its_number(self):
        sparks = extractor.extract_sparks(spark_db())
        self.assertEqual(sparks, [
            {"id": 103, "kind": "stat", "stars": 3, "name": "Speed"},
            {"id": 3402, "kind": "aptitude", "stars": 2, "name": "Long"},
            {"id": 9000101, "kind": "9", "stars": 1, "name": ""},
        ])


class SparkImportTests(CalculatorTestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(self.tmp.cleanup)

    def write(self, sparks):
        write_snapshot(self.tmp.name, [])
        (Path(self.tmp.name) / "sparks.json").write_text(json.dumps(sparks), encoding="utf-8")

    def test_creates_every_spark_then_follows_the_game(self):
        self.write([snapshot_spark(), snapshot_spark(3402, "aptitude", 2, "Long")])
        out = import_game_data(self.tmp.name)
        self.assertIn("sparks: updating 0, creating 2, already current 0", out)
        self.assertEqual(str(Spark.objects.get(id=3402)), "Long ★★")

        self.write([snapshot_spark(name="Speed!"), snapshot_spark(3402, "aptitude", 2, "Long")])
        out = import_game_data(self.tmp.name)
        self.assertIn("sparks: updating 1, creating 0, already current 1", out)
        self.assertEqual(Spark.objects.get(id=103).name, "Speed!")
        self.assertIn("Nothing to write.", import_game_data(self.tmp.name))

    def test_a_kind_the_model_does_not_name_is_skipped_and_said(self):
        self.write([snapshot_spark(), snapshot_spark(9000101, "9", 1, "")])
        out = import_game_data(self.tmp.name)
        self.assertEqual(list(Spark.objects.values_list("id", flat=True)), [103])
        self.assertIn("1 of a kind models/spark.py doesn't name yet, skipped", out)

    def test_a_snapshot_without_sparks_imports_the_rest(self):
        write_snapshot(self.tmp.name, [])
        self.assertIn("Nothing to write.", import_game_data(self.tmp.name))
        self.assertFalse(Spark.objects.exists())
