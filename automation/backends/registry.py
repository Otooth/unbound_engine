"""Thread-safe registration and lazy construction of video backends."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import TypeAlias, overload

from .base import VideoBackend


BackendFactory: TypeAlias = Callable[[], VideoBackend]
BackendProvider: TypeAlias = VideoBackend | BackendFactory


class DuplicateBackendError(ValueError):
    """Raised when registering an existing name without replacement."""


class UnknownBackendError(LookupError):
    """Raised when a requested backend name is not registered."""


class BackendRegistry:
    """Map application engine names to backend objects or lazy factories."""

    def __init__(
        self,
        providers: Mapping[str, BackendProvider] | None = None,
    ) -> None:
        """Create a registry and optionally preload named providers."""

        self._providers: dict[str, BackendProvider] = {}
        self._instances: dict[str, VideoBackend] = {}
        self._lock = threading.RLock()
        if providers is not None:
            for name, provider in providers.items():
                self.register(name, provider)

    @overload
    def register(
        self,
        name_or_backend: VideoBackend,
        provider: None = None,
        *,
        replace: bool = False,
    ) -> None:
        ...

    @overload
    def register(
        self,
        name_or_backend: str,
        provider: BackendProvider,
        *,
        replace: bool = False,
    ) -> None:
        ...

    def register(
        self,
        name_or_backend: str | VideoBackend,
        provider: BackendProvider | None = None,
        *,
        replace: bool = False,
    ) -> None:
        """Register a backend instance or a lazy factory.

        ``register(backend)`` uses ``backend.name``.  The two-argument form
        permits application engine aliases such as ``"wan5b"`` and
        ``"wan14"`` to select separately configured backend instances.
        """

        if isinstance(name_or_backend, str):
            name = self._normalize_name(name_or_backend)
            if provider is None:
                raise TypeError("a provider is required for a named backend")
            selected_provider = provider
        else:
            if provider is not None:
                raise TypeError(
                    "provider must be omitted when registering an instance"
                )
            selected_provider = self._validate_backend(name_or_backend)
            name = self._normalize_name(selected_provider.name)

        if not isinstance(selected_provider, VideoBackend) and not callable(
            selected_provider
        ):
            raise TypeError(
                "backend provider must be a VideoBackend or zero-argument "
                "factory"
            )

        with self._lock:
            if name in self._providers and not replace:
                raise DuplicateBackendError(
                    f"backend {name!r} is already registered"
                )
            self._providers[name] = selected_provider
            self._instances.pop(name, None)
            if isinstance(selected_provider, VideoBackend):
                self._instances[name] = selected_provider

    def get(self, name: str) -> VideoBackend:
        """Return a backend, constructing a lazy provider at most once."""

        normalized_name = self._normalize_name(name)
        with self._lock:
            instance = self._instances.get(normalized_name)
            if instance is not None:
                return instance
            try:
                provider = self._providers[normalized_name]
            except KeyError as error:
                raise UnknownBackendError(
                    f"backend {normalized_name!r} is not registered"
                ) from error
            if isinstance(provider, VideoBackend):
                instance = provider
            else:
                instance = self._validate_backend(provider())
            self._instances[normalized_name] = instance
            return instance

    def names(self) -> tuple[str, ...]:
        """Return registered names in sorted order."""

        with self._lock:
            return tuple(sorted(self._providers))

    def __contains__(self, name: object) -> bool:
        """Return whether a string name is registered."""

        if not isinstance(name, str):
            return False
        with self._lock:
            return name.strip() in self._providers

    def __getitem__(self, name: str) -> VideoBackend:
        """Resolve ``registry[name]`` through :meth:`get`."""

        return self.get(name)

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Validate and normalize a registry key."""

        normalized = name.strip()
        if not normalized:
            raise ValueError("backend name must not be empty")
        return normalized

    @staticmethod
    def _validate_backend(candidate: object) -> VideoBackend:
        """Require factories and direct values to honor the abstract contract."""

        if not isinstance(candidate, VideoBackend):
            raise TypeError("backend factory did not return a VideoBackend")
        if not candidate.name.strip():
            raise ValueError("backend name must not be empty")
        return candidate
