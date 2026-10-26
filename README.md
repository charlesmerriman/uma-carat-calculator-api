# Uma Carat Calculator (API)

**Live at [umacaratcalculator.com](https://umacaratcalculator.com)**, with browsable API docs at
[umacaratcalculator.com/api/docs](https://umacaratcalculator.com/api/docs). A planning tool for
Uma Musume Pretty Derby players with over **100,000 visitors a month**. Since its September 2026 launch: nearly 400,000
visits, 16,600 accounts holding 18,500 saved plans, and 86 Patreon supporters.

[![CI](https://github.com/charlesmerriman/uma-carat-calculator-api/actions/workflows/ci.yml/badge.svg)](https://github.com/charlesmerriman/uma-carat-calculator-api/actions/workflows/ci.yml)

This is the Django REST API. The React frontend is
[uma-carat-calculator-web](https://github.com/charlesmerriman/uma-carat-calculator-web), and its
README describes what the site does for players.

## What it does

- **Serves the game's calendar and economy**: banners and their dates, events and their rewards,
  rank income tables and campaigns, assembled into one cached payload with a dated income
  ledger the browser forecasts from.
- **Predicts dates.** The global server follows the Japanese release schedule but announces
  late, so banner and event dates are predicted from the JP calendar, with admin-set offsets for
  when global slips. Every predicted date is flagged as one.
- **Stores players' plans**: about 18,500 of them holding 356,000 planned banners, several per account, each shareable by a public link with a
  server-drawn preview image for Discord and X.
- **Runs the PvP side**: guides with revisions and an editor, a community gallery of teams with
  votes, comments, reports and moderation.
- **Is the CMS.** Every banner, event, reward and FAQ answer is entered in the Django admin by a
  small team of non-technical editors.

## Engineering highlights

- **Privacy by design.** Sign-in is OAuth with identity-only scopes, and an account keeps
  nothing from the provider's profile: no email, name or picture. The redirect URI comes from a
  server-side allowlist sealed into a signed `state`, so the endpoint can't be used as an open
  redirector, and linking a second provider is a separate flow that can never create an account.
  [docs/auth-and-privacy.md](docs/auth-and-privacy.md) has the details.
- **One payload, one save.** `GET /calculator-data` returns everything the planner needs in a
  single response; its public half is cached in-process and invalidated by model signals.
  `PATCH /calculator-data` reconciles a plan's rows in one request: rows with an `id` update,
  rows without one are created, rows left out are deleted.
- **Derived, not stored.** Patreon supporter benefits are worked out on every request from the
  synced supporter list (a daily GitHub Action calls the sync), so a lapsed pledge can't leave a
  stale flag behind.
- **A locked-down admin.** Staff sign in with a password and an authenticator code
  (django-otp), with lockout after repeated failures (django-axes). Nothing else on the API
  takes a password. The staff analytics page reports aggregates only, never per-user rows.
- **Content flows one way.** Production content is authored in the admin and never overwritten
  by a script: there is no seed path into production, and the command that copies content
  down to a developer's machine refuses any database that isn't local SQLite.
- **Moderation from Discord.** A report on gallery content becomes a card in the team's Discord,
  and a small bot worker ([`bot/`](bot/)) acts on the moderators' button clicks. Every message
  the site sends to Discord goes through one keyed sender, so nothing is ever posted twice.
- **Tested.** About 1,570 tests in 55 modules, run in four parallel shards in CI on every push
  to `develop` and `master`, plus pylint.

## Architecture

One site, two repositories, both deployed by DigitalOcean App Platform on every push to
`master`:

- **Web** ([uma-carat-calculator-web](https://github.com/charlesmerriman/uma-carat-calculator-web)): React 19 + TypeScript, built by Vite, prerendered and served as a static site.
- **API** (this repo): Django 6 and Django REST Framework, on the same domain under `/api`.
- **Data**: managed PostgreSQL, with images on DigitalOcean Spaces behind its CDN.
- **Maths**: every projection runs in the browser. The API assembles and dates the reference data but never computes a forecast.

## Tech stack

| Tool | Used for |
|---|---|
| Django 6 | Web framework and ORM |
| Django REST Framework | API views, serializers, token authentication |
| drf-spectacular | The OpenAPI schema and the Swagger UI at `/api/docs` |
| django-unfold | The admin theme behind the CMS |
| django-otp, django-axes | The admin's second factor and its lockout |
| PostgreSQL (prod), SQLite (dev) | Selected by `DATABASE_URL` |
| django-storages + boto3 | Image uploads to DigitalOcean Spaces |
| Pillow | Link-preview images for shared plans and gallery posts |
| whitenoise, gunicorn | Static files and the WSGI server |
| requests | Calls to the OAuth providers and the Patreon API |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # annotated; runs as-is locally on SQLite
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
python manage.py test --parallel  # the suite
```

A fresh database starts empty. How to fill it from production, every management command,
the admin sign-in locally, deployment and configuration are in
[docs/development.md](docs/development.md).

## Documentation

- [development.md](docs/development.md): local setup and content, management commands, deployment and configuration
- [data-model.md](docs/data-model.md): models, constraints, date prediction and the changelog pipeline
- [api-reference.md](docs/api-reference.md): request and response shapes for every endpoint (also [live](https://umacaratcalculator.com/api/docs))
- [auth-and-privacy.md](docs/auth-and-privacy.md): the OAuth flows, account linking, and what is and isn't stored
- [income-calculation.md](docs/income-calculation.md): income amounts and schedules, campaign purchases and step-up costs
- [admin.md](docs/admin.md): the admin setup, the image picker and the Patreon import
- [analytics.md](docs/analytics.md): reading the staff analytics dashboard
- [content-editing.md](docs/content-editing.md): the guide for content editors

How the frontend turns this data into a forecast is covered in the
[web repo's docs](https://github.com/charlesmerriman/uma-carat-calculator-web/tree/HEAD/docs).

## About this project

In continuous development since December 2025 and live since September 2026. The resource
model follows [Henry's resource spreadsheet](https://docs.google.com/spreadsheets/d/100t3hnYl5Qm2UR8RtPlH-8Xd9KQbBlxEdXUOIR4d394/),
the community reference built by Daptrius that this site grew out of. The application is my
own work: the projection engine, authentication, admin and deployment.

Visitor figures come from the site's own privacy-first analytics (no third-party trackers):
bots are filtered out and each browser session counts once, as of October 2026.

## License

[MIT](LICENSE)
