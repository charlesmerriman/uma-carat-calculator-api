"""
Fills the game-data columns on Uma and SupportCard from the committed snapshot.

THE SOURCE is scripts/data/master_snapshot/*.json, written by
scripts/extract_master_snapshot.py from the global game client's own database.
It is global-only by construction, so a JP-only row in our database is simply
never matched and never touched.

WHAT IT WRITES, and what it leaves alone:

  Skill        every column the game owns: name, description, rarity, group,
               tier, icon, cost, conditions. Matched on `game_id`, and CREATED
               when missing: a skill needs no art of its own (63 generic icons
               are linked afterwards by `link_skill_images`), so there is no
               reason to make an editor add 718 rows by hand. Never touches
               image or admin_comments. `description_detailed` is written only
               when `--gametora FILE` is given (see below).
  Uma          title, rarity, running_style, the ten apt_* grades, the five
               base_* stats at the initial star count, the five growth_*
               bonuses, and `is_three_star` (= rarity is 3), which the selector
               pickers read. Matched on `game_id`.
  SupportCard  card_type, character_id, title. Matched on `game_id`.
  UmaSkill     one row per (uma, skill, source) the game lists: unique, innate
               and awakening skills, with `level` (see the model). Rows are
               ADDED and never deleted, so a hand-added row survives. An
               outfit not in our database is skipped along with its rows.
  SupportCardSkill  `hint` rows from the snapshot's support_hints.json (the
               game's own data) and `event` rows from support_events.json
               (gametora, via scripts/fetch_support_events.py; optional, so a
               snapshot without it imports hints alone). Same add-never-delete
               rule, matched on the card's game_id.

  Never: name, image, admin_comments, purpose, is_time_limited. Those are the
  editors'. Never creates an Uma or SupportCard either: a card needs art, and a
  row created here would sit in every picker with a broken image. A snapshot id
  with no row is reported so an editor can add it through the admin, image and
  all, and the next run fills it in.

It is an overwrite, not a merge: the game's value wins on every column above,
every run. That is what makes it idempotent and what the admin's "Game data"
fieldset warns about.

SANITY CHECK: the game's character name must appear in our row's name
("Oguri Cap" in "[Get Lots of Hugs for Me] Oguri Cap" or "Oguri Cap (Summer)").
A miss almost always means the row's game_id is wrong (a mislabeled image), so
the row is reported and skipped rather than overwritten with another card's
numbers.

THE DETAILED DESCRIPTIONS come from a separate file, gametora's skills.json,
whose `desc_en` carries the concrete numbers the game text leaves out. Pass it
with `--gametora path/to/skills.json`: a list of objects with an `id` and a
`desc_en`. Without it that column is left as it is.

Usage:
    python manage.py import_game_data --dry-run    # report only
    python manage.py import_game_data              # prompts
    python manage.py import_game_data --no-input   # scripted
    python manage.py import_game_data --snapshot path/to/dir

Run against PRODUCTION with --dry-run first, read it, then --apply. The rows it
touches are served by /calculator-data, so it drops that cache when done.
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from calculatorapi import public_payload_cache
from calculatorapi.models import Rarity, Skill, SupportCard, SupportCardSkill, Uma, UmaSkill

CONFIRM_PHRASE = "import"

DEFAULT_SNAPSHOT = Path(__file__).resolve().parents[3] / "scripts" / "data" / "master_snapshot"

# snapshot key -> Uma column, for the blocks that copy straight across.
APTITUDE_COLUMNS = {
    "turf": "apt_turf", "dirt": "apt_dirt",
    "short": "apt_short", "mile": "apt_mile", "medium": "apt_medium", "long": "apt_long",
    "front": "apt_front", "pace": "apt_pace", "late": "apt_late", "end": "apt_end",
}
STAT_NAMES = ("speed", "stamina", "power", "guts", "wit")
RUNNING_STYLES = {"front": 1, "pace": 2, "late": 3, "end": 4}


def load_snapshot(directory):
    """The snapshot files this command reads, as parsed JSON."""
    directory = Path(directory)
    if not directory.is_dir():
        raise CommandError(f"snapshot directory not found: {directory}")
    files = {}
    for name in ("skills", "cards", "card_skills", "support_cards", "support_hints"):
        path = directory / f"{name}.json"
        if not path.is_file():
            raise CommandError(f"snapshot file missing: {path}")
        files[name] = json.loads(path.read_text(encoding="utf-8"))
    # Event skills come from gametora, not the game, so the file is optional.
    events = directory / "support_events.json"
    files["support_events"] = (
        json.loads(events.read_text(encoding="utf-8")) if events.is_file() else []
    )
    return files


def uma_values(card):
    """The Uma column values one snapshot outfit implies."""
    initial = next(
        (star for star in card["stars"] if star["star"] == card["default_rarity"]), None,
    )
    if initial is None:
        raise CommandError(
            f"card {card['id']} has no per-star row for its initial rarity "
            f"{card['default_rarity']}; the snapshot is inconsistent"
        )
    values = {
        "title": card["title"],
        "rarity": card["default_rarity"],
        "running_style": RUNNING_STYLES[card["running_style"]],
        "is_three_star": card["default_rarity"] == Rarity.THREE,
    }
    for key, column in APTITUDE_COLUMNS.items():
        values[column] = initial["aptitude"][key]
    for stat in STAT_NAMES:
        values[f"base_{stat}"] = initial["stats"][stat]
        values[f"growth_{stat}"] = card["growth"][stat]
    return values


def load_gametora(path):
    """{skill id: desc_en} from gametora's skills.json, or {} when no path is given."""
    if not path:
        return {}
    path = Path(path)
    if not path.is_file():
        raise CommandError(f"gametora file not found: {path}")
    entries = json.loads(path.read_text(encoding="utf-8"))
    detailed = {}
    for entry in entries:
        try:
            detailed[int(entry["id"])] = entry.get("desc_en") or ""
        except (KeyError, TypeError, ValueError) as exc:
            raise CommandError(f"gametora entry without a usable id: {entry!r}") from exc
    return detailed


