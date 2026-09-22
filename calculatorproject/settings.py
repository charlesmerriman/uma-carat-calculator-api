from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv
# reverse_lazy (NOT reverse) is required in the UNFOLD sidebar: settings.py is
# imported before the URLconf is loaded, so eager reverse() would raise. The
# lazy variant resolves the URL only when the sidebar is rendered.
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "change-me-in-production")

DEBUG = os.getenv("DEBUG", "False") == "True"

ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    # django-unfold must come BEFORE django.contrib.admin so its templates
    # override the stock admin's (Django resolves app templates in order).
    "unfold",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "drf_spectacular",
    "corsheaders",
    "storages",
    "calculatorapi",
]

# django-unfold (admin theme) configuration. Branding here mirrors the
# admin.site.* values in calculatorapi/admin.py, which unfold's templates
# fall back to; the sidebar keeps the default auto-generated app list.
# Per-item sidebar visibility. Unlike unfold's auto-generated app list, a
# hardcoded SIDEBAR is NOT permission-aware by default — every staff user would
# see every link. This gate hides an item from anyone lacking the given model
# permission, so the "Content editors" group never sees the user-management
# section. When all of a group's items are hidden, unfold hides the group too.
def _requires_perm(codename):
    return lambda request: request.user.has_perm(codename)


