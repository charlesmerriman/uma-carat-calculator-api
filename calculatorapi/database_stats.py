"""
The production database's numbers about itself, for Oguri Bot
(notes/server-diagnostics-plan.md, item 10).

Postgres keeps running statistics in a few system views. Three cheap reads of
them, each a single small query:

* `activity()`: the connections open right now, by state, and how many are
  waiting on a lock. In GET /bot/health, so the slow-site alert can say
  whether the database is the queue.
* `overview()`: the database's size, how often a read came from memory rather
  than disk (the cache hit rate), deadlocks and temporary files, all counted
  since the statistics last started. For the weekly post.
* `top_queries()`: from `pg_stat_statements`, the queries that took the most
  total time. For the weekly post. None while that extension isn't installed:
  it needs the database's admin user once (`CREATE EXTENSION
  pg_stat_statements`), which the app's own user can't do.

All three return None on SQLite (local development and the tests), which has
none of these views.

WHAT IT GIVES AWAY: aggregates and query SHAPES only. `pg_stat_statements`
replaces every literal in a query with $1, $2..., so its text holds table and
column names, never a value somebody typed or a row's id. The app's database
user isn't a member of `pg_read_all_stats`, so it sees the text of its own
queries only: the site's, nothing of DigitalOcean's maintenance.
"""

import logging
import re

from django.db import DatabaseError, connection, transaction

logger = logging.getLogger(__name__)

# How much of a query's text the weekly post shows: enough to recognise it.
QUERY_TEXT_LIMIT = 200


def _postgres():
    return connection.vendor == "postgresql"


def activity():
    """{connections: {state: count}, waiting_on_locks} for this database,
    leaving out the connection asking, or None off Postgres."""
    if not _postgres():
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT coalesce(state, 'unknown'), count(*),
                   count(*) FILTER (WHERE wait_event_type = 'Lock')
            FROM pg_stat_activity
            WHERE datname = current_database() AND pid <> pg_backend_pid()
            GROUP BY 1
            """
        )
        rows = cursor.fetchall()
    return {
        "connections": {state: count for state, count, _ in rows},
        "waiting_on_locks": sum(waiting for _, _, waiting in rows),
    }


def overview():
    """The database's size and its running totals, or None off Postgres.
    `since` is when the totals last started (None: since the server began
    keeping them)."""
    if not _postgres():
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT pg_database_size(current_database()), blks_hit, blks_read,
                   deadlocks, temp_files, temp_bytes, stats_reset
            FROM pg_stat_database WHERE datname = current_database()
            """
        )
        size, hits, reads, deadlocks, temp_files, temp_bytes, since = cursor.fetchone()
    return {
        "size_bytes": size,
        "cache_hit_pct": cache_hit_pct(hits, reads),
        "deadlocks": deadlocks,
        "temp_files": temp_files,
        "temp_bytes": temp_bytes,
        "since": since.isoformat() if since else None,
    }


def cache_hit_pct(hits, reads):
    """The share of block reads served from memory, as a percentage to two
    places, or None before the first read."""
    total = hits + reads
    return round(100 * hits / total, 2) if total else None


def top_queries(limit=5):
    """{since, queries: [{query, calls, total_ms, mean_ms, rows}]}, the queries
    that took the most total time, or None off Postgres or while
    pg_stat_statements isn't installed (or can't be read)."""
    if not _postgres():
        return None
    try:
        # A savepoint: if the read fails (the extension installed but not
        # preloaded raises), the request's transaction carries on unharmed.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'")
            if cursor.fetchone() is None:
                return None
            cursor.execute("SELECT stats_reset FROM pg_stat_statements_info")
            (since,) = cursor.fetchone()
            cursor.execute(
                """
                SELECT query, calls, total_exec_time, mean_exec_time, rows
                FROM pg_stat_statements
                WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                  AND query NOT LIKE '%%pg_stat_statements%%'
                ORDER BY total_exec_time DESC
                LIMIT %s
                """,
                [limit],
            )
            rows = cursor.fetchall()
    except DatabaseError:
        logger.warning("pg_stat_statements is installed but couldn't be read", exc_info=True)
        return None
    return {
        "since": since.isoformat() if since else None,
        "queries": [
            {
                "query": query_text(query),
                "calls": calls,
                "total_ms": round(total_ms),
                "mean_ms": round(mean_ms, 1),
                "rows": row_count,
            }
            for query, calls, total_ms, mean_ms, row_count in rows
        ],
    }


def query_text(query):
    """A query as the weekly post shows it: on one line, cut to QUERY_TEXT_LIMIT."""
    flat = re.sub(r"\s+", " ", query or "").strip()
    return flat if len(flat) <= QUERY_TEXT_LIMIT else flat[: QUERY_TEXT_LIMIT - 1] + "…"
