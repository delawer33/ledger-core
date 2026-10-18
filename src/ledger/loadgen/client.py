"""A minimal keep-alive JSON client; one per worker, so latency is the server's, not connect()."""

import http.client
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

SOURCE_SYSTEM = "loadgen"


@dataclass(frozen=True)
class Reply:
    """One HTTP exchange: `name` labels the operation, `code` is the rule code of an error body."""

    name: str
    status: int
    code: str | None
    latency_ms: float
    body: dict[str, Any]

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class ApiClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        parts = urlsplit(base_url)
        self._prefix = parts.path.rstrip("/")
        self._host = parts.hostname or "127.0.0.1"
        self._port = parts.port or (443 if parts.scheme == "https" else 80)
        self._https = parts.scheme == "https"
        self._timeout = timeout
        self._conn: http.client.HTTPConnection | None = None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
            self._conn = cls(self._host, self._port, timeout=self._timeout)
        return self._conn

    def request(
        self, name: str, method: str, path: str, body: dict[str, Any] | None, key: str | None
    ) -> Reply:
        headers = {"Content-Type": "application/json", "X-Source-System": SOURCE_SYSTEM}
        if key is not None:
            headers["Idempotency-Key"] = key
        payload = json.dumps(body) if body is not None else None
        started = time.perf_counter()
        try:
            conn = self._connection()
            conn.request(method, self._prefix + path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            status = response.status
        except (OSError, http.client.HTTPException):
            # Transport failure (refused, reset, timeout): reported as status 0, connection redone.
            self.close()
            return Reply(name, 0, "transport_error", (time.perf_counter() - started) * 1000, {})
        latency = (time.perf_counter() - started) * 1000
        try:
            parsed = json.loads(raw) if raw else {}
        except ValueError:
            parsed = {}
        data = parsed if isinstance(parsed, dict) else {}
        code = data.get("code") if status >= 400 else None
        return Reply(name, status, str(code) if code is not None else None, latency, data)

    def post(self, name: str, path: str, body: dict[str, Any], key: str) -> Reply:
        return self.request(name, "POST", path, body, key)

    def get(self, name: str, path: str) -> Reply:
        return self.request(name, "GET", path, None, None)