def skill_values(skill, detailed):
    """The Skill column values one snapshot skill implies."""
    values = {
        "name": skill["name"],
        "description": skill["description"],
        "rarity": skill["rarity"],
        "group_id": skill["group_id"],
        "tier": skill["group_rate"],
        "icon_id": skill["icon_id"],
        "cost": skill["cost"],
        "precondition": skill["precondition_1"],
        "condition": skill["condition_1"],
    }
    if skill["id"] in detailed:
        values["description_detailed"] = detailed[skill["id"]]
    return values


def support_values(card):
    """The SupportCard column values one snapshot support card implies."""
    return {
        "title": card["title"],
        "card_type": card["card_type"],
        "character_id": card["chara_id"],
    }


class JunctionSpec:
    """One skill junction table: who owns the rows and whether they carry a level."""

    def __init__(self, label, owner_model, model, owner_field):
        self.label = label
        self.owner_model = owner_model
        self.model = model
        self.owner_field = owner_field
        self.has_level = any(field.name == "level" for field in model._meta.fields)  # pylint: disable=protected-access

    def existing_levels(self):
        """{(owner game_id, skill game_id, source): level} for every row in the table."""
        columns = [f"{self.owner_field}__game_id", "skill__game_id", "source"]
        if self.has_level:
            columns.append("level")
        return {
            tuple(values[:3]): (values[3] if self.has_level else None)
            for values in self.model.objects.values_list(*columns)
        }

    def fields(self, owner, skill, source, level=None):
        """Constructor / filter kwargs for one row."""
        fields = {self.owner_field: owner, "skill": skill, "source": source}
        if self.has_level and level is not None:
            fields["level"] = level
        return fields


UMA_SKILLS = JunctionSpec("uma skill", Uma, UmaSkill, "uma")
SUPPORT_CARD_SKILLS = JunctionSpec("support card skill", SupportCard, SupportCardSkill, "support_card")


def _letters(text):
    """Lowercase letters and digits only, so "TM Opera O" and "T.M. Opera O" agree."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def name_matches(row_name, chara_name):
    """Our row names always contain the character's name; a miss is a wrong game_id."""
    return _letters(chara_name) in _letters(row_name)


