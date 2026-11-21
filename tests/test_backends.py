"""Unit tests for backend contracts and exclusive GPU ownership."""

from __future__ import annotations

import threading
import unittest
from collections.abc import Mapping
from typing import Any

from automation.backends import (
    BackendRegistry,
    ComfyUIBackend,
    DuplicateBackendError,
    ExclusiveBackendManager,
    GenerationCancelled,
    LTXHTTPBackend,
    UnknownBackendError,
    VideoBackend,
)


class FakeBackend(VideoBackend):
    """In-memory backend used to test lifecycle behavior without I/O."""

    def __init__(
        self,
        name: str,
        *,
        wait_error: Exception | None = None,
        release_error: Exception | None = None,
        block_until_cancelled: bool = False,
    ) -> None:
        self._name = name
        self.wait_error = wait_error
        self.release_error = release_error
        self.block_until_cancelled = block_until_cancelled
        self.submissions: list[dict[str, Any]] = []
        self.waited_handles: list[str] = []
        self.cancelled_handles: list[str | None] = []
        self.release_calls = 0
        self.wait_started = threading.Event()
        self.cancelled = threading.Event()

    @property
    def name(self) -> str:
        """Return the fake backend name."""

        return self._name

    def health(self) -> dict[str, Any]:
        """Return a deterministic healthy response."""

        return {"ok": True, "name": self.name}

    def submit(self, request: Mapping[str, Any]) -> str:
        """Record a request and return a deterministic handle."""

        self.submissions.append(dict(request))
        return f"{self.name}-{len(self.submissions)}"

    def wait(
        self,
        handle: str,
        *,
        cancel_callback=None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Return a result, fail, or block until ``cancel`` is called."""

        del timeout
        self.waited_handles.append(handle)
        self.wait_started.set()
        if self.block_until_cancelled:
            if not self.cancelled.wait(2.0):
                raise TimeoutError("test backend was not cancelled")
            raise GenerationCancelled(f"{handle} cancelled")
        self._check_cancelled(handle, cancel_callback)
        if self.wait_error is not None:
            raise self.wait_error
        return {"handle": handle, "status": "succeeded"}

    def cancel(self, handle: str | None = None) -> None:
        """Record cancellation and unblock a waiting fake generation."""

        self.cancelled_handles.append(handle)
        self.cancelled.set()

    def release(self) -> None:
        """Record release and optionally emulate a release failure."""

        self.release_calls += 1
        if self.release_error is not None:
            raise self.release_error


class BackendRegistryTests(unittest.TestCase):
    """Verify registry lookup and lazy instance reuse."""

    def test_lazy_factory_is_built_once(self) -> None:
        builds: list[FakeBackend] = []

        def factory() -> VideoBackend:
            backend = FakeBackend("lazy")
            builds.append(backend)
            return backend

        registry = BackendRegistry({"engine": factory})

        self.assertIs(registry.get("engine"), registry.get("engine"))
        self.assertEqual(len(builds), 1)
        self.assertEqual(registry.names(), ("engine",))

    def test_duplicate_and_unknown_names_are_explicit(self) -> None:
        registry = BackendRegistry()
        registry.register(FakeBackend("fake"))

        with self.assertRaises(DuplicateBackendError):
            registry.register(FakeBackend("fake"))
        with self.assertRaises(UnknownBackendError):
            registry.get("missing")


class ExclusiveBackendManagerTests(unittest.TestCase):
    """Verify model switching, reuse, failure cleanup, and cancellation."""

    def test_switch_releases_old_backend(self) -> None:
        first = FakeBackend("first")
        second = FakeBackend("second")
        manager = ExclusiveBackendManager(
            BackendRegistry({"first": first, "second": second})
        )

        manager.activate("first")
        selected = manager.activate("second")

        self.assertIs(selected, second)
        self.assertEqual(first.release_calls, 1)
        self.assertEqual(second.release_calls, 0)
        self.assertEqual(manager.active_name, "second")

    def test_same_backend_is_reused(self) -> None:
        backend = FakeBackend("shared")
        manager = ExclusiveBackendManager(
            BackendRegistry({"shared": backend})
        )

        first = manager.activate("shared")
        second = manager.activate("shared")

        self.assertIs(first, second)
        self.assertEqual(backend.release_calls, 0)
        self.assertTrue(manager.release())
        self.assertEqual(backend.release_calls, 1)

    def test_generation_exception_clears_uncertain_state(self) -> None:
        backend = FakeBackend(
            "failing",
            wait_error=RuntimeError("generation failed"),
        )
        manager = ExclusiveBackendManager(
            BackendRegistry({"failing": backend})
        )

        with self.assertRaisesRegex(RuntimeError, "generation failed"):
            manager.generate("failing", {"prompt": "safe test scene"})

        self.assertIsNone(manager.active_name)
        self.assertIsNone(manager.active_backend)
        self.assertIsNone(manager.active_handle)
        self.assertFalse(manager.busy)
        self.assertEqual(backend.release_calls, 1)

    def test_release_exception_still_leaves_manager_inactive(self) -> None:
        failing = FakeBackend(
            "failing-release",
            release_error=RuntimeError("release failed"),
        )
        replacement = FakeBackend("replacement")
        manager = ExclusiveBackendManager(
            BackendRegistry(
                {
                    "failing": failing,
                    "replacement": replacement,
                }
            )
        )
        manager.activate("failing")

        with self.assertRaisesRegex(RuntimeError, "release failed"):
            manager.activate("replacement")

        self.assertIsNone(manager.active_name)
        self.assertIsNone(manager.active_backend)
        self.assertEqual(failing.release_calls, 1)
        self.assertEqual(replacement.release_calls, 0)

    def test_cancel_targets_the_running_handle(self) -> None:
        backend = FakeBackend("blocking", block_until_cancelled=True)
        manager = ExclusiveBackendManager(
            BackendRegistry({"blocking": backend})
        )
        errors: list[BaseException] = []

        def generate() -> None:
            try:
                manager.generate("blocking", {"prompt": "safe test scene"})
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=generate)
        worker.start()
        self.assertTrue(backend.wait_started.wait(1.0))

        self.assertTrue(manager.cancel())
        worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(backend.cancelled_handles, ["blocking-1"])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], GenerationCancelled)
        self.assertIsNone(manager.active_name)
        self.assertFalse(manager.busy)
        self.assertEqual(backend.release_calls, 1)


class HTTPBackendTests(unittest.TestCase):
    """Exercise HTTP adapters with injected transports and no network."""

    def test_comfyui_uses_existing_prompt_history_and_free_api(self) -> None:
        calls: list[tuple[str, Mapping[str, Any] | None]] = []

        def requester(
            url: str,
            payload: Mapping[str, Any] | None = None,
            *,
            timeout: float = 60.0,
        ) -> dict[str, Any]:
            del timeout
            calls.append((url, payload))
            if url.endswith("/prompt"):
                return {"prompt_id": "prompt-1"}
            if url.endswith("/history/prompt-1"):
                return {
                    "prompt-1": {
                        "status": {
                            "completed": True,
                            "status_str": "success",
                        },
                        "outputs": {"save": {"videos": []}},
                    }
                }
            return {}

        backend = ComfyUIBackend(requester=requester)
        result = backend.generate({"node": {"class_type": "Test"}})
        backend.release()

        self.assertEqual(result["status"]["status_str"], "success")
        self.assertEqual(calls[0][0], "http://127.0.0.1:8188/prompt")
        self.assertEqual(
            calls[0][1],
            {"prompt": {"node": {"class_type": "Test"}}},
        )
        self.assertEqual(calls[-1][0], "http://127.0.0.1:8188/free")
        self.assertEqual(
            calls[-1][1],
            {"unload_models": True, "free_memory": True},
        )

    def test_comfyui_cancel_callback_interrupts_before_polling(self) -> None:
        urls: list[str] = []

        def requester(
            url: str,
            payload: Mapping[str, Any] | None = None,
            *,
            timeout: float = 60.0,
        ) -> dict[str, Any]:
            del payload, timeout
            urls.append(url)
            return {}

        backend = ComfyUIBackend(requester=requester)

        with self.assertRaises(GenerationCancelled):
            backend.wait("prompt-1", cancel_callback=lambda: True)

        self.assertEqual(urls, ["http://127.0.0.1:8188/interrupt"])

    def test_ltx_routes_are_configurable_and_handles_are_escaped(self) -> None:
        calls: list[tuple[str, Mapping[str, Any] | None]] = []

        def requester(
            url: str,
            payload: Mapping[str, Any] | None = None,
            *,
            timeout: float = 60.0,
        ) -> dict[str, Any]:
            del timeout
            calls.append((url, payload))
            if url.endswith("/v1/generate"):
                return {"job_id": "job/1"}
            if url.endswith("/v1/jobs/job%2F1"):
                return {"status": "succeeded", "video": "output.mp4"}
            return {}

        backend = LTXHTTPBackend(
            "http://127.0.0.1:9000",
            submit_path="/v1/generate",
            status_path="/v1/jobs/{handle}",
            cancel_path="/v1/jobs/{handle}/cancel",
            requester=requester,
        )

        result = backend.generate({"prompt": "safe test scene"})

        self.assertEqual(result["video"], "output.mp4")
        self.assertEqual(
            calls[1][0],
            "http://127.0.0.1:9000/v1/jobs/job%2F1",
        )


if __name__ == "__main__":
    unittest.main()
