"""Exclusive, process-local ownership of GPU video backends."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from .base import (
    CancelCallback,
    GenerationRequest,
    JsonObject,
    VideoBackend,
)
from .registry import BackendRegistry


class ExclusiveBackendManager:
    """Keep at most one backend's model active on the GPU.

    Generation operations are serialized.  Switching backend objects releases
    the old object before publishing the new one.  Re-selecting the same
    object is a no-op, so a loaded model can be reused across jobs.

    Any exception escaping :meth:`use` or :meth:`generate` detaches and
    releases that backend.  This intentionally leaves ``active_name`` as
    ``None`` rather than claiming an uncertain GPU state.
    """

    def __init__(self, registry: BackendRegistry) -> None:
        """Create a manager for an already configured backend registry."""

        self._registry = registry
        self._operation_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._active_name: str | None = None
        self._active_backend: VideoBackend | None = None
        self._active_handle: str | None = None
        self._busy = False

    @property
    def registry(self) -> BackendRegistry:
        """Return the backing registry."""

        return self._registry

    @property
    def active_name(self) -> str | None:
        """Return the selected registry name, if any."""

        with self._state_lock:
            return self._active_name

    @property
    def active_backend(self) -> VideoBackend | None:
        """Return the selected backend object, if any."""

        with self._state_lock:
            return self._active_backend

    @property
    def active_handle(self) -> str | None:
        """Return the currently waiting job handle, if any."""

        with self._state_lock:
            return self._active_handle

    @property
    def busy(self) -> bool:
        """Return whether a managed operation currently owns the GPU slot."""

        with self._state_lock:
            return self._busy

    def health(self, name: str) -> JsonObject:
        """Query health without changing the active GPU backend."""

        return self._registry.get(name).health()

    def activate(self, name: str) -> VideoBackend:
        """Select a backend, releasing a different active backend first.

        This method performs only the transition.  Callers that use the
        returned object for work should prefer :meth:`use` so ownership stays
        serialized for the complete operation.
        """

        with self._operation_lock:
            return self._activate_locked(name)

    @contextmanager
    def use(self, name: str) -> Iterator[VideoBackend]:
        """Yield one backend while holding exclusive generation ownership."""

        backend: VideoBackend | None = None
        with self._operation_lock:
            try:
                backend = self._activate_locked(name)
                with self._state_lock:
                    self._busy = True
                yield backend
            except BaseException as error:
                if backend is not None:
                    self._invalidate_backend(backend, error)
                raise
            finally:
                with self._state_lock:
                    self._busy = False

    def generate(
        self,
        name: str,
        request: GenerationRequest,
        *,
        cancel_callback: CancelCallback | None = None,
        timeout: float | None = None,
    ) -> JsonObject:
        """Run one serialized submit/wait operation on a named backend."""

        with self.use(name) as backend:
            handle = backend.submit(request)
            if not isinstance(handle, str) or not handle:
                raise RuntimeError(
                    f"backend {backend.name!r} returned an invalid handle"
                )
            with self._state_lock:
                self._active_handle = handle
            try:
                return backend.wait(
                    handle,
                    cancel_callback=cancel_callback,
                    timeout=timeout,
                )
            finally:
                with self._state_lock:
                    if self._active_handle == handle:
                        self._active_handle = None

    def cancel(self, handle: str | None = None) -> bool:
        """Cancel a managed job without waiting for the generation lock.

        If ``handle`` is omitted, the current handle recorded by
        :meth:`generate` is used.  ``False`` means there was no cancellable
        managed job.
        """

        with self._state_lock:
            backend = self._active_backend
            selected_handle = (
                self._active_handle if handle is None else handle
            )
        if backend is None or selected_handle is None:
            return False
        backend.cancel(selected_handle)
        return True

    def release(self) -> bool:
        """Release the active backend and clear manager state.

        State is cleared before calling the backend.  Therefore a release
        failure is visible to the caller while the manager remains safely
        inactive.  ``False`` means no backend was active.
        """

        with self._operation_lock:
            with self._state_lock:
                backend = self._active_backend
                self._clear_active_locked()
            if backend is None:
                return False
            backend.release()
            return True

    def close(self) -> None:
        """Release resources, ignoring whether a backend was active."""

        self.release()

    def __enter__(self) -> ExclusiveBackendManager:
        """Return this manager for context-managed application lifetimes."""

        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> bool:
        """Release the active backend when leaving an application lifetime."""

        del exception_type, traceback
        if exception is None:
            self.release()
        else:
            try:
                self.release()
            except Exception as release_error:
                exception.add_note(
                    "Backend release during manager shutdown also failed: "
                    f"{type(release_error).__name__}: {release_error}"
                )
        return False

    def _activate_locked(self, name: str) -> VideoBackend:
        """Transition backend state while the operation lock is held."""

        target = self._registry.get(name)
        normalized_name = name.strip()
        with self._state_lock:
            current = self._active_backend
            if current is target:
                self._active_name = normalized_name
                return target
            self._clear_active_locked()

        if current is not None:
            current.release()

        with self._state_lock:
            self._active_name = normalized_name
            self._active_backend = target
        return target

    def _invalidate_backend(
        self,
        backend: VideoBackend,
        primary_error: BaseException,
    ) -> None:
        """Detach and release a backend without masking an operation error."""

        with self._state_lock:
            if self._active_backend is not backend:
                return
            self._clear_active_locked()
        try:
            backend.release()
        except Exception as release_error:
            primary_error.add_note(
                "Backend release after failure also failed: "
                f"{type(release_error).__name__}: {release_error}"
            )

    def _clear_active_locked(self) -> None:
        """Clear active state while the state lock is held."""

        self._active_name = None
        self._active_backend = None
        self._active_handle = None
