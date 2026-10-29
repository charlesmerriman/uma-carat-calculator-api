from datetime import timedelta
from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv
from corsheaders.defaults import default_headers
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

# Django refuses any request with more than this many form fields and answers
# a bare 400 (TooManyFieldsSent) before the view runs. The default of 1000 is a
# guard against hash-flooding attacks, sized for ordinary forms. The PvP guide
# admin page saves every table of a guide in ONE form (stats, team comps, and
# the umas, skills, decks with their six cards and mentions of five styles),
# and a filled guide posts well over 1000 fields, so saving one returned 400
# in prod on 2026-10-15. 10,000 leaves room for guides to grow about eightfold.
# Only the admin posts forms this shape; the API takes JSON, which this limit
# does not count.
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10_000

INSTALLED_APPS = [
    # django-unfold must come BEFORE the admin so its templates override the
    # stock admin's (Django resolves app templates in order). BasicAppConfig,
    # not plain "unfold": unfold's default config replaces admin.site with its
    # own instance in ready(), which would undo the site swap on the next line.
    "unfold.apps.BasicAppConfig",
    # Django's admin app with default_site pointed at our AdminSite subclass:
    # unfold's theme plus django-otp's second factor. -> calculatorproject/apps.py
    "calculatorproject.apps.CalculatorAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Two-factor for the admin login: the device base, the authenticator-app
    # (TOTP) device and the backup codes. Listed BEFORE calculatorapi so their
    # stock device admins register first and calculatorapi/admin.py can take
    # them off the site again (a device is managed beside its owner, as
    # inlines on the user page). -> calculatorapi/admin_site.py
    "django_otp",
    "django_otp.plugins.otp_totp",
    "django_otp.plugins.otp_static",
    # Login lockout: counts wrong passwords per (username, address) in the
    # database and refuses the pair for a cool-off. Settings further down.
    "axes",
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
        {
            "icon": "lock",
            "title": _("Two-factor settings"),
            "link": reverse_lazy("admin:two_factor"),
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
                        "title": _("PvP guides"),
                        "icon": "menu_book",
                        "link": reverse_lazy("admin:calculatorapi_pvpguide_changelist"),
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
                    {
                        "title": _("Daily legend races"),
                        "icon": "trophy",
                        "link": reverse_lazy("admin:calculatorapi_dailylegendracerelease_changelist"),
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
                        "title": _("Gallery reports"),
                        "icon": "flag",
                        "link": reverse_lazy("admin:calculatorapi_galleryreport_changelist"),
                        "permission": _requires_perm("calculatorapi.view_galleryreport"),
                    },
                    {
                        "title": _("Gallery posts"),
                        "icon": "photo_library",
                        "link": reverse_lazy("admin:calculatorapi_gallerypost_changelist"),
                        "permission": _requires_perm("calculatorapi.view_gallerypost"),
                    },
                    {
                        "title": _("Gallery comments"),
                        "icon": "forum",
                        "link": reverse_lazy("admin:calculatorapi_gallerycomment_changelist"),
                        "permission": _requires_perm("calculatorapi.view_gallerycomment"),
                    },
                    {
                        "title": _("Blocked terms"),
                        "icon": "block",
                        "link": reverse_lazy("admin:calculatorapi_blockedterm_changelist"),
                        "permission": _requires_perm("calculatorapi.view_blockedterm"),
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
            # The admin's own front door. The first item is everyone's (your
            # authenticator and backup codes); the two logs are axes's tables,
            # owner-only because the content editors group holds no axes
            # permission. -> calculatorapi/admin_site.py, admin.py section 10
            {
                "title": _("Security"),
                "separator": True,
                "collapsible": True,
                "items": [
                    {
                        "title": _("Two-factor settings"),
                        "icon": "lock",
                        "link": reverse_lazy("admin:two_factor"),
                    },
                    {
                        "title": _("Sign-in log"),
                        "icon": "history",
                        "link": reverse_lazy("admin:axes_accesslog_changelist"),
                        "permission": _requires_perm("axes.view_accesslog"),
                    },
                    {
                        "title": _("Locked out"),
                        "icon": "lock_clock",
                        "link": reverse_lazy("admin:axes_accessattempt_changelist"),
                        "permission": _requires_perm("axes.view_accessattempt"),
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
    # The public, unauthenticated write endpoints are throttled, plus the one
    # public read that hands out a user's data; nothing else needs to be,
    # since everything else is either catalogue, read-only, or requires a
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
        # The bot worker (views/bot_api.py): every moderator click and /mod
        # command is one call, all from the worker's one address. 600/hour is
        # far past a busy moderation hour and still caps what a leaked key does.
        "bot_api": "600/hour",
        # Starting a link is authenticated, so this is not abuse protection so
        # much as a cap on outbound work: every accepted call mints a signed
        # state and sends the user to a provider. 20/hour is far above any
        # honest use (you link an account once) and low enough that a stolen
        # token cannot be used to hammer Patreon on our behalf.
        "account_link": "20/hour",
        # A shared plan's landing card (views/plan_public.py). A person opens a
        # link a handful of times; 120/hour leaves room for a Discord channel
        # behind one NAT clicking the same link, and still stops a script from
        # walking the id space (it 404s 120 times an hour, out of 62^8).
        "public_plan": "120/hour",
        # Taking a copy writes a plan (or, for a guest, hands out the full rows)
        # and bumps the copy count. Nobody copies thirty plans in an hour; a
        # script inflating a count is capped here, not stopped, which is
        # acceptable for a number that is social proof.
        "public_plan_copy": "30/hour",
        # The gallery (views/gallery.py, views/pvp_gallery.py). Reads cover
        # feeds, posts and comments; a busy reader stays well under 120.
        "gallery_read": "120/hour",
        # Every upvote and take-back. Generous: clicking around a feed is
        # honest use; a script running votes up is capped.
        "gallery_vote": "120/hour",
        # Checked by hand, so an over-limit view is dropped rather than
        # answered 429 (the client fires and forgets).
        "gallery_view": "300/hour",
        # The public_plan_copy figure, for the same reasons.
        "gallery_copy": "30/hour",
        # A re-post moves a post to the top of New and clears its votes.
        "gallery_publish": "20/hour",
        # Comments, and a lower rate for accounts younger than a day
        # (views/gallery.py GalleryCommentThrottle): sign-in costs a Google,
        # Discord or Patreon account, and this caps a fresh batch's first day.
        "gallery_comment": "20/hour",
        "gallery_comment_new": "5/hour",
        # Reports. Plenty for anyone honest; a report campaign is capped.
        "gallery_report": "20/day",
        # The /post/<id> share page and its preview picture. High: Discord and
        # X crawl from a few shared addresses, and every paste is a fetch.
        "gallery_share": "600/hour",
        # The site's guide editor (views/pvp_guide_editor.py). A save is one
        # PUT of the whole guide two seconds after the last keystroke, so an
        # hour of typing is a few hundred at most; a stuck client is capped.
        "guide_editor": "1200/hour",
        # Copy from a guide, load from the sheet, fill track greens: a
        # preview-then-apply loop, a handful per guide.
        "guide_editor_action": "60/hour",
        # The /plan/<id> share page (views/plan_share.py), for the same reason.
        "plan_share": "600/hour",
        # The /guide/<kind>-<id> share page (views/guide_share.py), likewise.
        "guide_share": "600/hour",
    },
}

CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = os.getenv("CORS_ORIGIN_WHITELIST", "http://localhost:5173").split(",")
# The library's defaults plus Content-Encoding: the veteran import is gzipped
# (views/trained_uma.GzipJSONParser), and a cross-origin page (local dev,
# `npm run dev:live` against prod) is refused at the preflight without it.
CORS_ALLOW_HEADERS = (*default_headers, "content-encoding")

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


# The site's Discord bot (calculatorapi/discord_messages.py). The token lets
# Django post and edit messages as the bot; each feature has its own channel
# id, and stays silent while either is blank (locally, in tests, and in
# production until set). See discord-bot-plan.md for the bot as a whole.
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
# #changelog: each patch note posts there on the deploy that ships it.
DISCORD_CHANGELOG_CHANNEL_ID = os.getenv("DISCORD_CHANGELOG_CHANNEL_ID", "")
# A role each new patch note pings (the community server's @Carat Calc Ping).
# Blank: patch notes ping nobody.
DISCORD_CHANGELOG_PING_ROLE_ID = os.getenv("DISCORD_CHANGELOG_PING_ROLE_ID", "")
# Optional YYYY-MM-DD: patch notes dated before it never post. Set to the day
# the bot went live, so releases already announced by hand don't repeat.
DISCORD_CHANGELOG_START_DATE = os.getenv("DISCORD_CHANGELOG_START_DATE", "")
# #calc-mod-queue: a card per reported gallery post or comment
# (calculatorapi/moderation_cards.py). Private to moderators in Discord.
DISCORD_MOD_QUEUE_CHANNEL_ID = os.getenv("DISCORD_MOD_QUEUE_CHANNEL_ID", "")
# The team's channel for the Rankings page's weekly movers post
# (calculatorapi/rankings_snapshots.py): the three biggest climbers of the
# lists worth pinning, once a week. Blank: nothing is posted.
DISCORD_RANKINGS_CHANNEL_ID = os.getenv("DISCORD_RANKINGS_CHANNEL_ID", "")
# The bot worker's key to the bot API (views/bot_api.py), sent as X-Bot-Key.
# Blank: every bot route 404s AND the cards carry no buttons, so a card never
# offers a click nothing is listening for. Set it together with the worker.
BOT_API_SECRET = os.getenv("BOT_API_SECRET", "")

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

# ── Admin login: the second factor and the lockout ───────────────────────────
# Whether /admin asks for an authenticator code after the password. ON unless
# the env var says "False", in production and locally alike. Turning it on
# cannot lock anyone out: a staff account with no authenticator yet is walked
# through setting one up at its next login (calculatorapi/admin_site.py). The
# switch exists for two reasons. A local .env can set it to False so the
# local admin stays password-only. And it is the recovery path if a phone AND
# its backup codes are both lost: set False in the DigitalOcean dashboard,
# sign in, delete the lost authenticator on the user page, set it back to
# True, enrol again. -> docs/admin.md ("Signing in and two-factor")
ADMIN_REQUIRE_OTP = os.getenv("ADMIN_REQUIRE_OTP", "True") == "True"
# The label an authenticator app shows beside the code.
OTP_TOTP_ISSUER = "Uma Carat Calculator"

# django-axes. Its backend runs FIRST so a locked-out pair is refused before
# the password is checked at all; it authenticates nobody itself, ModelBackend
# still does that. Every password login in this project goes through
# authenticate() with a request (the admin form), which axes requires.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
# Lock the (username, address) PAIR, never either alone. Username alone lets
# anyone lock the owner out with five wrong passwords; address alone is
# sidestepped by rotating addresses. The address is the forwarded client
# address (visits.client_ip): behind App Platform, REMOTE_ADDR is the proxy
# and keying on it would lock every visitor at once. That forwarded entry is
# client-settable, so a determined attacker with a fresh header every five
# guesses is not stopped here. This stops sprays; the second factor is the
# wall. -> docs/admin.md
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_CLIENT_IP_CALLABLE = "calculatorapi.visits.client_ip"
# A correct login clears the pair's count, so a typo a week never adds up.
AXES_RESET_ON_SUCCESS = True
# Rendered with the admin's own unauthenticated layout. The status code stays
# axes's default, 429.
AXES_LOCKOUT_TEMPLATE = "admin/two_factor/lockout.html"
# axes's stock admins are plain ModelAdmins and render unstyled under unfold;
# calculatorapi/admin.py registers themed read-only ones (the Security group).
AXES_ENABLE_ADMIN = False

MIDDLEWARE = [
    # First, so it counts every query a request runs (the session and auth
    # lookups too) for gunicorn's access line: calculatorapi/middleware.py.
    "calculatorapi.middleware.RequestStatsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Gives request.user an is_verified() answering "did THIS session pass
    # the second factor", from the device id django-otp stored at login. Must
    # follow AuthenticationMiddleware, which sets request.user.
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Last: turns a lockout raised inside a login form into the 429 page
    # (AXES_LOCKOUT_TEMPLATE) instead of the form's "wrong password".
    "axes.middleware.AxesMiddleware",
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

# `manage.py test` runs through a runner that swaps the password hasher for
# the fastest one for the length of the run: a production PBKDF2 hash is
# tuned to be slow, and the suite creates a user with a password in most of
# its tests. The runner sets the environment variable below in the parent
# process; a parallel worker started with forkserver() or spawn() (Python
# 3.14's default) imports this file afresh and applies the same swap by
# reading it. Never set the variable by hand: outside a test process tree it
# would store weak hashes. -> calculatorproject/test_runner.py
TEST_RUNNER = "calculatorproject.test_runner.FastHashRunner"
if os.environ.get("CALCULATOR_TEST_RUN") == "1":
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

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
# a single one (.do/app.yaml: instance_count 1, gunicorn.conf.py: workers = 1;
# threads share the process, so they share the cache).
# calculatorapi/public_payload_cache.py carries the full reasoning and what to
# change if that ever stops being true.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "calculator-data",
        "OPTIONS": {
            # What lives here: the /calculator-data payload (~1MB), the admin
            # analytics report (calculatorapi.analytics.get_report, a few KB),
            # and DRF's throttle counters: one small key per client IP per
            # scope, each kept for its hour.
            #
            # The bound stops an accidental per-request key from quietly holding
            # hundreds of copies of a megabyte (the default is 300). When the
            # cache is full, LocMem drops the least-recently-used THIRD of its
            # keys, and every visitor in the past hour holds a visit_beacon key,
            # so in a busy hour it is always full. The payload is read on every
            # /app load and stays at the recent end. The report is read only
            # when staff look, so it is the key that falls off: at 8 entries a
            # handful of new visitors would push it out, at 32 it takes a few
            # dozen. Losing it costs one rebuild. Worst case is still two
            # payloads and a few dozen counters.
            #
            # Since the PvP team builder (R2) it also holds GET /pvp-catalogue
            # (~500 KB, calculatorapi/pvp_catalogue_cache.py), read only when
            # someone opens the builder, so 40 keeps it from being the next key
            # to fall off. Worst case: two payloads, two catalogues, counters.
            # Since 2026-10-27 also the signed-in date lookups beside the
            # payload (public_payload_cache.USER_CONTEXT_KEY, ~26 KB), read on
            # every signed-in load, so never the key that falls off.
            "MAX_ENTRIES": 40,
        },
    },
    # The gallery's own small keys, kept OUT of "default" on purpose: one
    # "already counted" key per viewer per post per day (gallery.record_view)
    # and every gallery throttle counter (views/gallery.py GalleryThrottle),
    # plus the /plan/<id> and /guide/<ref> share pages' (views/plan_share.py
    # PlanShareThrottle, views/guide_share.py GuideShareThrottle).
    # In "default" they would crowd its 40 slots and push the /calculator-data
    # payload out, and every visitor after that would pay the ~2s rebuild (the
    # 2026-09-23 outage). Values are a few bytes each, so the bound is high.
    # Same single-process caveat as "default": it lives in this process.
    "gallery": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "gallery",
        "OPTIONS": {"MAX_ENTRIES": 20000},
    },
    # Rendered link-preview images (calculatorapi/gallery_preview.py for a
    # post, plan_preview.py for a shared plan, guide_preview.py for a guide),
    # about 100 KB each. Rendered on request and kept here, never written to
    # storage: default storage is the production Spaces bucket even on a
    # developer's machine. 64 entries caps it near 6 MB; a dropped image costs
    # one re-render the next time a crawler asks.
    "gallery-previews": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "gallery-previews",
        "OPTIONS": {"MAX_ENTRIES": 64},
    },
    # GET /skills and one GET /skills/<id> page per skill (calculatorapi/skills_cache.py):
    # the list is a few hundred KB, a page a few KB, so a full set is ~5 MB.
    # Its own alias so ~2,200 page keys never crowd "default". Same
    # single-process caveat.
    "skills": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "skills",
        "OPTIONS": {"MAX_ENTRIES": 3000},
    },
    # The Rankings page's payloads (calculatorapi/rankings_cache.py), each
    # built at most once an hour by decision and never invalidated: one per
    # tab, plus one per event for the PvP tabs. Tens of KB each. Its own alias
    # so they never crowd "default". Same single-process caveat.
    "rankings": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "rankings",
        "OPTIONS": {"MAX_ENTRIES": 200},
    },
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
        # Failed admin logins, lockouts and resets, one line each, so
        # `doctl apps logs` can answer "is someone hammering the login".
        "axes": {
            "handlers": ["console"],
            "level": "INFO",
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}
