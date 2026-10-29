"""
Fills the game-data columns on Uma and SupportCard from the committed snapshot.

THE SOURCES, in order of authority (since 2026-10-23):

  1. scripts/data/master_snapshot/*.json, written by
     scripts/extract_master_snapshot.py from the global game client's own
     database. Global always wins.
  2. scripts/data/jp_snapshot/*.json, the same extractor run on a JP mdb:
     the structure of skills global doesn't define, evolutions, and the
     skill links of outfits and cards global doesn't have.
  3. scripts/data/umatl/text.json, UmaTL's English, only ever into the
     `translated_*` columns, which a player reads only while global's are blank.

  The two extra sources are read from beside master_snapshot/ (or from
  --jp-snapshot / --umatl); either may be missing, and --global-only ignores
  both. The JP pass's helpers live in calculatorapi/game_data_sources.py.

PER SKILL (matched on game_id), the first path that applies:

  in global's skill table   availability "global"; every game column from
                            global, name and description included
  announced (text ahead)    availability "announced"; global's name and text,
                            JP's exact structure every run (a guess only when
                            the JP snapshot lacks the id, written once)
  JP only                   availability "jp"; JP's structure; global's name
                            and text if the client shipped them (a bonus
                            skill), otherwise the editor's are left alone
  every path                `jp_*` from JP and `translated_*` from UmaTL when
                            loaded; a name this run replaces becomes a SkillAlias

EVOLUTIONS (JP): an outfit's evolution is an `evolved` UmaSkill row; its base
(`evolves_from`) comes from the rank rule (game_data_sources.evolution_base),
written only while empty so an editor's correction sticks; a scenario's or a
succession's base is the game's own. Unlock conditions are replaced as a set
whenever they differ: global's English, UmaTL's, and the Japanese.

`is_on_global` on Uma and SupportCard is set here: true for the ids in the
global snapshot whose `start_date` has passed (out_on_global; the client
carries a few JP-only support cards dated 2099). Global outfits and cards get
global's skill links only;
JP's links go to the others, after a name check in English (jp_owner_links).

WHAT IT WRITES, and what it leaves alone:

  Skill        every column the game owns: name, description, rarity, group,
               tier, icon, cost, conditions, effects, and `availability`
               ("global" here, "announced" below). Matched on `game_id`, and CREATED
               when missing: a skill needs no art of its own (63 generic icons
               are linked afterwards by `link_skill_images`), so there is no
               reason to make an editor add 718 rows by hand. Never touches
               image or admin_comments. `description_detailed` is written only
               when `--gametora FILE` is given (see below).
  Skill, text ahead of release (skill_text_ahead.json): the client ships the
               official English name and description of upcoming skills
               before it defines them. Uniques, their inherited copies and
               general skills (five- and six-digit ids) become rows so a guide
               can name them early. Rarity, group and tier are GUESSED from
               the id and name (guessed_structure) and written only when the
               row is created, so an editor's correction sticks; icon and
               cost stay empty. Name and description follow the game on every
               run. The day the client defines the skill it moves to
               skills.json and every column is overwritten as above. Evolved
               (nine-digit) and event bonus (seven-digit) ids are skipped.
  Uma          title, rarity, running_style, the ten apt_* grades, the five
               base_* stats at the initial star count, the five growth_*
               bonuses. `rarity` is what the selector pickers read, through
               `Uma.is_three_star`. Matched on `game_id`.
  SupportCard  card_type, rarity, character_id, title, and group_members (a
               group card's umas, by name). Matched on `game_id`. A card
               global doesn't have takes `card_type` alone from the JP
               snapshot, after the same name check as its links.
  UmaSkill     one row per (uma, skill, source) the game lists: unique, innate
               and awakening skills, with `level` (see the model). Rows are
               ADDED and never deleted, so a hand-added row survives. An
               outfit not in our database is skipped along with its rows.
  SupportCardSkill  `hint` rows from the snapshot's support_hints.json (the
               game's own data) and `event` rows from support_events.json
               (gametora, via scripts/fetch_support_events.py; optional, so a
               snapshot without it imports hints alone). Same add-never-delete
               rule, matched on the card's game_id.
  Spark        every spark in sparks.json (kind, stars, name), keyed on the
               game's own id, created when missing. Optional: a snapshot from
               before 2026-10-27 has none. A kind models/spark.py doesn't name
               is skipped and reported. Never deleted.

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
from django.utils import timezone

from calculatorapi import public_payload_cache, pvp_catalogue_cache, skills_cache
from calculatorapi.models import (
    Skill, SkillAlias, SkillEvolutionCondition, Spark, SparkKind, SupportCard, SupportCardSkill,
    Uma, UmaSkill,
)
from calculatorapi.game_data_sources import (
    SecondarySources, jp_owner_links, name_matches, plan_evolutions,
)
from calculatorapi.models.skill import SkillAvailability, SkillRarity, SkillTier

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
    # Optional: event skills come from gametora, not the game, a snapshot
    # from before 2026-10-16 has no text-ahead file, and one from before
    # 2026-10-27 no sparks.
    for name in ("support_events", "skill_text_ahead", "sparks"):
        path = directory / f"{name}.json"
        files[name] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    # Global's English for evolution conditions, keyed by condition id; a
    # snapshot from before 2026-10-23 has none.
    path = directory / "evolution_condition_text.json"
    files["evolution_condition_text"] = (
        json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    )
    return files


# Which text-ahead skills become rows, by the length of the id. Evolved skills
# (nine digits) only with the JP snapshot: the global client says nothing about
# which gold skill each one evolves from. Seven-digit ids are event and League
# of Heroes bonus skills, not skills a trainee learns.
AHEAD_ID_LENGTHS = (5, 6)
EVOLVED_ID_LENGTH = 9


def takes_ahead(skill, jp_ids=frozenset()):
    """
    Should this text-ahead skill become an ANNOUNCED row? See AHEAD_ID_LENGTHS.
    An evolved one too once the JP snapshot defines it (`jp_ids`), since that
    gives it its base. A seven-digit bonus skill never: it becomes a JP row
    with global's name, out of the guide picker.
    """
    if not skill["description"].strip():
        return False
    length = len(str(skill["id"]))
    return length in AHEAD_ID_LENGTHS or (length == EVOLVED_ID_LENGTH and skill["id"] in jp_ids)


def guessed_structure(skill_id, name, known_ids):
    """
    Rarity, group and tier for a skill the client has text for but does not
    define yet, read off its id and name. Checked against the 734 skills the
    client does define (2026-10-16):

      group_id   id // 10, for every kind. Right for all of them.
      5 digits   a ★1/★2 outfit's starting unique: rarity 3.
      1xxxx1     an outfit's unique: rarity 4 when that outfit also has a
                 starting unique (the id with the outfit digit dropped,
                 100661 -> 10661), else 5. Right for every unique.
      9xxxx1     a unique's inherited copy: rarity 1, tier 2. Right for all.
      general    the name's own mark (× penalty, ◎, ○); else an `...1` id
                 whose `...2` sibling exists is the gold; else white. Right
                 for 444 of 490: the misses are old penalty skills named
                 without a ×, and tier-3 golds.

    `known_ids` is every id the snapshot knows, defined or not, for the
    sibling checks. A wrong guess is an editor's fix in the Skill admin, and
    the game's own values replace it once the skill is defined.
    """
    digits = str(skill_id)
    group_id = skill_id // 10
    if len(digits) == 5:
        return {"rarity": SkillRarity.UNIQUE_BEFORE_THREE_STAR, "group_id": group_id,
                "tier": SkillTier.WHITE}
    if digits[0] == "1":
        has_starting_unique = digits[1] == "0" and int("1" + digits[2:]) in known_ids
        rarity = SkillRarity.UNIQUE_AT_THREE_STAR if has_starting_unique else SkillRarity.UNIQUE
        return {"rarity": rarity, "group_id": group_id, "tier": SkillTier.WHITE}
    if digits[0] == "9":
        return {"rarity": SkillRarity.WHITE, "group_id": group_id, "tier": SkillTier.GOLD}

    mark = name.rstrip()[-1:]
    if mark == "×":
        rarity, tier = SkillRarity.WHITE, SkillTier.PENALTY
    elif mark == "◎":
        rarity, tier = SkillRarity.WHITE, SkillTier.GOLD
    elif mark == "○":
        rarity, tier = SkillRarity.WHITE, SkillTier.WHITE
    elif skill_id % 10 == 1 and skill_id + 1 in known_ids:
        rarity, tier = SkillRarity.GOLD, SkillTier.GOLD
    else:
        rarity, tier = SkillRarity.WHITE, SkillTier.WHITE
    return {"rarity": rarity, "group_id": group_id, "tier": tier}


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


def effects_with_conditions(skill):
    """
    The snapshot's effect slots, each with its own (pre)condition string.

    The snapshot keeps the slots' conditions at the top level
    (`condition_1`, `condition_2`); `Skill.condition` holds slot 1's only, as
    it always has. Copying each slot's pair into its slot makes `effects`
    whole: "when slot 2 fires, it does this". The strings are the game's,
    verbatim. A snapshot from before 2026-10-23 has no effects.
    """
    slots = []
    for n, slot in enumerate(skill.get("effects", []), start=1):
        slots.append({
            "precondition": skill.get(f"precondition_{n}", ""),
            "condition": skill.get(f"condition_{n}", ""),
            **slot,
        })
    return slots


def structure_values(skill):
    """The game-owned structure columns of one snapshot skill, either client's."""
    return {
        "rarity": skill["rarity"],
        "group_id": skill["group_id"],
        "tier": skill["group_rate"],
        "icon_id": skill["icon_id"],
        "cost": skill["cost"],
        "precondition": skill["precondition_1"],
        "condition": skill["condition_1"],
        "effects": effects_with_conditions(skill),
        # A snapshot from before 2026-10-23 has no activate_lot.
        "activate_lot": skill.get("activate_lot"),
    }