UNFOLD = {
    "SITE_TITLE": "Uma Calculator Admin",
    "SITE_HEADER": "Uma Calculator",
    "SITE_SUBHEADER": "Content management",
    # Material Symbols icon shown in the sidebar header. Using a built-in icon
    # (rather than a logo image) avoids shipping a brand asset we don't have.
    "SITE_SYMBOL": "calculate",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    # A small "Production"/"Local" badge in the header so the client can never
    # confuse the live admin with a local one. Callback lives in admin_dashboard.
    "ENVIRONMENT": "calculatorapi.admin_dashboard.environment_callback",
    # Populates the landing page with headline stat cards (see custom_index.html).
    "DASHBOARD_CALLBACK": "calculatorapi.admin_dashboard.dashboard_callback",
    # Header dropdown of quick links (view the live site, jump to analytics).
    "SITE_DROPDOWN": [
        {
            "icon": "open_in_new",
            "title": _("View live site"),
            "link": "/",
        },
        {
            "icon": "monitoring",
            "title": _("Analytics dashboard"),
            "link": reverse_lazy("admin-analytics"),
        },
    ],
    # Brand palette. Unfold injects each shade as `--color-primary-<n>` and uses
    # it directly (buttons, links, active nav), so any valid CSS color works.
    # This gold ramp is anchored on the frontend's brand accent (#E6D28A at 300);
    # 600–800 are darkened enough to keep white button text readable.
    "COLORS": {
        "primary": {
            "50": "#fbf8ef",
            "100": "#f6eed2",
            "200": "#ecdea5",
            "300": "#e6d28a",  # frontend --color-brand
            "400": "#d6bc5c",
            "500": "#c29e34",
            "600": "#a07f20",
            "700": "#7d621c",
            "800": "#644f1b",
            "900": "#54421a",
            "950": "#30250d",
        },
    },
    # Curated left-hand navigation. Replaces unfold's auto-generated app list so
    # the ~17 models are grouped into task-based sections with icons. NOTE: a new
    # model must be added here or it won't appear in the sidebar (it's still
    # reachable by URL). Icons are Material Symbols names.
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": _("Overview"),
                "items": [
                    {
                        "title": _("Dashboard"),
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                    {
                        "title": _("Analytics"),
                        "icon": "monitoring",
                        "link": reverse_lazy("admin-analytics"),
                    },
                ],
            },
            {
                "title": _("Banners"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Timelines"),
                        "icon": "calendar_month",
                        "link": reverse_lazy("admin:calculatorapi_bannertimeline_changelist"),
                    },
                    {
                        "title": _("Uma banners"),
                        "icon": "sprint",
                        "link": reverse_lazy("admin:calculatorapi_banneruma_changelist"),
                    },
                    {
                        "title": _("Support banners"),
                        "icon": "style",
                        "link": reverse_lazy("admin:calculatorapi_bannersupport_changelist"),
                    },
                    {
                        "title": _("Step-up banners"),
                        "icon": "stairs",
                        "link": reverse_lazy("admin:calculatorapi_bannerstepup_changelist"),
                    },
                ],
            },
            {
                "title": _("Events & competitions"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Game events"),
                        "icon": "event",
                        "link": reverse_lazy("admin:calculatorapi_gameevent_changelist"),
                    },
                    {
                        "title": _("Champions Meetings"),
                        "icon": "emoji_events",
                        "link": reverse_lazy("admin:calculatorapi_championsmeeting_changelist"),
                    },
                    {
                        "title": _("League of Heroes"),
                        "icon": "military_tech",
                        "link": reverse_lazy("admin:calculatorapi_leagueofheroes_changelist"),
                    },
                    {
                        "title": _("Anniversary campaigns"),
                        "icon": "redeem",
                        "link": reverse_lazy("admin:calculatorapi_anniversaryevent_changelist"),
                    },
                    {
                        "title": _("Scenarios"),
                        "icon": "stadia_controller",
                        "link": reverse_lazy("admin:calculatorapi_scenario_changelist"),
                    },
                ],
            },
            {
                "title": _("Characters"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Umas"),
                        "icon": "directions_run",
                        "link": reverse_lazy("admin:calculatorapi_uma_changelist"),
                    },
                    {
                        "title": _("Support cards"),
                        "icon": "cards",
                        "link": reverse_lazy("admin:calculatorapi_supportcard_changelist"),
                    },
                    {
                        "title": _("Skills"),
                        "icon": "bolt",
                        "link": reverse_lazy("admin:calculatorapi_skill_changelist"),
                    },
                ],
            },
            {
                "title": _("Income tables"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Club ranks"),
                        "icon": "groups",
                        "link": reverse_lazy("admin:calculatorapi_clubrank_changelist"),
                    },
                    {
                        "title": _("Team Trials ranks"),
                        "icon": "diversity_3",
                        "link": reverse_lazy("admin:calculatorapi_teamtrialsrank_changelist"),
                    },
                    {
                        "title": _("Champions Meeting ranks"),
                        "icon": "emoji_events",
                        "link": reverse_lazy("admin:calculatorapi_championsmeetingrank_changelist"),
                    },
                    {
                        "title": _("League of Heroes ranks"),
                        "icon": "military_tech",
                        "link": reverse_lazy("admin:calculatorapi_leagueofheroesrank_changelist"),
                    },
                ],
            },
            {
                "title": _("Configuration"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Calculation constants"),
                        "icon": "tune",
                        "link": reverse_lazy(
                            "admin:calculatorapi_calculationconstants_changelist"
                        ),
                        # Deliberately NOT a content-editor page: these numbers
                        # move every user's projection, and some of them move
                        # banner dates. CalculationConstants is left out of
                        # CONTENT_MODELS, so the group has no permission and
                        # unfold hides the whole section for them.
                        "permission": _requires_perm(
                            "calculatorapi.change_calculationconstants"
                        ),
                    },
                ],
            },
            {
                "title": _("Site content"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Pages"),
                        "icon": "article",
                        "link": reverse_lazy("admin:calculatorapi_sitepage_changelist"),
                    },
                    {
                        "title": _("FAQ"),
                        "icon": "quiz",
                        "link": reverse_lazy("admin:calculatorapi_faqcategory_changelist"),
                    },
                    {
                        "title": _("Changelog"),
                        "icon": "history",
                        "link": reverse_lazy("admin:calculatorapi_changelogentry_changelist"),
                    },
                    {
                        "title": _("Patreon supporters"),
                        "icon": "volunteer_activism",
                        "link": reverse_lazy("admin:calculatorapi_patreonsupporter_changelist"),
                    },
                    {
                        "title": _("Patreon tiers"),
                        "icon": "workspace_premium",
                        "link": reverse_lazy("admin:calculatorapi_patreontier_changelist"),
                    },
                ],
            },
            {
                "title": _("Users & access"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Users"),
                        "icon": "person",
                        "link": reverse_lazy("admin:calculatorapi_customuser_changelist"),
                        "permission": _requires_perm("calculatorapi.view_customuser"),
                    },
                    {
                        "title": _("Plans"),
                        "icon": "folder_copy",
                        "link": reverse_lazy("admin:calculatorapi_plan_changelist"),
                        "permission": _requires_perm("calculatorapi.view_plan"),
                    },
                    {
                        "title": _("Income profiles"),
                        "icon": "switch_account",
                        "link": reverse_lazy("admin:calculatorapi_incomeprofile_changelist"),
                        "permission": _requires_perm("calculatorapi.view_incomeprofile"),
                    },
                    {
                        "title": _("Planned banners"),
                        "icon": "checklist",
                        "link": reverse_lazy("admin:calculatorapi_userplannedbanner_changelist"),
                        "permission": _requires_perm("calculatorapi.view_userplannedbanner"),
                    },
                    {
                        "title": _("Planned purchases"),
                        "icon": "shopping_cart",
                        "link": reverse_lazy("admin:calculatorapi_userplannedpurchase_changelist"),
                        "permission": _requires_perm("calculatorapi.view_userplannedpurchase"),
                    },
                    {
                        "title": _("Step-up selections"),
                        "icon": "star",
                        "link": reverse_lazy("admin:calculatorapi_userstepupselection_changelist"),
                        "permission": _requires_perm("calculatorapi.view_userstepupselection"),
                    },
                    {
                        "title": _("Linked accounts"),
                        "icon": "link",
                        "link": reverse_lazy("admin:calculatorapi_socialaccount_changelist"),
                        "permission": _requires_perm("calculatorapi.view_socialaccount"),
                    },
                    {
                        "title": _("Groups"),
                        "icon": "shield_person",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                        "permission": _requires_perm("auth.view_group"),
                    },
                ],
            },
        ],
    },
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.TokenAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # The two public, unauthenticated write endpoints are throttled; nothing
    # else needs to be, since everything else is either read-only or requires a
    # token.
    #
    # visit_beacon (views/visits.py) is what stops one client running the
    # page-view counter up on its own. 60/hour is far above what the SPA does
    # -- one beacon per browser session -- with headroom for a group of people
    # sharing an office or campus NAT.
    "DEFAULT_THROTTLE_RATES": {
        "visit_beacon": "60/hour",
        # The scheduled sync runs once a day. This is generous enough for manual
        # workflow_dispatch runs and retries while still capping what a leaked
        # key could do -- each accepted request spends Patreon API quota.
        "patreon_sync": "12/hour",
        # Starting a link is authenticated, so this is not abuse protection so
        # much as a cap on outbound work: every accepted call mints a signed
        # state and sends the user to a provider. 20/hour is far above any
        # honest use (you link an account once) and low enough that a stolen
        # token cannot be used to hammer Patreon on our behalf.
        "account_link": "20/hour",
    },
}

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ORIGIN_WHITELIST", "http://localhost:5173").split(",")

