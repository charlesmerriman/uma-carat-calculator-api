# pylint: disable=invalid-name
# (gunicorn reads these lowercase names; UPPER_CASE would be ignored)
"""gunicorn settings for the production service.

gunicorn reads this file from the working directory on start, so the
run_command in .do/app.yaml stays a bare
``gunicorn calculatorproject.wsgi:application --bind 0.0.0.0:8080``.

ONE process, several threads -- and that split is load-bearing.
calculatorapi/public_payload_cache.py keeps the rendered /calculator-data
catalogue in Django's local-memory cache, which lives inside one process. A
second WORKER would carry its own copy, and an admin edit would invalidate only
the copy in the process that handled it. Threads share the process, so they
share the cache; LocMemCache takes a lock around every read and write.

Why threads at all: the default sync worker serves ONE request at a time. On
2026-09-23 a new banner brought a crowd in at once and the queue behind that
one request grew past what any browser waits for (see the cache module for the
rebuild bug that made each request slow in the first place). Most of a
request's wall time here is waiting -- on Postgres, and on sending a megabyte
of JSON back through the ingress -- and threads overlap that even under the
GIL. Four is a modest number for the 0.5 GB instance; raise it before adding a
worker, and if a worker ever becomes necessary move CACHES onto Redis first.
"""

workers = 1
threads = 4          # implies worker_class = "gthread"
worker_class = "gthread"

# A catalogue rebuild is ~2s and a signed-in save is well under that; the
# default 30s is plenty, and a thread wedged on a dead DB connection should be
# recycled rather than kept.
timeout = 30
