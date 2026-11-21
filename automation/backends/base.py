"""Common contracts for local and HTTP video-generation backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Any, TypeAlias


JsonObject: TypeAlias = dict[str, Any]
GenerationRequest: TypeAlias = Mapping[str, Any]
CancelCallback: TypeAlias = Callable[[], bool | None]


class GenerationCancelled(RuntimeError):
    """Raised when a cancellation callback asks a backend to stop."""


class VideoBackend(ABC):
    """Abstract contract implemented by every video backend.

    Backends expose the asynchronous ``submit``/``wait`` pair because it maps
    directly to ComfyUI's prompt API.  ``generate`` is the synchronous
    convenience operation used by the exclusive backend manager.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the stable registry name of this backend."""

    @abstractmethod
    def health(self) -> JsonObject:
        """Return backend-specific health information or raise on failure."""

    @abstractmethod
    def submit(self, request: GenerationRequest) -> str:
        """Submit a generation request and return its backend job handle."""

    @abstractmethod
    def wait(
        self,
        handle: str,
        *,
        cancel_callback: CancelCallback | None = None,
        timeout: float | None = None,
    ) -> JsonObject:
        """Wait for a submitted job and return its backend result."""

    def generate(
        self,
        request: GenerationRequest,
        *,
        cancel_callback: CancelCallback | None = None,
        timeout: float | None = None,
    ) -> JsonObject:
        """Submit and wait for one generation request."""

        handle = self.submit(request)
        return self.wait(
            handle,
            cancel_callback=cancel_callback,
            timeout=timeout,
        )

    @abstractmethod
    def cancel(self, handle: str | None = None) -> None:
        """Cancel the current generation, optionally identified by handle."""

    @abstractmethod
    def release(self) -> None:
        """Unload models and release GPU resources held by this backend."""

    def _check_cancelled(
        self,
        handle: str,
        cancel_callback: CancelCallback | None,
    ) -> None:
        """Evaluate a callback and interrupt the backend when it cancels.

        A callback may either return a truthy value or raise its application's
        own cancellation exception.  The latter form matches
        ``automation.app.raise_if_cancelled`` and is re-raised unchanged.
        """

        if cancel_callback is None:
            return
        try:
            requested = cancel_callback()
        except BaseException as error:
            self._cancel_preserving_error(handle, error)
            raise
        if requested:
            error = GenerationCancelled(
                f"{self.name} generation {handle!r} was cancelled"
            )
            self._cancel_preserving_error(handle, error)
            raise error

    def _cancel_preserving_error(
        self,
        handle: str,
        primary_error: BaseException,
    ) -> None:
        """Cancel without replacing an exception raised by the caller."""

        try:
            self.cancel(handle)
        except Exception as cancel_error:
            primary_error.add_note(
                "The backend cancellation request also failed: "
                f"{type(cancel_error).__name__}: {cancel_error}"
            )