# --- Social sign-in (Google / Discord) -------------------------------------
# Ordinary accounts authenticate through a provider instead of a password, so
# no email/password/name is ever stored for them (see models/social_account.py).
# Credentials live here rather than being read inside calculatorapi/oauth.py so
# tests can swap them with @override_settings.
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
DISCORD_OAUTH_CLIENT_ID = os.getenv("DISCORD_OAUTH_CLIENT_ID", "")
DISCORD_OAUTH_CLIENT_SECRET = os.getenv("DISCORD_OAUTH_CLIENT_SECRET", "")

# Patreon as a SIGN-IN provider. Read the warning below before touching these.
#
# ⚠️ THERE ARE TWO PATREON CREDENTIAL PAIRS AND THEY ARE NOT INTERCHANGEABLE.
#
#   PATREON_OAUTH_CLIENT_ID / _SECRET  (here)   — the identity app. Acts on
#       behalf of A VISITOR, who consents to it. Scope: "identity". Used by
#       calculatorapi/oauth.py to learn one opaque user id, exactly like Google
#       and Discord.
#
#   PATREON_CLIENT_ID / _SECRET  (further down) — the creator app. Acts on
#       behalf of THE SITE OWNER, reading our own campaign's member list. Used
#       by calculatorapi/patreon_api.py for the supporters sync.
#
# They can be the same registered client on Patreon's side, but they are used
# for different things with different tokens, and wiring one pair where the
# other belongs fails in a way that reads like an outage rather than a config
# error. Keep the names distinct and the uses separate.
PATREON_OAUTH_CLIENT_ID = os.getenv("PATREON_OAUTH_CLIENT_ID", "")
PATREON_OAUTH_CLIENT_SECRET = os.getenv("PATREON_OAUTH_CLIENT_SECRET", "")

