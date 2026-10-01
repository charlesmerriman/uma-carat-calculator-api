"""
Copies production's content down into the LOCAL database.

Production's database has no outside endpoint, so nothing here connects to it.
The snapshot takes a detour instead:

1. `doctl apps console` opens a shell in the production container, where
   Django's own `dumpdata` writes the content tables to a temp file. That is a
   read; production's database is never written to.
2. Still in that shell, the file is uploaded to the media Space as a PRIVATE
   object under a random name.
3. Back here, the object is downloaded with the Spaces credentials in `.env`,
   then deleted from the Space.
4. `load_content_snapshot` replaces the local content tables with it.

Only the tables in `content_snapshot.CONTENT_MODELS` are dumped: no accounts,
plans, supporters, credentials or traffic counts. Local only: it refuses any
database that is not SQLite before it contacts production at all.

Needs `doctl` installed and signed in, and the same `DO_SPACES_*` values in
`.env` that production uses.

Usage:
    python manage.py pull_prod_content
    python manage.py pull_prod_content --keep snapshot.json.gz   # keep the file too
"""

import os
import pty
import re
import secrets
import select
import shutil
import subprocess
import tempfile
import time

from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from calculatorapi.content_snapshot import content_labels, require_local_database

# The service's name in .do/app.yaml, which is what `doctl apps console` takes.
COMPONENT = "calculatorproject"
REMOTE_FILE = "/tmp/content_snapshot.json.gz"

# The remote shell prints this when the commands finish, with their exit code
# where `$?` is. The terminal also echoes the line as it is typed, but the echo
# still has the literal `$?`, so only the real result matches the pattern.
SENTINEL = "__SNAPSHOT_EXIT_$?__"
SENTINEL_PATTERN = re.compile(rb"__SNAPSHOT_EXIT_(\d+)__")

SHELL_QUIET_SECONDS = 2
SHELL_READY_TIMEOUT = 90
COMMAND_TIMEOUT = 300


def remote_command_line(key):
    """The one shell line that dumps, uploads and cleans up in the container."""
    upload = (
        "from django.core.files.storage import default_storage; "
        f"default_storage.bucket.upload_file('{REMOTE_FILE}', '{key}', "
        "ExtraArgs={'ACL': 'private'})"
    )
    steps = " && ".join([
        f"python manage.py dumpdata {' '.join(content_labels())} -o {REMOTE_FILE}",
        f'python manage.py shell -c "{upload}"',
    ])
    # The temp file goes whether or not the steps worked; `status` carries the
    # steps' exit code past the rm so the sentinel reports the right one.
    return f"{steps}; status=$?; rm -f {REMOTE_FILE}; (exit $status); echo {SENTINEL}"


def run_in_console(argv, line):
    """Type one line into an interactive remote shell; return (exit code, output).

    `doctl apps console` only works attached to a terminal, so a plain pipe is
    not enough: this runs it inside a pseudo-terminal (pty), which looks like a
    real one to the program, and plays the part of the person typing.
    """
    pid, terminal = pty.fork()
    if pid == 0:
        # The child half of the fork: become the console program.
        os.execvp(argv[0], argv)

    output = b""

    def read_until(pattern, timeout, quiet=None):
        """Collect output until `pattern` matches, or it has been quiet a while."""
        nonlocal output
        started = last_output = time.monotonic()
        while time.monotonic() - started < timeout:
            ready, _, _ = select.select([terminal], [], [], 0.25)
            if ready:
                try:
                    chunk = os.read(terminal, 65536)
                except OSError:
                    # Linux reports "the other side closed" as an error here.
                    return None
                if not chunk:
                    return None
                output += chunk
                last_output = time.monotonic()
                match = pattern.search(output) if pattern else None
                if match:
                    return match
            elif quiet and output and time.monotonic() - last_output > quiet:
                return True
        return None

    try:
        # There is no reliable prompt to wait for, so "it printed something and
        # then went quiet" stands in for "the shell is ready for input".
        if not read_until(None, SHELL_READY_TIMEOUT, quiet=SHELL_QUIET_SECONDS):
            return None, output.decode(errors="replace")
        os.write(terminal, line.encode() + b"\n")
        match = read_until(SENTINEL_PATTERN, COMMAND_TIMEOUT)
        try:
            os.write(terminal, b"exit\n")
        except OSError:
            pass
        return (int(match.group(1)) if match else None), output.decode(errors="replace")
    finally:
        os.close(terminal)
        os.waitpid(pid, 0)


def find_app_id():
    """The account's App Platform app, when there is exactly one."""
    listing = subprocess.run(
        ["doctl", "apps", "list", "--format", "ID", "--no-header"],
        capture_output=True, text=True, check=False,
    )
    ids = listing.stdout.split()
    if listing.returncode != 0 or len(ids) != 1:
        raise CommandError(
            "Could not pick the app from `doctl apps list` "
            f"({len(ids)} found). Pass it with --app-id."
        )
    return ids[0]


class Command(BaseCommand):
    help = "Replace the local content tables with a fresh snapshot of production's."

    def add_arguments(self, parser):
        parser.add_argument(
            "--app-id",
            help="The DigitalOcean app id. Default: the only app `doctl apps list` shows.",
        )
        parser.add_argument(
            "--keep", metavar="PATH",
            help="Also save the snapshot file here (reload it later with load_content_snapshot).",
        )

    def handle(self, *args, **options):
        # First, before production is contacted or anything is downloaded.
        require_local_database()
        if not shutil.which("doctl"):
            raise CommandError("doctl is not installed or not on PATH.")
        app_id = options["app_id"] or find_app_id()

        # A random name, so the object cannot be guessed during the minute it
        # exists. It is private as well; the two are belt and braces.
        key = f"snapshots/content-{secrets.token_hex(16)}.json.gz"
        bucket = default_storage.bucket

        self.stdout.write("Dumping content in the production container...")
        try:
            status, transcript = run_in_console(
                ["doctl", "apps", "console", app_id, COMPONENT],
                remote_command_line(key),
            )
            if status != 0:
                reason = "timed out" if status is None else f"exited with {status}"
                raise CommandError(
                    f"The dump in the production console {reason}. Nothing was loaded. "
                    f"Console output:\n{transcript[-3000:]}"
                )

            self.stdout.write("Downloading the snapshot...")
            with tempfile.TemporaryDirectory() as folder:
                # loaddata picks its parser from the file extension.
                path = os.path.join(folder, "content_snapshot.json.gz")
                try:
                    bucket.download_file(key, path)
                except Exception as error:  # botocore raises its own types
                    raise CommandError(
                        f"Could not download the snapshot from the Space: {error}\n"
                        "Check that the DO_SPACES_* values in .env name production's bucket."
                    ) from error
                if options["keep"]:
                    shutil.copyfile(path, options["keep"])
                call_command("load_content_snapshot", path, stdout=self.stdout)
        finally:
            # Runs on every path out, including a failed dump that may still
            # have uploaded. Deleting a key that does not exist is not an error.
            try:
                bucket.Object(key).delete()
            except Exception as error:  # pylint: disable=broad-exception-caught
                self.stderr.write(self.style.WARNING(
                    f"Could not delete {key} from the Space ({error}). Delete it by hand."
                ))