def skill_values(skill, detailed):
    """The Skill column values one global snapshot skill implies."""
    values = {
        "name": skill["name"],
        "description": skill["description"],
        **structure_values(skill),
        # In the global client's skill table, so on global, every run.
        "availability": SkillAvailability.GLOBAL,
    }
    if skill["id"] in detailed:
        values["description_detailed"] = detailed[skill["id"]]
    return values


def support_values(card):
    """The SupportCard column values one snapshot support card implies."""
    return {
        "title": card["title"],
        "card_type": card["card_type"],
        "rarity": card["rarity"],
        "character_id": card["chara_id"],
        # Only group cards carry `members` in the snapshot; every other card
        # is written back to [] so a card that stops being one is cleared.
        "group_members": card.get("members", []),
    }


def out_on_global(card, now):
    """
    Whether a card in the global snapshot is out on global by `now`. Being in
    the client's table is not enough: it carries four JP-only support cards
    (30058 to 30061, Twin Turbo [Constellation of Stars] among them) dated
    2099-01-01, its "not released here" mark, so a card whose `start_date` is
    still ahead is not out (Daptrius, 2026-10-29: the Collection showed them).
    A card the snapshot gives no date (every uma) is out. Compared with the
    time the import runs, so a card dated ahead turns on at the first deploy
    after its date.
    """
    start = card.get("start_date")
    return start is None or start <= now.timestamp()


