from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework import routers
from calculatorapi.views import (
    TeamTrialsRankViewSet,
    ClubRankViewSet,
    ChampionsMeetingRankViewSet,
    LeagueOfHeroesRankViewSet,
    LeagueOfHeroesViewSet,
    CalculatorViewSet,
    GameEventViewSet,
    ChangelogEntryViewSet,
    PatreonSupporterViewSet,
    UmaViewSet,
)
from calculatorapi.views.admin_images import admin_image_library
from calculatorapi.views.analytics import analytics_dashboard
from calculatorapi.views.user import user_login, user_logout
from calculatorapi.views.account import account_detail
from calculatorapi.views.plan_routes import plan_detail, plan_list
from calculatorapi.views.account_linking import (
    account_link_complete,
    account_link_delete,
    account_link_start,
)
from calculatorapi.views.visits import site_visit
from calculatorapi.views.patreon_supporters import patreon_sync
from calculatorapi.views.site_content import site_content
from calculatorapi.views.social_auth import social_auth_start, social_auth_complete

router = routers.SimpleRouter(trailing_slash=False)
router.register(r"teamtrialranks", TeamTrialsRankViewSet, basename="teamtrialrank")
router.register(r"clubranks", ClubRankViewSet, basename="clubrank")
router.register(
    r"championsmeetingranks",
    ChampionsMeetingRankViewSet,
    basename="championsmeetingrank",
)
router.register(
    r"leagueofheroesranks",
    LeagueOfHeroesRankViewSet,
    basename="leagueofheroesrank",
)
router.register(r"leagueofheroes", LeagueOfHeroesViewSet, basename="leagueofheroes")
router.register(r"events", GameEventViewSet, basename="event")
router.register(r"changelog", ChangelogEntryViewSet, basename="changelog")
# Public thank-you list for the home page. Read-only by construction — the
# viewset has no write actions at all, not merely permission-gated ones.
router.register(r"supporters", PatreonSupporterViewSet, basename="supporter")
# The uma catalogue as picker options (id, name, image), for the oshi picker
# on /account — a page that never loads /calculator-data. Read-only, public.
router.register(r"umas", UmaViewSet, basename="uma")

urlpatterns = [
    path("", include(router.urls)),
    # Password login is staff-only now; ordinary accounts use /auth/* below.
    # There is intentionally no "register" route — see views/user.py.
    path("login", user_login, name="login"),
    path("logout", user_logout, name="logout"),
    # GET: who the caller is, and what they are entitled to. Authenticated-only
    # and never cached — it is the SPA's source of truth for "am I signed in?",
    # replacing a localStorage token check that could only describe the browser.
    # DELETE: remove the caller's account (staff refused). → views/account.py
    path("account", account_detail, name="account"),
    # A signed-in user's named pull plans. The banner ROWS are still saved by
    # PATCH /calculator-data (now carrying plan_id); these create, rename,
    # switch, copy and delete the plans themselves. -> views/plan_routes.py
    path("plans", plan_list, name="plan-list"),
    path("plans/<int:plan_id>", plan_detail, name="plan-detail"),
    # Attaching a provider identity to an account that is ALREADY signed in.
    # Separate from /auth/* above and deliberately so: those create accounts,
    # these must never. The signed state here is bound to the user it was minted
    # for. → views/account_linking.py
    path(
        "account/link/<str:provider>/start",
        account_link_start,
        name="account-link-start",
    ),
    path(
        "account/link/<str:provider>/complete",
        account_link_complete,
        name="account-link-complete",
    ),
    path(
        "account/link/<str:provider>",
        account_link_delete,
        name="account-link-delete",
    ),
    # Traffic beacon, pinged once per session by the SPA. Public and write-only
    # — the frontend is a separate static site, so this is the only way Django
    # learns that a page was loaded at all.
    path("visit", site_visit, name="site-visit"),
    # Trigger for the scheduled supporters sync, called by a GitHub Action.
    # Authorised by a shared secret header, and 404s entirely while
    # PATREON_SYNC_SECRET is unset. Cannot publish a name — see the view.
    path("patreon/sync", patreon_sync, name="patreon-sync"),
    # The admin-editable pages and FAQ, in one public read-only response. The
    # frontend build bakes it into the prerendered HTML; a loaded page fetches
    # it once more to pick up edits made since. → views/site_content.py
    path("site-content", site_content, name="site-content"),
    # Social sign-in (Google / Discord). The <provider> segment is validated by
    # the view against oauth.SUPPORTED_PROVIDERS rather than by a URL regex, so
    # an unknown provider gets a JSON 404 instead of Django's HTML one.
    path("auth/<str:provider>/start", social_auth_start, name="social-auth-start"),
    path("auth/social", social_auth_complete, name="social-auth-complete"),
    # Must come BEFORE admin.site.urls — the admin URLconf ends in a
    # catch-all that would 404 this path. admin_view() makes the page
    # staff-only (redirects everyone else to the admin login).
    path(
        "admin/analytics/",
        admin.site.admin_view(analytics_dashboard),
        name="admin-analytics",
    ),
    # Same placement rule as analytics above: before admin.site.urls, and
    # admin_view() makes it staff-only. Feeds the "choose an existing image"
    # picker on the content change forms.
    path(
        "admin/image-library/",
        admin.site.admin_view(admin_image_library),
        name="admin-image-library",
    ),
    path("admin/", admin.site.urls),
    path(
        "calculator-data",
        CalculatorViewSet.as_view(
            {"get": "get_calculator_data", "patch": "update_calculator_data"}
        ),
        name="calculator-data",
    ),
    # The OpenAPI schema, and Swagger UI over it. Both public. The docs page
    # fetches the schema by a RELATIVE url on purpose: production serves this
    # API under /api and strips the prefix, so the absolute /schema that
    # reverse() would produce lands on the static site instead.
    path("schema", SpectacularAPIView.as_view(), name="schema"),
    path("docs", SpectacularSwaggerView.as_view(url="schema"), name="api-docs"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
