# Uma Carat Calculator (API)

**Live at [umacaratcalculator.com](https://umacaratcalculator.com).** Tens of thousands of
visitors since the September 2026 launch, and dozens of Patreon supporters.

[![CI](https://github.com/charlesmerriman/uma-carat-calculator-api/actions/workflows/ci.yml/badge.svg)](https://github.com/charlesmerriman/uma-carat-calculator-api/actions/workflows/ci.yml)

The Django REST API behind a gacha resource planner for Uma Musume Pretty Derby. The React
frontend is [uma-carat-calculator-web](https://github.com/charlesmerriman/uma-carat-calculator-web),
and browsable API docs are live at [umacaratcalculator.com/api/docs](https://umacaratcalculator.com/api/docs).

It serves everything the planner needs to know about the game (banners and their dates, events
and their rewards, rank income tables, campaigns) as one aggregated payload, stores each
signed-in player's plan, and handles OAuth sign-in. Its admin is the site's CMS: every banner,
event and reward on the site is maintained there by hand.

## Architecture

One site, two repositories, both deployed by DigitalOcean App Platform on every push to
`master`:

- **Web** ([uma-carat-calculator-web](https://github.com/charlesmerriman/uma-carat-calculator-web)): a React 19 + TypeScript single-page app, built by Vite and served as a static site.
- **API** (this repo): Django 6 and Django REST Framework, served from the same domain under `/api`.
- **Data**: managed PostgreSQL, with banner and card images on DigitalOcean Spaces behind its CDN.
- **Accounts**: sign-in through Google, Discord or Patreon OAuth, exchanged for a DRF token. Player accounts store no email, name or password.
- **Maths**: every projection runs in the browser. The API assembles and dates the reference data, including a flat income ledger, but never computes a forecast.
- **Content**: banners, events and rewards are maintained by hand in the Django admin, which doubles as the CMS.

## About this project

- In continuous development since December 2025, live in open beta since September 2026, and actively maintained.
- The resource model follows [Henry's resource spreadsheet](https://docs.google.com/spreadsheets/d/100t3hnYl5Qm2UR8RtPlH-8Xd9KQbBlxEdXUOIR4d394/), the community reference built by Daptrius that this site grew out of. The application is my own work: the projection engine, authentication, admin and deployment.

## Tech stack

| Tool | Used for |
|---|---|
| Django 6 | Web framework and ORM |
| Django REST Framework | API views, serializers, token authentication |
| drf-spectacular | The generated OpenAPI schema, and Swagger UI over it at `/api/docs` |
| django-unfold | The admin theme behind the content CMS |
| PostgreSQL (prod), SQLite (dev) | Selected by `DATABASE_URL` through `dj-database-url` |
| django-storages + boto3 | Image uploads to DigitalOcean Spaces |
| whitenoise | Static files without a separate web server |
| gunicorn | WSGI server |
| requests | Calls to the OAuth providers and the Patreon API |

## How it works

### One payload

`GET /calculator-data` returns what the planner needs in a single response: every reference
table, the dated income ledger, banner timelines with resolved dates, campaigns and, for a
signed-in player, their stats and plan. Guests get the same public payload with the user
fields empty. The public half is cached in-process and invalidated by model signals whenever
content changes.

`PATCH /calculator-data` reconciles a player's collections in one request: rows with an `id`
are updated, rows without one are created, and rows missing from the payload are deleted.

### Predicted dates

The global server follows the Japanese release schedule but announces dates late, so banner
and event dates are predicted from the JP calendar, with admin-set offsets for when global
slips. Every predicted date is flagged as one. [docs/data-model.md](docs/data-model.md) has
the prediction model.

### Accounts and privacy

Players sign in through Google, Discord or Patreon with the OAuth2 authorization-code flow and
get back a DRF token. Scopes are limited to identity, and a player account holds nothing but
the provider's opaque id and a generated handle. The redirect URI comes from a server-side
allowlist and is sealed into a signed `state`, so the sign-in endpoint can't be used as an
open redirector. Linking a second provider is a separate, authenticated flow that can never
create an account. Password login exists for staff only, and it answers a wrong password
exactly as it answers a non-staff account.

### Patreon supporters

A scheduled GitHub Action calls `POST /patreon/sync` once a day, which pulls members from the
Patreon API through the same reconcile the admin's CSV import uses. Supporter benefits are
derived on every request from the linked supporter record rather than stored on the account,
so a lapsed pledge can't leave a stale flag behind.

### Content

The admin (`/admin/`) is set up for non-technical editors, with friendly names, inline
editing, autocomplete pickers, an image library backed by Spaces, and a "Content editors"
permission group scoped to game content. Staff also get `/admin/analytics/`, which reports
aggregates only, never per-user rows. The public changelog is authored in
`calculatorapi/data/changelog.yaml` and written to the database on every deploy.

### Data model

| Model | Holds |
|---|---|
| `Uma`, `SupportCard` | Characters and support cards. Release dates are derived from the banners that featured them, never stored |
| `BannerTimeline` | A banner window: its JP and global dates, category and schedule offset |
| `BannerUma`, `BannerSupport`, `BannerStepUp` | The banners in a window and the cards they feature |
| `UserPlannedBanner` | One row of a player's plan; a check constraint enforces exactly one banner target |
| `UserStepUpSelection` | A player's ten card picks on a step-up banner |
| `AnniversaryEvent` | A campaign, its banner parts and its purchasable packs |
| `UserPlannedPurchase` | A pack a player plans to buy |
| `GameEvent`, `ChampionsMeeting`, `LeagueOfHeroes`, `Scenario` | Events, race events and training scenarios |
| `ClubRank`, `TeamTrialsRank`, `ChampionsMeetingRank`, `LeagueOfHeroesRank` | Income per rank tier |
| `CalculationConstants` | Income constants, editable in the admin instead of baked into the frontend bundle |
| `CustomUser`, `SocialAccount` | Accounts and the provider identities linked to them |
| `PatreonTier`, `PatreonSupporter` | Supporter tiers and the synced supporter list |
| `ChangelogEntry`, `ChangelogChange` | Public patch notes |
| `DailyVisit`, `MonthlyVisit`, `Feedback` | Anonymous traffic counts and feedback |

### Endpoints

| Route | |
|---|---|
| `GET /calculator-data` | The aggregated payload. Public; the user fields fill in when a token is sent |
| `PATCH /calculator-data` | Save a plan (token required) |
| `GET /auth/<provider>/start`, `POST /auth/social` | OAuth sign-in with `google`, `discord` or `patreon` |
| `GET /account` | The signed-in account and its supporter benefits |
| `GET /account/link/<provider>/start`, `POST /account/link/<provider>/complete`, `DELETE /account/link/<provider>` | Link or unlink a provider on a signed-in account |
| `POST /login`, `POST /logout` | Staff password login, and logout |
| `POST /visit`, `POST /feedback` | Anonymous visit beacon and feedback form, both throttled |
| `POST /patreon/sync` | Supporter sync, authorised by a shared-secret header |
| `GET /teamtrialranks`, `/clubranks`, `/championsmeetingranks`, `/leagueofheroesranks`, `/leagueofheroes`, `/events`, `/changelog`, `/supporters` | Read-only reference data |
| `GET /schema`, `GET /docs` | The generated OpenAPI schema, and Swagger UI over it ([live](https://umacaratcalculator.com/api/docs)) |
| `/admin/`, `/admin/analytics/`, `/admin/image-library/` | Staff only |

Request and response shapes are in [docs/api-reference.md](docs/api-reference.md).

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt    # runtime dependencies plus lint tooling
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser       # a staff account for /admin
python manage.py runserver
```

[`.env.example`](.env.example) is annotated and runs as-is locally: Django falls back to SQLite
and an insecure development secret key. Sign-in additionally needs the OAuth client credentials
for the provider you test with, and `FRONTEND_URL` must produce the exact redirect URI
registered in that provider's console.

### A fresh database starts empty

There is no seeding step, on purpose. Production content is authored in the admin, and
`loaddata` upserts by primary key, so a seeding script is one stray run (or one wrong
`DATABASE_URL`) away from overwriting live edits with a stale snapshot. The JSON in
`calculatorapi/fixtures/` is a one-way export that nothing loads.

To work against real content, run the frontend against the production API with
`npm run dev:live`
([details](https://github.com/charlesmerriman/uma-carat-calculator-web#choosing-a-backend)).
Sign-in there is real: signed in, a save writes to the live database under your own account.
Use your local `/admin` when you specifically need local rows, such as when exercising a
migration.

## Tests and linting

```bash
python manage.py test                                                # the whole suite
python manage.py test calculatorapi.tests.test_ledger                # one module
python manage.py test calculatorapi.tests.test_ledger.LedgerTests    # one class
pylint calculatorapi/ calculatorproject/
```

CI runs both on every push. The tests live in `calculatorapi/tests/`, one module per feature
area. Every test starts with an empty cache (`CalculatorTestCase` in `tests/base.py`), so no
result depends on which tests ran before it.

## Management commands

| Command | What it does |
|---|---|
| `sync_changelog` | Writes `calculatorapi/data/changelog.yaml` into the changelog table. Runs on every deploy; use `--dry-run --strict` locally to validate the file |
| `create_content_editor_group` | Creates or refreshes the "Content editors" permission group. Runs on every deploy after `migrate`; locally run it by hand, after `migrate` |
| `seed_anniversary_campaigns` | Creates or refreshes the anniversary campaigns from the source sheet. Idempotent |
| `sync_patreon_supporters` | Syncs supporters from the Patreon API; the daily Action reaches the same reconcile over HTTP |
| `set_patreon_tier_order` | Sets supporter tier order from `NAME=ORDER` pairs |
| `prune_visitor_hashes` | Deletes visitor de-duplication hashes older than the retention window |
| `purge_user_pii` | Blanks email, name and password on every non-staff account. **Irreversible**, so run it with `--dry-run` first |
| `import_game_data` | Fills the game-data columns on Uma and SupportCard and creates or updates every Skill from `scripts/data/master_snapshot/`. `--dry-run` first; `--gametora skills.json` adds the detailed descriptions |
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

[`.do/app.yaml`](.do/app.yaml) is a reference copy of the app spec with its secrets blanked.
Change the live spec with `doctl apps spec get` and `doctl apps update` rather than by applying
this file, which would overwrite every secret with an empty string.

## Configuration

[`.env.example`](.env.example) documents every variable. The ones that matter most:

| Variable | |
|---|---|
| `DJANGO_SECRET_KEY` | Required in production |
| `DATABASE_URL` | PostgreSQL connection string; SQLite when unset |
| `FRONTEND_URL` | The OAuth redirect URI is derived from it and must match each provider console exactly |
| `GOOGLE_OAUTH_*`, `DISCORD_OAUTH_*`, `PATREON_OAUTH_*` | The sign-in apps |
| `PATREON_CLIENT_*`, `PATREON_SYNC_SECRET` | The creator app and the shared secret behind the supporters sync |
| `DO_SPACES_*` | Media storage |
| `API_PUBLIC_PREFIX` | The path the API is served under (`/api` in production), so the docs page's "Try it out" calls the API |

## Documentation

- [data-model.md](docs/data-model.md): models, constraints, date prediction and the changelog pipeline
- [api-reference.md](docs/api-reference.md): request and response shapes for every endpoint
- [auth-and-privacy.md](docs/auth-and-privacy.md): the OAuth flows, account linking, and what is and isn't stored
- [income-calculation.md](docs/income-calculation.md): income amounts and schedules, campaign purchases and step-up costs
- [admin.md](docs/admin.md): the admin setup, the image picker and the Patreon import
- [analytics.md](docs/analytics.md): reading the staff analytics dashboard
- [content-editing.md](docs/content-editing.md): the guide for content editors
- [banner-timeline-content-flowchart.svg](docs/banner-timeline-content-flowchart.svg): the banner-timeline content workflow as a flowchart

How the frontend turns this data into a forecast is covered in the
[web repo's docs](https://github.com/charlesmerriman/uma-carat-calculator-web/tree/HEAD/docs).

## License

[MIT](LICENSE)