# --- Patreon supporters sync -------------------------------------------------
# Client credentials for the Patreon API v2 client (registered at
# patreon.com/portal/registration/register-clients). Here rather than read inside
# calculatorapi/patreon_api.py so tests can swap them with @override_settings,
# same reason as the OAuth pair above.
#
# The ACCESS and REFRESH tokens are deliberately NOT settings: they rotate on
# every refresh and so live on the PatreonCredentials row, which reads the
# environment only to seed itself the first time.
PATREON_CLIENT_ID = os.getenv("PATREON_CLIENT_ID", "")
PATREON_CLIENT_SECRET = os.getenv("PATREON_CLIENT_SECRET", "")

# Shared secret for POST /patreon/sync, which the scheduled GitHub Action calls.
# While this is empty the route returns 404 and the scheduled sync is simply
# switched off -- there is no half-configured state in which the endpoint exists
# but accepts anything.
PATREON_SYNC_SECRET = os.getenv("PATREON_SYNC_SECRET", "")

# The admin's "Rebuild website" button (calculatorapi/digitalocean_api.py). A
# personal access token scoped to apps, and this app's id. Both blank locally;
# the button then says it is not configured instead of calling anything.
DO_API_TOKEN = os.getenv("DO_API_TOKEN", "")
DO_APP_ID = os.getenv("DO_APP_ID", "")

# Where the SPA lives. In production the ingress serves it from the same host
# as the API; in dev it is the Vite server on :5173.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")

# Both providers require the redirect_uri to match BYTE-FOR-BYTE across the
# authorize request, the token request, and the value registered in their
# console -- a stray trailing slash is enough to get rejected. Deriving it once
# from FRONTEND_URL guarantees the two requests agree; only the console entry
# has to be kept in sync by hand.
OAUTH_REDIRECT_URI = f"{FRONTEND_URL}/auth/callback"

# Redirect URIs that /auth/<provider>/start will honour when the SPA asks for
# one explicitly, on top of the canonical OAUTH_REDIRECT_URI above. Comma-
# separated; empty by default, which reproduces the old single-address
# behaviour exactly.
#
# WHY THIS EXISTS: `npm run dev:live` runs the local Vite dev server against a
# DEPLOYED backend, so a login started there has to come back to
# http://localhost:5173/auth/callback -- otherwise a developer can never be
# signed in while looking at real content, which is the whole point of that
# mode. Setting this on a deployed app is a deliberate act: it lets that app
# hand authorization codes to a loopback address.
#
# The ALLOWLIST is the security boundary. Honouring an arbitrary client-supplied
# redirect_uri would turn this endpoint into an open redirector that leaks
# authorization codes to whoever asked, so the server -- never the client --
# decides which addresses are acceptable. Entries must match BYTE-FOR-BYTE for
# the same reason OAUTH_REDIRECT_URI does, and must also be registered in the
# provider console.
_extra_redirect_uris = [
    uri.strip()
    for uri in os.getenv("OAUTH_EXTRA_REDIRECT_URIS", "").split(",")
    if uri.strip()
]
OAUTH_ALLOWED_REDIRECT_URIS = frozenset([OAUTH_REDIRECT_URI, *_extra_redirect_uris])

