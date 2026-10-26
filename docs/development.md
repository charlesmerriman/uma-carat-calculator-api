# Development

Local setup, content, commands, deployment and configuration. The [README](../README.md) has
the overview.

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt    # runtime dependencies plus lint tooling
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser       # a staff account for /admin
# The first /admin login sets up an authenticator app (admin.md, "Signing
# in"); ADMIN_REQUIRE_OTP=False in .env keeps the local admin password-only.
python manage.py runserver
```

[`.env.example`](../.env.example) is annotated and runs as-is locally: Django falls back to SQLite
and an insecure development secret key. Sign-in additionally needs the OAuth client credentials
for the provider you test with, and `FRONTEND_URL` must produce the exact redirect URI
registered in that provider's console.

## Local content

A fresh database starts empty. Nothing ever loads content INTO production: it is authored
in the admin, and `loaddata` upserts by primary key, so a seeding script is one stray run
(or one wrong `DATABASE_URL`) away from overwriting live edits with a stale snapshot. The
JSON in `calculatorapi/fixtures/` is a one-way export that nothing loads.

Content does travel the other way, from production down to a local SQLite database:

```bash
python manage.py migrate
python manage.py pull_prod_content      # needs doctl signed in, and DO_SPACES_* in .env
python manage.py createsuperuser        # accounts are never copied, so make your own
```

`pull_prod_content` dumps the content tables in the production container (a read), carries
the file out through the media Space as a private object it deletes afterwards, and replaces
the local content tables with it. Run it again whenever local content has fallen behind.
It copies only the tables listed in `calculatorapi/content_snapshot.py`: no accounts, plans,
supporters, credentials, traffic counts or analytics snapshots. Both it and
`load_content_snapshot` refuse any database that is not SQLite.

A refresh keeps local accounts and their rows. If one of those rows points at content that
production has since deleted, the load stops and changes nothing; delete the row it names,
or start over by moving `db.sqlite3` aside and running the three commands above.

Local `.env` points at production's media Space, which is why pulled rows show their images.
It also means a file uploaded through the local admin lands in the production bucket.

For a quick look at real content with no local database at all, run the frontend against
the production API with `npm run dev:live`
([details](https://github.com/charlesmerriman/uma-carat-calculator-web/blob/HEAD/docs/development.md#choosing-a-backend)).
Sign-in there is real: signed in, a save writes to the live database under your own account.

## Tests and linting

```bash
python manage.py test --parallel                                     # the whole suite
python manage.py test calculatorapi.tests.test_ledger                # one module
python manage.py test calculatorapi.tests.test_ledger.LedgerTests    # one class
pylint calculatorapi/ calculatorproject/
```

CI runs both on every push to `develop` and `master`, the tests in four shards. The tests live in `calculatorapi/tests/`, one module per feature
area. Every test starts with an empty cache (`CalculatorTestCase` in `tests/base.py`), so no
result depends on which tests ran before it.

## Management commands

| Command | What it does |
|---|---|
| `sync_changelog` | Writes `calculatorapi/data/changelog.yaml` into the changelog table. Runs on every deploy; use `--dry-run --strict` locally to validate the file |
| `create_content_editor_group` | Creates or refreshes the "Content editors" permission group. Runs on every deploy after `migrate`; locally run it by hand, after `migrate` |
| `pull_prod_content` | Local only. Replaces the local content tables with a fresh snapshot of production's; see "Local content" |
| `load_content_snapshot` | Local only. Loads a snapshot file kept with `pull_prod_content --keep` |
| `seed_anniversary_campaigns` | Creates or refreshes the anniversary campaigns from the source sheet. Idempotent |
| `sync_patreon_supporters` | Syncs supporters from the Patreon API; the daily Action reaches the same reconcile over HTTP |
| `set_patreon_tier_order` | Sets supporter tier order from `NAME=ORDER` pairs |
| `prune_visitor_hashes` | Deletes visitor de-duplication hashes older than the retention window (90 days, never below 45). Runs on every deploy |
| `snapshot_analytics` | Keeps today's copy of the admin analytics report unless one exists (the admin also keeps one on its first rebuild each day). Runs on every deploy; never fails it |
| `purge_user_pii` | Blanks email, name and password on every non-staff account. **Irreversible**, so run it with `--dry-run` first |
| `import_game_data` | Fills the game-data columns on Uma and SupportCard and creates or updates every Skill from `scripts/data/master_snapshot/`, then the JP-only skills, evolutions and JP outfits' links from `jp_snapshot/` and UmaTL's English from `umatl/` (global always wins; `--global-only` skips both). `--dry-run` first; `--gametora skills.json` adds the detailed descriptions |
| `import_game_data` also reads `support_events.json`, written by `scripts/fetch_support_events.py` from gametora (the game has no clean table for support card event skills) |
| `link_skill_images` | Points every Skill without an image at `skills/<icon_id>.png` in the Space, after `scripts/fetch_skill_icons.py --upload` put the files there |
| `link_borderless_umas` | Points every Uma without a borderless image at the file in `umas_borderless/` whose name starts with its `game_id`, after `scripts/upload_borderless_umas.py <export.zip> --upload` put the files there. Reports files with no uma and umas with no file; `--dry-run` first |
| `merge_duplicate_support_cards` | The same merge for support cards (it subclasses the uma command): folds "Daiichi Ruby (Rerun)", which has no `game_id` and so can never be reached by the import, into the card with the same image id. `--dry-run` first |
| `merge_duplicate_umas` | Folds a `(Rerun)` copy of an uma into the original by image id, re-pointing every row that referenced it. Run before any migration that makes uma ids unique; `--dry-run` first |

Four more are one-off data repairs, kept for the record: `classify_banner_categories`,
`backfill_race_prep_supports`, `fix_support_card_variants` and `repair_launch_banner`.

## Deployment

App Platform builds on every push to `master`: `pip install -r requirements.txt` and
`collectstatic`, then `migrate`, `sync_changelog` and gunicorn on start. `develop` is where
work integrates; `master` only moves by merging it.

[`.do/app.yaml`](../.do/app.yaml) is a reference copy of the app spec with its secrets blanked.
Change the live spec with `doctl apps spec get` and `doctl apps update` rather than by applying
this file, which would overwrite every secret with an empty string.

## Configuration

[`.env.example`](../.env.example) documents every variable. The ones that matter most:

| Variable | |
|---|---|
| `DJANGO_SECRET_KEY` | Required in production |
| `DATABASE_URL` | PostgreSQL connection string; SQLite when unset |
| `FRONTEND_URL` | The OAuth redirect URI is derived from it and must match each provider console exactly |
| `GOOGLE_OAUTH_*`, `DISCORD_OAUTH_*`, `PATREON_OAUTH_*` | The sign-in apps |
| `PATREON_CLIENT_*`, `PATREON_SYNC_SECRET` | The creator app and the shared secret behind the supporters sync |
| `DO_SPACES_*` | Media storage |
| `API_PUBLIC_PREFIX` | The path the API is served under (`/api` in production), so the docs page's "Try it out" calls the API |
