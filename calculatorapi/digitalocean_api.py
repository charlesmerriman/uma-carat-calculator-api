"""
The one call this app makes to DigitalOcean: start a rebuild of itself.

The admin's "Rebuild website" button lives here so the admin view stays a
thin gate-and-message wrapper, and so the tests can patch one module.

WHY A REBUILD AND NOT A CACHE PURGE
-----------------------------------
The site's public pages are prerendered at build time with the words baked
into the HTML (see the frontend's scripts/prerender.mjs). A visitor's browser
picks up an admin edit on its own after the page hydrates, but a crawler reads
the baked HTML, and the only way to change that is a new build. A deployment
with `force_build` is exactly that: App Platform rebuilds every component from
its current commit and swaps the result in.

Configured by two environment variables (see settings.py): DO_API_TOKEN, a
personal access token scoped to apps, and DO_APP_ID. Unset means the button
explains it is not configured rather than failing.
"""

import requests
from django.conf import settings

API_BASE = "https://api.digitalocean.com/v2"
HTTP_TIMEOUT_SECONDS = 15

# Deployment phases that mean "still running" in App Platform's model. Anything
# else (ACTIVE, ERROR, CANCELED, SUPERSEDED) is finished.
IN_PROGRESS_PHASES = frozenset({
    "PENDING_BUILD", "BUILDING", "PENDING_DEPLOY", "DEPLOYING",
})


class RebuildError(Exception):
    """A rebuild could not be started. The message is shown to the editor."""


def is_configured():
    return bool(settings.DO_API_TOKEN and settings.DO_APP_ID)


def _headers():
    return {
        "Authorization": f"Bearer {settings.DO_API_TOKEN}",
        "Content-Type": "application/json",
    }


def _request(method, path, **kwargs):
    try:
        response = requests.request(
            method, f"{API_BASE}{path}", headers=_headers(),
            timeout=HTTP_TIMEOUT_SECONDS, **kwargs,
        )
    except requests.RequestException as exc:
        raise RebuildError(f"Could not reach DigitalOcean: {exc}") from exc
    if response.status_code >= 400:
        raise RebuildError(
            f"DigitalOcean answered {response.status_code}. Check DO_API_TOKEN "
            "and DO_APP_ID, and that the token can manage apps."
        )
    try:
        return response.json()
    except ValueError as exc:
        raise RebuildError("DigitalOcean sent a response that was not JSON.") from exc


def deployment_in_progress():
    """True if the latest deployment is still building or deploying."""
    payload = _request(
        "GET", f"/apps/{settings.DO_APP_ID}/deployments", params={"per_page": 1},
    )
    deployments = payload.get("deployments") or []
    if not deployments:
        return False
    return deployments[0].get("phase") in IN_PROGRESS_PHASES


def trigger_rebuild():
    """Start a forced rebuild. Returns the new deployment's id.

    Raises RebuildError if unconfigured, if a deployment is already running
    (so an eager editor cannot queue five rebuilds), or if the API refuses.
    """
    if not is_configured():
        raise RebuildError(
            "The rebuild button is not set up on this server: DO_API_TOKEN and "
            "DO_APP_ID are not configured."
        )
    if deployment_in_progress():
        raise RebuildError(
            "A rebuild is already running. Wait for it to finish (about 5 to 10 "
            "minutes) before starting another; it will include every edit saved "
            "before it started."
        )
    payload = _request(
        "POST", f"/apps/{settings.DO_APP_ID}/deployments", json={"force_build": True},
    )
    return (payload.get("deployment") or {}).get("id", "")