class Command(BaseCommand):
    help = "Fill Uma and SupportCard game-data columns from the committed master snapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--snapshot", default=DEFAULT_SNAPSHOT,
            help="Directory holding the snapshot JSON (default: scripts/data/master_snapshot).",
        )
        parser.add_argument(
            "--gametora", default=None,
            help="gametora's skills.json, for description_detailed. Optional.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would change without writing anything.",
        )
        parser.add_argument(
            "--no-input", action="store_true",
            help="Skip the confirmation prompt (for scripted runs).",
        )

    def handle(self, *args, **options):
        snapshot = load_snapshot(options["snapshot"])
        detailed = load_gametora(options["gametora"])

        uma_plan = self._plan("uma", Uma, snapshot["cards"], uma_values)
        support_plan = self._plan(
            "support card", SupportCard, snapshot["support_cards"], support_values,
        )
        known_skills = {skill["id"] for skill in snapshot["skills"]}
        plans = [
            self._plan_skills(snapshot["skills"], detailed),
            uma_plan,
            support_plan,
            self._plan_junction(
                UMA_SKILLS,
                [
                    (row["card_id"], row["skill_id"], row["source"], row["level"])
                    for row in snapshot["card_skills"]
                ],
                known_skills, distrusted=uma_plan["distrusted"],
            ),
            self._plan_junction(
                SUPPORT_CARD_SKILLS,
                [
                    (row["support_card_id"], row["skill_id"], "hint", None)
                    for row in snapshot["support_hints"]
                ] + [
                    (row["support_card_id"], row["skill_id"], "event", None)
                    for row in snapshot["support_events"]
                ],
                known_skills, distrusted=support_plan["distrusted"],
            ),
        ]
        self._report(plans, options["dry_run"])

        if not any(plan["changed"] or plan.get("created") for plan in plans):
            self.stdout.write(self.style.SUCCESS("\nNothing to write."))
            return

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\nDry run: nothing written."))
            return

        if not options["no_input"]:
            typed = input(f'\nType "{CONFIRM_PHRASE}" to apply: ')
            if typed.strip() != CONFIRM_PHRASE:
                self.stdout.write(self.style.ERROR("Aborted."))
                return

        written = self._write(plans)
        self.stdout.write(self.style.SUCCESS(f"\nUpdated {written} row(s)."))

    @staticmethod
    def _plan(label, model, cards, values_for):
        """
        Match every snapshot card to a row and work out what would change.

        Rows are matched on game_id in one query; the loop then only touches
        Python objects, so a dry run is as cheap as it looks.
        """
        by_game_id = {row.game_id: row for row in model.objects.exclude(game_id__isnull=True)}
        changed, unchanged, missing, mismatched = [], 0, [], []
        distrusted = set()  # game_ids the name check rejected; junctions skip them too

        for card in cards:
            row = by_game_id.get(card["id"])
            if row is None:
                missing.append(f"{card['id']}  {card['name']}")
                continue
            if not name_matches(row.name, card["chara_name"]):
                mismatched.append(
                    f"pk={row.pk} '{row.name}' has game_id {card['id']}, which the game "
                    f"says is '{card['name']}'; check the id"
                )
                distrusted.add(card["id"])
                continue
            values = values_for(card)
            diff = {
                column: value for column, value in values.items()
                if getattr(row, column) != value
            }
            if diff:
                changed.append((row, diff))
            else:
                unchanged += 1

        return {
            "label": label, "changed": changed, "unchanged": unchanged,
            "missing": missing, "mismatched": mismatched, "distrusted": distrusted,
        }

    @staticmethod
    def _plan_skills(skills, detailed):
        """
        Like _plan, but a skill with no row is CREATED rather than reported:
        `created` holds unsaved Skill instances, `changed` (row, diff) pairs.
        """
        by_game_id = {row.game_id: row for row in Skill.objects.all()}
        changed, created, unchanged = [], [], 0
        for skill in skills:
            values = skill_values(skill, detailed)
            row = by_game_id.get(skill["id"])
            if row is None:
                created.append(Skill(game_id=skill["id"], **values))
                continue
            diff = {
                column: value for column, value in values.items()
                if getattr(row, column) != value
            }
            if diff:
                changed.append((row, diff))
            else:
                unchanged += 1
        return {
            "label": "skill", "changed": changed, "created": created,
            "unchanged": unchanged, "missing": [], "mismatched": [],
        }

    @staticmethod
    def _plan_junction(spec, rows, known_skills, distrusted=frozenset()):
        """
        Junction rows to add or update, as (owner game_id, skill game_id, source, level).

        Shared by UmaSkill and SupportCardSkill (whose `level` is always None).
        Skills are resolved by game_id at write time, after the skill rows of
        this same run exist, so a first run does not report every skill as
        missing. What can be missing is the owner: a snapshot card with no row
        here is reported once by its own plan, so its skills are skipped
        quietly. So is a card that plan `distrusted`: an id the name check
        rejected is no better a key for its skills than for its stats.
        """
        owner_ids = set(
            spec.owner_model.objects.exclude(game_id__isnull=True)
            .values_list("game_id", flat=True)
        ) - set(distrusted)
        known = known_skills | set(Skill.objects.values_list("game_id", flat=True))
        existing = spec.existing_levels()

        plan = {
            "label": spec.label, "changed": [], "created": [], "unchanged": 0,
            "missing": [], "mismatched": [], "junction": spec,
        }
        seen = set()
        for owner_id, skill_id, source, level in rows:
            key = (owner_id, skill_id, source)
            if key in seen or owner_id not in owner_ids:
                continue  # a duplicate pairing (a hint in two groups), or no row
            seen.add(key)
            if skill_id not in known:
                plan["missing"].append(
                    f"skill {skill_id} for {spec.label} {owner_id} is in no snapshot"
                )
            elif key not in existing:
                plan["created"].append((*key, level))
            elif spec.has_level and existing[key] != level:
                plan["changed"].append((*key, level))
            else:
                plan["unchanged"] += 1
        return plan

    @staticmethod
    def _write_junction(plan):
        """Resolve the game ids to rows now that every skill exists, then add."""
        spec = plan["junction"]
        owners = {
            row.game_id: row for row in spec.owner_model.objects.exclude(game_id__isnull=True)
        }
        skills = {skill.game_id: skill for skill in Skill.objects.all()}
        spec.model.objects.bulk_create([
            spec.model(**spec.fields(owners[owner_id], skills[skill_id], source, level))
            for owner_id, skill_id, source, level in plan["created"]
        ])
        for owner_id, skill_id, source, level in plan["changed"]:
            spec.model.objects.filter(
                **spec.fields(owners[owner_id], skills[skill_id], source)
            ).update(level=level)
        return len(plan["created"]) + len(plan["changed"])

    @staticmethod
    def _write(plans):
        """
        Every row in one transaction, so a crash halfway leaves the columns as
        they were rather than half of one import and half of the last.
        """
        written = 0
        with transaction.atomic():
            for plan in plans:
                if plan.get("junction"):
                    written += Command._write_junction(plan)
                    continue
                for row, diff in plan["changed"]:
                    for column, value in diff.items():
                        setattr(row, column, value)
                    row.save(update_fields=list(diff))
                    written += 1
                new_rows = plan.get("created", [])
                Skill.objects.bulk_create(new_rows)
                written += len(new_rows)
        # Per-row saves already fire the invalidation signal, but the invariant
        # is that a bulk writer drops the cache itself, so this does too.
        public_payload_cache.invalidate()
        return written

    def _report(self, plans, dry_run):
        verb = "Would update" if dry_run else "Updating"
        for plan in plans:
            label = plan["label"]
            created = plan.get("created")
            if plan.get("junction"):
                self.stdout.write(
                    f"\n{label}s: {'would add' if dry_run else 'adding'} {len(created)}, "
                    f"{verb.lower()} {len(plan['changed'])}, already current {plan['unchanged']}"
                )
                for line in plan["missing"]:
                    self.stdout.write(self.style.WARNING(f"    {line}"))
                continue
            if created is not None:
                # Skills: created rather than reported missing.
                self.stdout.write(
                    f"\n{label}s: {verb.lower()} {len(plan['changed'])}, "
                    f"{'would create' if dry_run else 'creating'} {len(created)}, "
                    f"already current {plan['unchanged']}"
                )
            else:
                self.stdout.write(
                    f"\n{label}s: {verb.lower()} {len(plan['changed'])}, "
                    f"already current {plan['unchanged']}, "
                    f"not in database {len(plan['missing'])}, "
                    f"name mismatch {len(plan['mismatched'])}"
                )
            for row, diff in plan["changed"]:
                columns = ", ".join(sorted(diff))
                self.stdout.write(f"    pk={row.pk:<5} {row.name[:40]:<40} {columns}")
            if plan["missing"]:
                self.stdout.write(self.style.WARNING(
                    f"  Not in database ({len(plan['missing'])}); add through the admin, "
                    "with an image, and re-run:"
                ))
                for line in plan["missing"]:
                    self.stdout.write(f"    {line}")
            if plan["mismatched"]:
                self.stdout.write(self.style.WARNING(
                    f"  Name mismatch ({len(plan['mismatched'])}); left alone:"
                ))
                for line in plan["mismatched"]:
                    self.stdout.write(f"    {line}")