def no_longer_on_global(by_game_id, cards):
    """
    (row, diff) pairs for the rows the global snapshot doesn't hold but which
    say they are on global: not on global, whatever they said before.
    """
    in_snapshot = {card["id"] for card in cards}
    return [
        (row, {"is_on_global": False})
        for game_id, row in by_game_id.items()
        if game_id not in in_snapshot and row.is_on_global
    ]


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
            "--jp-snapshot", default=None,
            help="The JP snapshot directory (default: jp_snapshot/ beside --snapshot, if there).",
        )
        parser.add_argument(
            "--umatl", default=None,
            help="The UmaTL slice directory (default: umatl/ beside --snapshot, if there).",
        )
        parser.add_argument(
            "--global-only", action="store_true",
            help="Ignore the JP snapshot and UmaTL: the import as it was before 2026-10-23.",
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
        sources = (
            SecondarySources.empty() if options["global_only"]
            else SecondarySources(options["snapshot"], options["jp_snapshot"], options["umatl"])
        )
        evolutions = plan_evolutions(sources, snapshot["evolution_condition_text"])
        jp_links = jp_owner_links(sources, snapshot)

        uma_plan = self._plan("uma", Uma, snapshot["cards"], uma_values)
        support_plan = self._plan(
            "support card", SupportCard, snapshot["support_cards"], support_values,
        )
        self._add_jp_card_types(support_plan, jp_links["card_types"])
        known_skills = (
            {skill["id"] for skill in snapshot["skills"]}
            | {skill["id"] for skill in snapshot["skill_text_ahead"]}
            | set(sources.jp_skills)
        )
        plans = [
            self._plan_skills(
                snapshot["skills"], detailed, snapshot["skill_text_ahead"], sources, evolutions,
            ),
            uma_plan,
            support_plan,
            self._plan_junction(
                UMA_SKILLS,
                [
                    (row["card_id"], row["skill_id"], row["source"], row["level"])
                    for row in snapshot["card_skills"]
                ] + evolutions["junction"] + jp_links["uma_rows"],
                known_skills, distrusted=uma_plan["distrusted"] | jp_links["uma_distrusted"],
            ),
            self._plan_junction(
                SUPPORT_CARD_SKILLS,
                [
                    (row["support_card_id"], row["skill_id"], "hint", None)
                    for row in snapshot["support_hints"]
                ] + [
                    (row["support_card_id"], row["skill_id"], "event", None)
                    for row in snapshot["support_events"]
                ] + jp_links["card_rows"],
                known_skills, distrusted=support_plan["distrusted"] | jp_links["card_distrusted"],
            ),
            self._plan_evolution_links(evolutions, known_skills),
            self._plan_sparks(snapshot["sparks"]),
        ]
        if sources.jp is not None:
            plans.append(self._jp_notice(jp_links, evolutions))
        self._report(plans, options["dry_run"])

        if not any(
            plan["changed"] or plan.get("created") or plan.get("conditions") or plan.get("aliases")
            for plan in plans
        ):
            self.stdout.write(self.style.SUCCESS("\nNothing to write."))
            return

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("\nDry run: nothing written."))
            return

        if not options["no_input"]:
            if input(f'\nType "{CONFIRM_PHRASE}" to apply: ').strip() != CONFIRM_PHRASE:
                self.stdout.write(self.style.ERROR("Aborted."))
                return

        self.stdout.write(self.style.SUCCESS(f"\nUpdated {self._write(plans)} row(s)."))

    @staticmethod
    def _plan(label, model, cards, values_for):
        """
        Match every snapshot card to a row and work out what would change.

        Rows are matched on game_id in one query; the loop then only touches
        Python objects, so a dry run is as cheap as it looks.
        """
        by_game_id = {row.game_id: row for row in model.objects.exclude(game_id__isnull=True)}
        now = timezone.now()
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
            # In the global snapshot and dated, so out on global (the team
            # builder reads it for cards on no banner; see pvp_catalogue.py).
            values = {**values_for(card), "is_on_global": out_on_global(card, now)}
            diff = {
                column: value for column, value in values.items()
                if getattr(row, column) != value
            }
            if diff:
                changed.append((row, diff))
            else:
                unchanged += 1

        changed += no_longer_on_global(by_game_id, cards)

        return {
            "label": label, "changed": changed, "unchanged": unchanged,
            "missing": missing, "mismatched": mismatched, "distrusted": distrusted,
        }

    @staticmethod
    def _add_jp_card_types(plan, card_types):
        """
        Fold the JP-only cards' types into the support card plan. A row the
        plan already changes (it said it was on global and no longer is)
        gets the type in the same diff, so it is saved once.
        """
        pending = {row.game_id: diff for row, diff in plan["changed"]}
        for row in SupportCard.objects.filter(game_id__in=card_types):
            card_type = card_types[row.game_id]
            if row.card_type == card_type:
                continue
            if row.game_id in pending:
                pending[row.game_id]["card_type"] = card_type
            else:
                plan["changed"].append((row, {"card_type": card_type}))

    @staticmethod
    def _plan_skills(skills, detailed, ahead=(), sources=None, evolutions=None):  # pylint: disable=too-many-locals
        """
        Like _plan, but a skill with no row is CREATED rather than reported:
        `created` holds unsaved Skill instances, `changed` (row, diff) pairs.

        Three paths, in order of authority (the module docstring has the
        full table):
          1. in global's skill table: every game column from global;
          2. announced (`ahead`, the text-ahead file): global's name and
             text, and JP's exact structure when the JP snapshot defines it,
             else a guessed one written once;
          3. JP only: JP's structure; global's name and text if the client
             shipped them, else the editor's are left alone.
        Every path also takes the JP and UmaTL text slots, and a name the
        import replaces is kept as an alias (`aliases`).
        """
        sources = sources or SecondarySources.empty()
        evolutions = evolutions or plan_evolutions(sources, {})
        by_game_id = {row.game_id: row for row in Skill.objects.all()}
        changed, created, aliases, unchanged = [], [], [], 0

        def plan_row(game_id, values, create_values):
            nonlocal unchanged
            values = {**values, **sources.text_slots(game_id)}
            if game_id in evolutions["scenarios"] or game_id in evolutions["bases"]:
                values["evolution_scenario"] = evolutions["scenarios"].get(game_id)
            row = by_game_id.get(game_id)
            if row is None:
                created.append(Skill(game_id=game_id, **values, **create_values))
                return
            diff = {
                column: value for column, value in values.items()
                if getattr(row, column) != value
            }
            for column in ("name", "translated_name"):
                if column in diff and getattr(row, column):
                    aliases.append((game_id, getattr(row, column)))
            if diff:
                changed.append((row, diff))
            else:
                unchanged += 1

        for skill in skills:
            plan_row(skill["id"], skill_values(skill, detailed), {})

        defined = {skill["id"] for skill in skills}
        ahead_by_id = {skill["id"]: skill for skill in ahead}
        known_ids = defined | set(ahead_by_id)
        taken = [
            skill for skill in ahead
            if skill["id"] not in defined and takes_ahead(skill, sources.jp_skills)
        ]
        for skill in taken:
            values = {"name": skill["name"], "description": skill["description"],
                      "availability": SkillAvailability.ANNOUNCED}
            jp = sources.jp_skills.get(skill["id"])
            if jp is not None:
                # The game's own values, every run: no guessing (owner, 2026-10-16).
                plan_row(skill["id"], {**values, **structure_values(jp)}, {})
            else:
                plan_row(skill["id"], values,
                         guessed_structure(skill["id"], skill["name"], known_ids))

        handled = defined | {skill["id"] for skill in taken}
        jp_only = 0
        for game_id, jp in sources.jp_skills.items():
            if game_id in handled:
                continue
            jp_only += 1
            values = {**structure_values(jp), "availability": SkillAvailability.JP}
            text = ahead_by_id.get(game_id)
            if text is not None:  # global shipped its English (a bonus skill, say)
                values.update(name=text["name"], description=text["description"])
            plan_row(game_id, values, {})
        return {
            "label": "skill", "changed": changed, "created": created, "aliases": aliases,
            "unchanged": unchanged, "missing": [], "mismatched": [], "ahead": len(taken),
            "jp_only": jp_only,
        }

    @staticmethod
    def _plan_sparks(sparks):
        """
        Like _plan_skills without the editors: every spark is the game's, so a
        missing row is created and a changed one overwritten. `skipped` counts
        the kinds models/spark.py has no name for yet.
        """
        by_id = Spark.objects.in_bulk()
        kinds = set(SparkKind.values)
        changed, created, unchanged, skipped = [], [], 0, 0
        for spark in sparks:
            if spark["kind"] not in kinds:
                skipped += 1
                continue
            values = {"kind": spark["kind"], "stars": spark["stars"], "name": spark["name"]}
            row = by_id.get(spark["id"])
            if row is None:
                created.append(Spark(id=spark["id"], **values))
                continue
            diff = {column: value for column, value in values.items() if getattr(row, column) != value}
            if diff:
                changed.append((row, diff))
            else:
                unchanged += 1
        return {
            "label": "spark", "spark": True, "changed": changed, "created": created,
            "unchanged": unchanged, "skipped_kinds": skipped, "missing": [], "mismatched": [],
        }

    @staticmethod
    def _write_sparks(plan):
        for row, diff in plan["changed"]:
            for column, value in diff.items():
                setattr(row, column, value)
            row.save(update_fields=list(diff))
        Spark.objects.bulk_create(plan["created"])
        return len(plan["changed"]) + len(plan["created"])

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
    def _jp_notice(jp_links, evolutions):
        """The report's "JP data" section: what the JP pass skipped or trusted."""
        return {
            "label": "JP data", "notice": True, "changed": [],
            "lines": jp_links["lines"] + evolutions["unruled"] + [
                f"{jp_links['unchecked']} JP outfit(s) or card(s) with no English name to "
                "check against (a character global doesn't have, or a group card): links trusted",
            ],
        }

    @staticmethod
    def _plan_evolution_links(evolutions, known_skills):
        """
        `evolves_from` links and unlock conditions, written after this run's
        skill rows exist (a base is often created in the same run).

        A base from the game (scenario, succession) follows it every run. A
        base from the rank rule is written only while the link is empty, so
        an editor's correction in the Skill admin sticks. Conditions are game
        data: an evolved skill's set is replaced whenever it differs.
        """
        existing_ids = set(Skill.objects.values_list("game_id", flat=True))
        evolved_ids = set(evolutions["bases"]) | set(evolutions["conditions"])
        current = dict(
            Skill.objects.filter(game_id__in=evolved_ids)
            .values_list("game_id", "evolves_from__game_id")
        )
        plan = {"label": "evolution", "evolution": True, "changed": [], "conditions": [],
                "unchanged": 0, "missing": [], "mismatched": []}
        for evolved, base in sorted(evolutions["bases"].items()):
            if base not in known_skills and base not in existing_ids:
                plan["missing"].append(f"evolved skill {evolved}: its base {base} is in no snapshot")
                continue
            now = current.get(evolved)
            if evolved in evolutions["ruled"] and now is not None:
                continue  # the rule's pick, or an editor's correction of it
            if now != base:
                plan["changed"].append((evolved, base))

        fields = ("game_id", "num", "sub_num", "timing_type", "count_type",
                  "text", "translated_text", "jp_text")
        have = {}
        for row in SkillEvolutionCondition.objects.filter(
            skill__game_id__in=evolved_ids,
        ).values("skill__game_id", *fields).order_by("game_id"):
            have.setdefault(row.pop("skill__game_id"), []).append(row)
        for evolved, conditions in sorted(evolutions["conditions"].items()):
            wanted = sorted(conditions, key=lambda condition: condition["game_id"])
            if have.get(evolved, []) != wanted:
                plan["conditions"].append((evolved, wanted))
            else:
                plan["unchanged"] += 1
        return plan

    @staticmethod
    def _write_evolutions(plan):
        """Links in one bulk update, conditions replaced per skill in bulk."""
        ids = {evolved for evolved, _ in plan["changed"]} | {base for _, base in plan["changed"]}
        ids |= {evolved for evolved, _ in plan["conditions"]}
        pks = dict(Skill.objects.filter(game_id__in=ids).values_list("game_id", "pk"))
        linked = list(Skill.objects.filter(game_id__in=[evolved for evolved, _ in plan["changed"]]))
        bases = dict(plan["changed"])
        for skill in linked:
            skill.evolves_from_id = pks[bases[skill.game_id]]
        Skill.objects.bulk_update(linked, ["evolves_from"])

        replaced = [pks[evolved] for evolved, _ in plan["conditions"]]
        SkillEvolutionCondition.objects.filter(skill_id__in=replaced).delete()
        SkillEvolutionCondition.objects.bulk_create([
            SkillEvolutionCondition(skill_id=pks[evolved], **condition)
            for evolved, conditions in plan["conditions"]
            for condition in conditions
        ])
        return len(linked) + len(replaced)

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
                if plan.get("notice"):
                    continue
                if plan.get("junction"):
                    written += Command._write_junction(plan)
                    continue
                if plan.get("evolution"):
                    written += Command._write_evolutions(plan)
                    continue
                if plan.get("spark"):
                    written += Command._write_sparks(plan)
                    continue
                for row, diff in plan["changed"]:
                    for column, value in diff.items():
                        setattr(row, column, value)
                    row.save(update_fields=list(diff))
                    written += 1
                new_rows = plan.get("created", [])
                Skill.objects.bulk_create(new_rows)
                written += len(new_rows)
                # Old names, now that every skill row exists. A name already
                # kept is skipped by the unique constraint.
                aliases = plan.get("aliases", [])
                if aliases:
                    pks = dict(Skill.objects.filter(
                        game_id__in={game_id for game_id, _ in aliases},
                    ).values_list("game_id", "pk"))
                    SkillAlias.objects.bulk_create(
                        [SkillAlias(skill_id=pks[game_id], name=name) for game_id, name in aliases],
                        ignore_conflicts=True,
                    )
        # Per-row saves already fire the invalidation signal, but the invariant
        # is that a bulk writer drops the cache itself, so this does too.
        public_payload_cache.invalidate()
        pvp_catalogue_cache.invalidate()
        skills_cache.invalidate()
        return written

    def _report_skill_notes(self, plan):
        """The skill plan's JP-only count and new aliases, and the spark plan's
        unknown kinds, when there are any."""
        if plan.get("skipped_kinds"):
            self.stdout.write(self.style.WARNING(
                f"    {plan['skipped_kinds']} of a kind models/spark.py doesn't name yet, skipped"
            ))
        if plan.get("jp_only"):
            self.stdout.write(f"    {plan['jp_only']} are JP-only skills, hidden from players")
        if plan.get("aliases"):
            self.stdout.write(f"    {len(plan['aliases'])} replaced name(s) kept as aliases")

    def _report_jp_plan(self, plan, verb, dry_run):
        """Report the JP pass's two plan kinds; False for any other plan."""
        if plan.get("notice"):
            self.stdout.write(f"\n{plan['label']}:")
            for line in plan["lines"]:
                self.stdout.write(self.style.WARNING(f"    {line}"))
            return True
        if plan.get("evolution"):
            self.stdout.write(
                f"\nevolutions: {verb.lower()} {len(plan['changed'])} base link(s), "
                f"{'would replace' if dry_run else 'replacing'} the conditions of "
                f"{len(plan['conditions'])}, already current {plan['unchanged']}"
            )
            for line in plan["missing"]:
                self.stdout.write(self.style.WARNING(f"    {line}"))
            return True
        return False

    def _report(self, plans, dry_run):
        verb = "Would update" if dry_run else "Updating"
        for plan in plans:
            label = plan["label"]
            created = plan.get("created")
            if self._report_jp_plan(plan, verb, dry_run):
                continue
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
                if plan.get("ahead"):
                    self.stdout.write(
                        f"    {plan['ahead']} of them are text the client shipped ahead of "
                        f"release: the JP client's structure when the JP snapshot has it, "
                        f"else rarity and tier are guesses until global defines them"
                    )
                self._report_skill_notes(plan)
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
