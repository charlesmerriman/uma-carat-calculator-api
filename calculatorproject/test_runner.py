"""The test runner: Django's, with the password hasher swapped for the fastest
one for the length of the run.

WHY
---
Every test that creates a user with a password (make_user, create_superuser,
every login in the admin tests) pays a full PBKDF2 hash, and PBKDF2 is tuned
to take a good fraction of a second on purpose: that cost is what makes a
stolen database slow to crack. Under test it is pure waste, and it added up:
the 2026-10-15 profile had single lockout tests at 3 to 4 s and the gallery
ranking tests (many voters, each a user) at 4 to 7 s, almost all of it
hashing. Swapping in MD5 for the test run is what Django's own documentation
recommends ("Speeding up the tests").

WHY IT TAKES TWO PLACES
-----------------------
A settings module has no safe way to know it is being imported for a test
run: `"test" in sys.argv` misfires on any command with "test" in its
arguments, and a separate test settings module has to be remembered on every
run. So the swap is driven from here, the one piece of code that only
`manage.py test` ever loads (settings.TEST_RUNNER), and it has to happen in
two places because of how --parallel starts its workers:

- This process, and workers started with fork() (Python 3.12's default on
  Linux, so a local run), see the `override_settings` below: fork() copies
  the parent, settings object included.
- Workers started with forkserver() or spawn() (Python 3.14's default on
  Linux, so the CI run) are fresh interpreters: Django calls django.setup()
  in each, which imports settings.py again, and only the environment
  survives into them. So the runner also sets TESTING_ENV, and settings.py
  applies the same hasher list when it sees it. The forkserver process is
  started on first use, which is after setup_test_environment(), so it
  inherits the variable.

`test_the_suite_runs_on_the_fast_hasher` (tests/test_admin_security.py)
fails if either path stops working, so a silent return to the slow hasher
shows up as a red test rather than as a slow suite.
"""

import os

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings

# Read by settings.py. The value is not a secret and not a mode switch for
# the app: it only ever exists inside a `manage.py test` process tree.
TESTING_ENV = "CALCULATOR_TEST_RUN"
FAST_PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


class FastHashRunner(DiscoverRunner):
    """DiscoverRunner, plus the hasher swap above for the whole run."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fast_hashers = None

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # override_settings rather than assigning to settings: enable() sends
        # setting_changed, which empties django.contrib.auth.hashers' cache of
        # the hasher list, so a hash computed before this point is not reused.
        self._fast_hashers = override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
        self._fast_hashers.enable()
        os.environ[TESTING_ENV] = "1"

    def teardown_test_environment(self, **kwargs):
        os.environ.pop(TESTING_ENV, None)
        self._fast_hashers.disable()
        super().teardown_test_environment(**kwargs)
