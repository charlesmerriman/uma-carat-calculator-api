"""
The game-data scripts under scripts/: rebuilding a JP mdb from a CSV export,
the extractor's effects and evolutions, the UmaTL slice, and the borderless
card upload's file picking, naming and resize.

The scripts are not a package, so each is loaded from its path. Everything
runs against tiny made-up databases in a temp folder; no game file is needed.
"""

import csv
import importlib.util
import io
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from .base import CalculatorTestCase

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load_script(name):
    spec = importlib.util.spec_from_file_location(f"scripts_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rebuild_script = load_script("jp_export_to_mdb")
extractor = load_script("extract_master_snapshot")
umatl = load_script("fetch_umatl")
card_upload = load_script("upload_borderless_cards")


def write_export(root, tables, counts=None):
    """
    A CSV export shaped like the teammate's: {table: (columns, rows)}, where
    columns are (name, type, pk). `counts` overrides the catalogue's row counts.
    """
    (root / "masterdata_tables").mkdir()
    schema = {"tables": {}}
    with (root / "masterdata_catalog.csv").open("w", encoding="utf-8-sig", newline="") as f:
        catalogue = csv.writer(f)
        catalogue.writerow(["table_name", "row_count", "column_count", "columns", "count_error"])
        for table, (columns, rows) in tables.items():
            schema["tables"][table] = {"columns": [
                {"cid": i, "name": name, "type": kind, "notnull": 1, "dflt_value": None, "pk": pk}
                for i, (name, kind, pk) in enumerate(columns)
            ]}
            with (root / "masterdata_tables" / f"{table}.csv").open("w", encoding="utf-8-sig", newline="") as t:
                writer = csv.writer(t)
                writer.writerow([name for name, _, _ in columns])
                writer.writerows(rows)
            count = (counts or {}).get(table, len(rows))
            catalogue.writerow([table, count, len(columns), "", ""])
    (root / "masterdata_schema_and_previews.json").write_text(json.dumps(schema), encoding="utf-8")


class JpExportRebuildTests(CalculatorTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.export = self.tmp / "export"
        self.export.mkdir()
        self.out = self.tmp / "out" / "master.mdb"

    def test_columns_get_their_declared_types_back(self):
        # The whole point: CSV text "100101" must become the integer 100101,
        # or every id comparison in the extractor matches nothing.
        write_export(self.export, {
            "skill_data": ([("id", "INTEGER", 1), ("condition_1", "TEXT", 0)],
                           [["100101", "order==1&phase>=2"]]),
        })
        rebuild_script.rebuild(self.export, self.out)
        conn = sqlite3.connect(self.out)
        self.assertEqual(
            conn.execute("SELECT typeof(id), id, condition_1 FROM skill_data").fetchone(),
            ("integer", 100101, "order==1&phase>=2"),
        )
        self.assertEqual(conn.execute("SELECT id FROM skill_data WHERE id = 100101").fetchone(), (100101,))

    def test_a_composite_key_is_kept(self):
        write_export(self.export, {
            "text_data": ([("category", "INTEGER", 1), ("index", "INTEGER", 2), ("text", "TEXT", 0)],
                          [["47", "1", "a"], ["48", "1", "b"]]),
        })
        rebuild_script.rebuild(self.export, self.out)
        conn = sqlite3.connect(self.out)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO text_data (category, "index", text) VALUES (47, 1, "dup")')

    def test_a_short_csv_stops_the_rebuild(self):
        write_export(
            self.export,
            {"skill_data": ([("id", "INTEGER", 1)], [["1"], ["2"]])},
            counts={"skill_data": 3},
        )
        with self.assertRaisesRegex(SystemExit, "2 rows in the CSV, the catalogue says 3"):
            rebuild_script.rebuild(self.export, self.out)

    def test_a_name_that_isnt_the_games_is_refused(self):
        # The export is someone else's file: nothing odd reaches the SQL.
        write_export(self.export, {'x"; DROP TABLE y; --': ([("id", "INTEGER", 1)], [])})
        with self.assertRaisesRegex(SystemExit, "refusing unexpected table name"):
            rebuild_script.rebuild(self.export, self.out)


def skill_row(**overrides):
    """A skill_data row with every effect column, empty unless overridden."""
    row = {"condition_2": "", "float_ability_time_1": 0, "ability_time_usage_1": 1,
           "float_cooldown_time_1": 0, "float_ability_time_2": 0, "ability_time_usage_2": 1,
           "float_cooldown_time_2": 0}
    for n in (1, 2):
        for m in (1, 2, 3):
            row.update({
                f"ability_type_{n}_{m}": 0, f"float_ability_value_{n}_{m}": 0,
                f"ability_value_usage_{n}_{m}": 1, f"ability_value_level_usage_{n}_{m}": 1,
                f"additional_activate_type_{n}_{m}": 0, f"target_type_{n}_{m}": 1,
                f"target_value_{n}_{m}": 0,
            })
    row.update(overrides)
    return row


class EffectSlotTests(CalculatorTestCase):
    def test_a_one_slot_skill_keeps_its_raw_numbers(self):
        # Xceleration-like: one speed boost, 2.4 s, 500 s cooldown, verbatim.
        slots = extractor.effect_slots(skill_row(
            float_ability_time_1=24000, float_cooldown_time_1=5000000,
            ability_type_1_1=27, float_ability_value_1_1=3500,
        ))
        self.assertEqual(slots, [{
            "duration": 24000, "duration_usage": 1, "cooldown": 5000000,
            "abilities": [{"type": 27, "value": 3500, "value_usage": 1, "value_level_usage": 1,
                           "additional_activate_type": 0, "target_type": 1, "target_value": 0}],
        }])

    def test_slot_two_only_with_a_second_condition(self):
        self.assertEqual(len(extractor.effect_slots(skill_row(ability_type_1_1=27))), 1)
        two = extractor.effect_slots(skill_row(
            ability_type_1_1=27, condition_2="order==1", ability_type_2_1=31, ability_type_2_3=9,
        ))
        self.assertEqual([[a["type"] for a in slot["abilities"]] for slot in two], [[27], [31, 9]])


def evolution_db(with_side_tables=True):
    """An in-memory mdb holding only the tables extract_evolutions reads."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE text_data (category INTEGER, "index" INTEGER, text TEXT);
        CREATE TABLE skill_upgrade_description (id INTEGER, card_id INTEGER, rank INTEGER,
                                                skill_id INTEGER, start_date INTEGER);
        CREATE TABLE skill_upgrade_condition (id INTEGER, description_id INTEGER, num INTEGER,
                                              sub_num INTEGER, timing_type INTEGER, count_type INTEGER);
        INSERT INTO skill_upgrade_description VALUES (100101111, 100101, 3, 100101111, 1677207600);
        INSERT INTO skill_upgrade_condition VALUES (10010101, 100101111, 1, 1, 1, 1);
        INSERT INTO skill_upgrade_condition VALUES (10010102, 100101111, 2, 1, 1, 1);
        INSERT INTO text_data VALUES (290, 10010101, 'Reach 600 Stamina');
    """)
    if with_side_tables:
        conn.executescript("""
            CREATE TABLE skill_upgrade_speciality (id INTEGER, scenario_id INTEGER,
                                                   base_skill_id INTEGER, skill_id INTEGER, start_date INTEGER);
            CREATE TABLE skill_upgrade_succession_skill (id INTEGER, card_id INTEGER,
                                                         base_skill_id INTEGER, skill_id INTEGER, start_date INTEGER);
            INSERT INTO skill_upgrade_speciality VALUES (407011, 7, 202801, 407011, 1708743600);
            INSERT INTO skill_upgrade_succession_skill VALUES (901351, 113501, 901351, 91101351, 1766322600);
            INSERT INTO skill_upgrade_condition VALUES (90135101, 901351, 1, 1, 3, 2);
        """)
    return conn


class EvolutionExtractTests(CalculatorTestCase):
    def test_an_outfit_evolution_with_its_conditions_and_text(self):
        evolutions = extractor.extract_evolutions(evolution_db())
        [outfit] = evolutions["outfit"]
        self.assertEqual(
            (outfit["skill_id"], outfit["card_id"], outfit["rank"]), (100101111, 100101, 3),
        )
        # The game names no base for an outfit evolution, so nor does the file.
        self.assertNotIn("base_skill_id", outfit)
        self.assertEqual([c["id"] for c in outfit["conditions"]], [10010101, 10010102])
        self.assertEqual([c["text"] for c in outfit["conditions"]], ["Reach 600 Stamina", ""])

    def test_conditions_join_on_the_rows_own_id_not_the_skill_id(self):
        # Succession row 901351 evolves into skill 91101351; its condition
        # names 901351.
        [succession] = extractor.extract_evolutions(evolution_db())["succession"]
        self.assertEqual((succession["skill_id"], succession["base_skill_id"]), (91101351, 901351))
        self.assertEqual([c["id"] for c in succession["conditions"]], [90135101])

    def test_a_client_without_the_side_tables_reads_as_empty(self):
        evolutions = extractor.extract_evolutions(evolution_db(with_side_tables=False))
        self.assertEqual((evolutions["scenario"], evolutions["succession"]), ([], []))
        self.assertEqual(len(evolutions["outfit"]), 1)


class UmaTLSliceTests(CalculatorTestCase):
    def test_keeps_only_wanted_ids_sorted_and_drops_blanks(self):
        full = {
            "47": {"20": "Beta", "3": "Alpha", "99": "Not ours", "5": "  "},
            "48": {"3": "Does a thing"},
            "290": {"10010101": "Reach 600 Stamina"},
            "6": {"1001": "Special Week"},  # a category we never take
        }
        wanted = {
            "skill_names": {"3", "5", "20"},
            "skill_descriptions": {"3", "5", "20"},
            "evolution_conditions": {"10010101", "10010102"},
        }
        text = umatl.slice_text(full, wanted)
        self.assertEqual(list(text["skill_names"].items()), [("3", "Alpha"), ("20", "Beta")])
        self.assertEqual(text["skill_descriptions"], {"3": "Does a thing"})
        self.assertEqual(text["evolution_conditions"], {"10010101": "Reach 600 Stamina"})
        self.assertEqual(set(text), {"skill_names", "skill_descriptions", "evolution_conditions"})


class UploadBorderlessCardsTests(CalculatorTestCase):
    """scripts/upload_borderless_cards.py, short of the upload itself: which
    files it takes from Daptrius's download, what it names them, and the resize.
    Tiny made-up PNGs; no game file and no Space."""

    @staticmethod
    def png(size=(12, 16), colour=(200, 40, 40)):
        out = io.BytesIO()
        Image.new("RGB", size, colour).save(out, "PNG")
        return out.getvalue()

    def write_zip(self, path, entries):
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)

    def test_takes_card_textures_only_and_each_card_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            textures = "Data/jp/scraper/Game Images/support_cards/"
            self.write_zip(root / "one.zip", {
                f"{textures}tex_support_card_30024__tex_support_card_30024__Texture2D__-1.png": self.png(),
                # Not a card texture: an uma's, a six-digit id, a stray file.
                f"{textures}tex_chara_100101__x.png": self.png(),
                f"{textures}tex_support_card_300240__x.png": self.png(),
                "Data/jp/notes.txt": b"hello",
                "Data/shared/support_cards.json": json.dumps([
                    {"id": 10089, "name_en": "Jungle Pocket"},
                ]),
            })
            # The same card unzipped beside its zip, plus one more.
            folder = root / "one" / "Data" / "jp" / "scraper" / "Game Images" / "support_cards"
            folder.mkdir(parents=True)
            (folder / "tex_support_card_30024__tex_support_card_30024__Texture2D__-1.png").write_bytes(self.png())
            (folder / "tex_support_card_10089__tex_support_card_10089__Texture2D__7.png").write_bytes(self.png())

            found, names = card_upload.read_sources([root / "one.zip", root / "one"])

        self.assertEqual(sorted(found), [10089, 30024])
        self.assertEqual(names, {10089: "Jungle Pocket"})

    def test_names_follow_the_bordered_pictures(self):
        global_cards = {30024: {"chara_name": "Oguri Cap", "card_type": "power"}}
        jp_cards = {
            30024: {"chara_name": "オグリキャップ", "card_type": "power"},
            10089: {"chara_name": "ジャングルポケット", "card_type": "speed"},
            30081: {"chara_name": "チームシリウス", "card_type": "group"},
            10094: {"chara_name": "佐岳メイ", "card_type": "friend"},
        }
        daptrius = {30024: "Should Lose", 10089: "Jungle Pocket", 10094: "Mei Satake"}

        def name(game_id):
            return card_upload.file_name(game_id, global_cards, jp_cards, daptrius)

        # Global's name wins; a JP-only card takes Daptrius's English.
        self.assertEqual(name(30024), "30024-Oguri-Cap-pwr.webp")
        self.assertEqual(name(10089), "10089-Jungle-Pocket-spd.webp")
        self.assertEqual(name(10094), "10094-Mei-Satake-pls.webp")
        # No English name anywhere: the id and the type, never the Japanese.
        self.assertEqual(name(30081), "30081-grp.webp")

    def test_shrinks_to_the_sites_card_size_as_webp(self):
        webp = card_upload.to_webp(self.png(size=(1536, 2048)))

        with Image.open(io.BytesIO(webp)) as image:
            self.assertEqual((image.format, image.size), ("WEBP", (450, 600)))
