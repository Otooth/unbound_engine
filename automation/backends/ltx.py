"""Configurable HTTP adapter for a future standalone LTX service."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from urllib.parse import quote

from .base import (
    CancelCallback,
    GenerationRequest,
    JsonObject,
    VideoBackend,
)
from .http import JsonRequester, request_json


class LTXHTTPBackend(VideoBackend):
    """Drive an asynchronous LTX HTTP service.

    The standalone service is not fixed by this project yet.  Endpoint paths
    are therefore configurable.  The default contract expects ``POST /jobs``
    to return ``job_id`` or ``id``, and ``GET /jobs/{handle}`` to return a
    top-level ``status`` field.
    """

    def __init__(
        self,
        base_url: str,
        *,
        name: str = "ltx",
        health_path: str = "/health",
        submit_path: str = "/jobs",
        status_path: str = "/jobs/{handle}",
        cancel_path: str = "/jobs/{handle}/cancel",
        release_path: str = "/release",
        success_statuses: Iterable[str] = ("succeeded", "success", "completed"),
        failure_statuses: Iterable[str] = ("failed", "error", "cancelled"),
        wait_timeout: float = 7200.0,
        poll_interval: float = 2.0,
        http_timeout: float = 60.0,
        requester: JsonRequester = request_json,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Configure routes and polling for a standalone LTX server."""

        normalized_name = name.strip()
        normalized_url = base_url.rstrip("/")
        if not normalized_name:
            raise ValueError("backend name must not be empty")
        if not normalized_url:
            raise ValueError("base_url must not be empty")
        if wait_timeout <= 0:
            raise ValueError("wait_timeout must be positive")
        if poll_interval < 0:
            raise ValueError("poll_interval must not be negative")
        if http_timeout <= 0:
            raise ValueError("http_timeout must be positive")
        paths = (
            health_path,
            submit_path,
            status_path,
            cancel_path,
            release_path,
        )
        if any(not path.startswith("/") for path in paths):
            raise ValueError("LTX endpoint paths must start with '/'")

        self._name = normalized_name
        self._base_url = normalized_url
        self._health_path = health_path
        self._submit_path = submit_path
        self._status_path = status_path
        self._cancel_path = cancel_path
        self._release_path = release_path
        self._success_statuses = frozenset(
            status.lower() for status in success_statuses
        )
        self._failure_statuses = frozenset(
            status.lower() for status in failure_statuses
        )
        if not self._success_statuses:
            raise ValueError("success_statuses must not be empty")
        if not self._failure_statuses:
            raise ValueError("failure_statuses must not be empty")
        if self._success_statuses & self._failure_statuses:
            raise ValueError("success and failure statuses must not overlap")
        self._wait_timeout = wait_timeout
        self._poll_interval = poll_interval
        self._http_timeout = http_timeout
        self._requester = requester
        self._monotonic = monotonic
        self._sleep = sleep

    @property
    def name(self) -> str:
        """Return this client's registry name."""

        return self._name

    def health(self) -> JsonObject:
        """Return the configured LTX health endpoint response."""

        return self._requester(
            self._url(self._health_path),
            timeout=self._http_timeout,
        )

    def submit(self, request: GenerationRequest) -> str:
        """Submit an LTX job and return the server's job ID."""

        response = self._requester(
            self._url(self._submit_path),
            request,
            timeout=self._http_timeout,
        )
        handle = response.get("job_id", response.get("id"))
        if not isinstance(handle, str) or not handle:
            raise RuntimeError(f"LTX service returned no job ID: {response}")
        return handle

    def wait(
        self,
        handle: str,
        *,
        cancel_callback: CancelCallback | None = None,
        timeout: float | None = None,
    ) -> JsonObject:
        """Poll the configured status endpoint until the LTX job terminates."""

        if not handle:
            raise ValueError("LTX job handle must not be empty")
        wait_timeout = self._wait_timeout if timeout is None else timeout
        if wait_timeout <= 0:
            raise ValueError("timeout must be positive")

        deadline = self._monotonic() + wait_timeout
        status_path = self._format_handle_path(self._status_path, handle)
        while True:
            self._check_cancelled(handle, cancel_callback)
            response = self._requester(
                self._url(status_path),
                timeout=self._http_timeout,
            )
            raw_status = response.get("status")
            status = (
                raw_status.lower()
                if isinstance(raw_status, str)
                else ""
            )
            if status in self._success_statuses:
                return response
            if status in self._failure_statuses:
                detail = response.get("error", response)
                raise RuntimeError(
                    f"LTX generation {handle!r} ended as {status}: {detail}"
                )

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            self._sleep(min(self._poll_interval, remaining))
        raise TimeoutError(f"LTX generation {handle!r} timed out")

    def cancel(self, handle: str | None = None) -> None:
        """Request cancellation of one LTX job."""

        if not handle:
            raise ValueError("LTX cancellation requires a job handle")
        path = self._format_handle_path(self._cancel_path, handle)
        self._requester(
            self._url(path),
            {},
            timeout=self._http_timeout,
        )

    def release(self) -> None:
        """Ask the LTX service to unload its active model."""

        self._requester(
            self._url(self._release_path),
            {},
            timeout=self._http_timeout,
        )

    def _url(self, path: str) -> str:
        """Join a validated absolute endpoint path to the service URL."""

        return f"{self._base_url}{path}"

    @staticmethod
    def _format_handle_path(template: str, handle: str) -> str:
        """Substitute a URL-escaped job handle into an endpoint template."""

        if "{handle}" not in template:
            raise ValueError("LTX job endpoint must contain '{handle}'")
        return template.format(handle=quote(handle, safe=""))
