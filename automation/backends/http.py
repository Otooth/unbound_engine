"""Small standard-library JSON HTTP transport shared by backends."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import JsonObject


class BackendHTTPError(RuntimeError):
    """Raised when a backend HTTP request cannot return a JSON object."""


class JsonRequester(Protocol):
    """Callable shape accepted for injectable JSON HTTP transports."""

    def __call__(
        self,
        url: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float = 60.0,
    ) -> JsonObject:
        """Send one JSON request and return a JSON object."""


def request_json(
    url: str,
    payload: Mapping[str, Any] | None = None,
    *,
    timeout: float = 60.0,
) -> JsonObject:
    """Send a GET or POST and require an object-shaped JSON response.

    Passing ``None`` performs a GET.  Any mapping, including an empty one,
    performs a POST.  Empty response bodies are represented by an empty dict.
    """

    encoded = (
        None
        if payload is None
        else json.dumps(dict(payload)).encode("utf-8")
    )
    request = Request(
        url,
        data=encoded,
        headers=(
            {}
            if encoded is None
            else {"Content-Type": "application/json"}
        ),
        method="GET" if encoded is None else "POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise BackendHTTPError(
            f"{url}: HTTP {error.code}: {detail}"
        ) from error
    except URLError as error:
        raise BackendHTTPError(f"{url}: {error.reason}") from error

    if not body:
        return {}
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackendHTTPError(f"{url}: invalid JSON response") from error
    if not isinstance(parsed, dict):
        raise BackendHTTPError(f"{url}: expected a JSON object")
    return parsed
