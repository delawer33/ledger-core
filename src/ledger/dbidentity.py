"""Which database a process is connected to; compared by the load generator against the service."""

import hashlib

from django.db import connection


def database_identity() -> str:
    """Stable id of the connected database: cluster system identifier plus database name and oid.

    Hashed, so /health does not publish the raw cluster identifier. Two databases in one
    cluster differ by name/oid; two clusters differ by system identifier.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT (SELECT system_identifier FROM pg_control_system()), datname, oid "
            "FROM pg_database WHERE datname = current_database()"
        )
        row = cursor.fetchone()
    raw = ":".join(str(part) for part in row) if row else "unknown"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
