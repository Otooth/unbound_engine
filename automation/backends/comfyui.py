"""ComfyUI implementation of the video backend contract."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import quote

from .base import (
    CancelCallback,
    GenerationRequest,
    JsonObject,
    VideoBackend,
)
from .http import JsonRequester, request_json


class ComfyUIBackend(VideoBackend):
    """Drive ComfyUI through its prompt, history, and memory APIs."""

    _TERMINAL_STATUSES = frozenset({"success", "error"})

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        *,
        name: str = "comfyui",
        wait_timeout: float = 7200.0,
        poll_interval: float = 2.0,
        http_timeout: float = 60.0,
        requester: JsonRequester = request_json,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Configure a ComfyUI client.

        ``requester``, ``monotonic``, and ``sleep`` are injectable so the
        transport and polling behavior can be tested without network access.
        """

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
        self._name = normalized_name
        self._base_url = normalized_url
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
        """Return ComfyUI's system statistics."""

        return self._requester(
            f"{self._base_url}/system_stats",
            timeout=self._http_timeout,
        )

    def submit(self, request: GenerationRequest) -> str:
        """Queue a ComfyUI workflow and return its prompt ID."""

        response = self._requester(
            f"{self._base_url}/prompt",
            {"prompt": dict(request)},
            timeout=self._http_timeout,
        )
        prompt_id = response.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise RuntimeError(f"ComfyUI rejected workflow: {response}")
        return prompt_id

    def wait(
        self,
        handle: str,
        *,
        cancel_callback: CancelCallback | None = None,
        timeout: float | None = None,
    ) -> JsonObject:
        """Poll ComfyUI history until a prompt succeeds, fails, or times out."""

        if not handle:
            raise ValueError("ComfyUI prompt handle must not be empty")
        wait_timeout = self._wait_timeout if timeout is None else timeout
        if wait_timeout <= 0:
            raise ValueError("timeout must be positive")

        deadline = self._monotonic() + wait_timeout
        encoded_handle = quote(handle, safe="")
        while True:
            self._check_cancelled(handle, cancel_callback)
            response = self._requester(
                f"{self._base_url}/history/{encoded_handle}",
                timeout=self._http_timeout,
            )
            history = response.get(handle)
            if isinstance(history, dict):
                status = history.get("status")
                if isinstance(status, dict):
                    status_name = status.get("status_str")
                    completed = status.get("completed") is True
                    if (
                        completed
                        or status_name in self._TERMINAL_STATUSES
                    ):
                        if status_name != "success":
                            raise RuntimeError(
                                "ComfyUI generation failed: "
                                f"{status}"
                            )
                        return history

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            self._sleep(min(self._poll_interval, remaining))
        raise TimeoutError(f"ComfyUI prompt {handle} timed out")

    def cancel(self, handle: str | None = None) -> None:
        """Interrupt ComfyUI's currently executing prompt.

        ComfyUI's interrupt endpoint is process-wide, so ``handle`` is
        accepted for contract compatibility but is not sent to the server.
        """

        del handle
        self._requester(
            f"{self._base_url}/interrupt",
            {},
            timeout=self._http_timeout,
        )

    def release(self) -> None:
        """Unload ComfyUI models and ask it to free cached GPU memory."""

        self._requester(
            f"{self._base_url}/free",
            {"unload_models": True, "free_memory": True},
            timeout=self._http_timeout,
        )