# How long a signed OAuth `state` stays valid. Covers a slow trip through the
# provider's consent screen without leaving replay material lying around.
OAUTH_STATE_MAX_AGE_SECONDS = 600

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "calculatorproject.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "calculatorproject.wsgi.application"

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.config(conn_max_age=600)
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# django-storages: media files go to DigitalOcean Spaces; static files stay on whitenoise.
# STORAGES replaces the old STATICFILES_STORAGE setting (deprecated in Django 4.2+).
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {
            "access_key": os.getenv("DO_SPACES_ACCESS_KEY"),
            "secret_key": os.getenv("DO_SPACES_SECRET_KEY"),
            "bucket_name": os.getenv("DO_SPACES_BUCKET_NAME"),
            # Base Spaces endpoint (not the CDN) — boto3 uses this for API calls.
            "endpoint_url": os.getenv("DO_SPACES_ENDPOINT_URL"),
            # CDN domain used to build public-facing image URLs (no https:// prefix).
            "custom_domain": os.getenv("DO_SPACES_CDN_DOMAIN"),
            "default_acl": "public-read",
            "file_overwrite": False,
            # Disable signed query params so CDN URLs are clean and cacheable.
            "querystring_auth": False,
        },
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "calculatorapi.CustomUser"

# Pinned rather than @latest: the docs page runs this third-party JavaScript,
# and anyone who pastes a token into its "Authorize" box is trusting it.
_SWAGGER_UI = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.15"

SPECTACULAR_SETTINGS = {
    "TITLE": "Uma Carat Calculator API",
    "DESCRIPTION": "Reference data, plan storage and OAuth sign-in for umacaratcalculator.com.",
    "VERSION": "1.0.0",
    # App Platform serves this API under /api and strips the prefix before a
    # request reaches Django, so nothing in here can see it. The schema has to be
    # told, or Swagger UI's "Try it out" would send /calculator-data to the static
    # site instead of the API. Unset locally, where runserver is the whole origin.
    "SERVERS": [{"url": os.getenv("API_PUBLIC_PREFIX", "").rstrip("/") or "/"}],
    # The schema route documenting itself is noise.
    "SERVE_INCLUDE_SCHEMA": False,
    "SWAGGER_UI_DIST": _SWAGGER_UI,
    "SWAGGER_UI_FAVICON_HREF": f"{_SWAGGER_UI}/favicon-32x32.png",
    # Generation notes (views it cannot infer a body for) are for whoever is
    # working on the schema: printed with DEBUG on, kept out of the production
    # log and the test output.
    "DISABLE_ERRORS_AND_WARNINGS": not DEBUG,
}

WHITENOISE_STATIC_PREFIX = '/static/'

# Local-memory cache, holding the public half of GET /calculator-data so the
# app's hot route stops rebuilding a ~1MB response per visitor. This is Django's
# default backend and was already in effect implicitly; it is spelled out here
# so the size limit and the deployment assumption are visible.
#
# LocMem lives inside ONE process, which is correct only while the service runs
# a single one (.do/app.yaml: instance_count 1, gunicorn with no --workers).
# calculatorapi/public_payload_cache.py carries the full reasoning and what to
# change if that ever stops being true.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "calculator-data",
        "OPTIONS": {
            # One entry is all this cache is for. The default (300) would let an
            # accidental per-request key quietly hold 300 copies of a megabyte.
            "MAX_ENTRIES": 8,
        },
    }
}

# Log all Django errors to stdout so DigitalOcean runtime logs capture tracebacks.
# Without this, Django silently swallows 500 errors when DEBUG=False.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        # Our own app. Without this, calculatorapi's logger.warning calls
        # propagate to the root logger, which has no handler configured here --
        # so an OAuth integration failing would write nothing anywhere.
        "calculatorapi": {
            "handlers": ["console"],
            "level": "WARNING",
        },
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}
