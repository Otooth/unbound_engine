from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from automation import app


class CorrectivePromptTests(unittest.TestCase):
    def test_measurement_messages_never_enter_generation_prompt(self) -> None:
        report = app.aggregate_reports(
            [
                app.evaluate_boundary_metrics(0.678, 8.73),
                app.QualityReport(
                    gate="temporal",
                    issues=(
                        app.QualityIssue(
                            code="temporal.frozen_video",
                            message="The video contains a frozen interval",
                            gate="temporal",
                        ),
                    ),
                ),
            ]
        )

        prompt = app.quality_corrective_prompt(report)

        self.assertNotIn("SSIM", prompt)
        self.assertNotIn("PSNR", prompt)
        self.assertIn("visibly moving", prompt)


class ProfileTests(unittest.TestCase):
    @staticmethod
    def _wan14_workflow(profile: str) -> dict[str, object]:
        workflow = json.loads(
            app.ENGINE_CONFIG["wan14"]["workflow"].read_text()
        )
        for node_id, values in app.ENGINE_CONFIG["wan14"][
            "profile_overrides"
        ][profile].items():
            workflow[node_id]["inputs"].update(copy.deepcopy(values))
        return workflow

    def test_wan14_defaults_to_quality(self) -> None:
        request = app.JobCreate(
            concept="A person waves slowly in a quiet studio.",
            engine="wan14",
            input_image="person.png",
        )
        self.assertEqual(
            app.resolve_generation_profile(request),
            ("quality", "comfy"),
        )

    def test_wan14_fast_is_explicit(self) -> None:
        request = app.JobCreate(
            concept="A person waves slowly in a quiet studio.",
            engine="wan14",
            input_image="person.png",
            generation_profile="fast",
        )
        self.assertEqual(
            app.resolve_generation_profile(request),
            ("fast", "comfy"),
        )

    def test_wan14_profile_switches_select_exact_official_paths(self) -> None:
        quality = self._wan14_workflow("quality")
        fast = self._wan14_workflow("fast")

        self.assertIs(quality["129:131"]["inputs"]["value"], False)
        self.assertEqual(quality["129:128"]["inputs"]["value"], 20)
        self.assertEqual(quality["129:126"]["inputs"]["value"], 3.5)
        self.assertEqual(quality["129:116"]["inputs"]["on_false"], ["129:95", 0])
        self.assertEqual(quality["129:117"]["inputs"]["on_false"], ["129:96", 0])

        self.assertIs(fast["129:131"]["inputs"]["value"], True)
        self.assertEqual(fast["129:118"]["inputs"]["value"], 4)
        self.assertEqual(fast["129:122"]["inputs"]["value"], 1)
        self.assertEqual(fast["129:116"]["inputs"]["on_true"], ["129:101", 0])
        self.assertEqual(fast["129:117"]["inputs"]["on_true"], ["129:102", 0])

    def test_native_ltx_uses_ltx_backend(self) -> None:
        request = app.JobCreate(
            concept="A paper boat crosses a calm pond at sunrise.",
            engine="ltx25",
            input_image="boat.png",
            generation_profile="dfr",
            ltx_license_acknowledged=True,
        )
        self.assertEqual(
            app.resolve_generation_profile(request),
            ("dfr", "ltx"),
        )

    def test_planning_unloads_comfy_and_ltx_before_ollama(self) -> None:
        manager = SimpleNamespace(active_name="ltx", release=Mock())
        ltx_backend = SimpleNamespace(release=Mock())
        with (
            patch.object(app, "BACKEND_MANAGER", manager),
            patch.object(app, "LTX_BACKEND", ltx_backend),
            patch.object(app, "free_comfy") as free_comfy,
        ):
            app.release_video_models_for_planning()

        free_comfy.assert_called_once_with()
        manager.release.assert_called_once_with()
        ltx_backend.release.assert_not_called()


class PlanAuditTests(unittest.TestCase):
    @staticmethod
    def _plan() -> dict[str, object]:
        return {
            "title": "Wave",
            "scene": "A person stands still before waving.",
            "camera_view": "front",
            "steps": ["The person raises one hand"],
            "result_states": ["The person holds one raised hand"],
        }

    @staticmethod
    def _failed_audit() -> dict[str, object]:
        return {
            "missing": ["the requested wave"],
            "order_ok": True,
            "scene_shows_initial_state": True,
            "state_sequence_ok": True,
            "orientation_coherent": True,
            "physical_consistency_ok": True,
        }

    def test_failed_audit_stops_after_two_attempts(self) -> None:
        calls = iter(
            [
                self._plan(),
                self._failed_audit(),
                self._plan(),
                self._failed_audit(),
            ]
        )
        with patch.object(
            app, "ollama_structured", side_effect=lambda *a, **k: next(calls)
        ):
            with self.assertRaises(app.PlanAuditError) as caught:
                app.create_plan(
                    "A person raises one hand.",
                    1,
                    False,
                )
        self.assertTrue(caught.exception.plan["rejected"])
        self.assertEqual(caught.exception.plan["audit_attempts"], 2)
        self.assertEqual(
            caught.exception.plan["check"]["missing"],
            ["the requested wave"],
        )

    def test_start_image_marks_scene_check_not_applicable(self) -> None:
        passed = self._failed_audit()
        passed["missing"] = []
        passed["scene_shows_initial_state"] = False
        calls = iter([self._plan(), passed])
        with patch.object(
            app, "ollama_structured", side_effect=lambda *a, **k: next(calls)
        ):
            plan = app.create_plan(
                "A person raises one hand.",
                1,
                True,
            )
        self.assertFalse(plan["check"]["scene_initial_state_applicable"])
        self.assertFalse(plan["check"]["scene_shows_initial_state"])
        self.assertEqual(
            plan["transitions"],
            [
                {
                    "initial_state": (
                        "exact visible state in the supplied start image"
                    ),
                    "visible_action": "The person raises one hand",
                    "result_state": "The person holds one raised hand",
                }
            ],
        )

    def test_nonhuman_plan_does_not_inject_human_anatomy(self) -> None:
        candidate = {
            "title": "Rolling ball",
            "scene": "A blue ball rests at the left edge of a wooden table.",
            "human_subject": False,
            "camera_view": "front",
            "steps": ["The blue ball rolls from left to right"],
            "result_states": ["The blue ball rests at the right edge"],
        }
        passed = self._failed_audit()
        passed["missing"] = []
        calls = iter([candidate, passed])
        with patch.object(
            app, "ollama_structured", side_effect=lambda *a, **k: next(calls)
        ):
            plan = app.create_plan(
                "A blue ball rolls from left to right.",
                1,
                False,
            )

        prompt = plan["shots"][0]["prompt"]
        self.assertFalse(plan["human_subject"])
        self.assertIn("coherent geometric orientation", prompt)
        self.assertIn("coherent shape", prompt)
        self.assertIn("duplicate object", prompt)
        self.assertNotIn("face, shoulders", prompt)
        self.assertNotIn("duplicate body part", prompt)

    def test_humanlike_text_overrides_a_false_model_classification(self) -> None:
        self.assertTrue(
            app.infer_human_subject(
                False,
                "A white armored dragon warrior raises one hand.",
            )
        )
        self.assertFalse(
            app.infer_human_subject(
                False,
                "A matte blue ball rolls across a wooden table.",
            )
        )
        self.assertFalse(
            app.infer_human_subject(
                True,
                "No people or animals are present.",
                "Gentle ocean waves roll toward the shore.",
            )
        )


class DatabaseMigrationTests(unittest.TestCase):
    def test_new_quality_columns_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(app, "DATA_ROOT", root),
                patch.object(app, "DB_PATH", root / "jobs.sqlite3"),
            ):
                app.init_db()
                with app.db_connect() as database:
                    columns = {
                        row["name"]
                        for row in database.execute(
                            "PRAGMA table_info(jobs)"
                        ).fetchall()
                    }
        self.assertTrue(
            {
                "backend_id",
                "generation_profile",
                "attempt",
                "quality_json",
                "continuation_json",
            }.issubset(columns)
        )

    def test_only_successful_final_output_is_published(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "output"
            candidate = output_root / "jobs" / "job-1" / "attempt.mp4"
            final = output_root / "jobs" / "job-1" / "final.mp4"
            candidate.parent.mkdir(parents=True)
            candidate.write_bytes(b"candidate")
            final.write_bytes(b"final")
            with (
                patch.object(app, "DATA_ROOT", root),
                patch.object(app, "DB_PATH", root / "jobs.sqlite3"),
                patch.object(app, "OUTPUT_ROOT", output_root),
            ):
                app.init_db()
                with app.db_connect() as database:
                    database.execute(
                        """
                        INSERT INTO jobs(
                            id, status, engine, concept, input_image, shots,
                            seed, quality_mode, precision_mode,
                            continuation_of, created_at, updated_at, output_path
                        ) VALUES (
                            'job-1', 'succeeded', 'wan5b', 'test concept',
                            '', 1, 1, 0, 0, '', 'now', 'now',
                            'jobs/job-1/final.mp4'
                        )
                        """
                    )
                with self.assertRaises(app.HTTPException) as caught:
                    app.get_output("jobs/job-1/attempt.mp4")
                response = app.get_output("jobs/job-1/final.mp4")

        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(Path(response.path), final)


class ContinuationStateTests(unittest.TestCase):
    def test_temporal_state_is_carried_with_result_state(self) -> None:
        parent = {
            "plan_json": (
                '{"result_states":["The person is holding a blue cup."]}'
            ),
            "continuation_json": (
                '{"temporal_state_text":"Visible temporal state: the person '
                'moves right at slow speed."}'
            ),
        }
        context = app.continuation_context(parent)  # type: ignore[arg-type]
        self.assertIn("holding a blue cup", context)
        self.assertIn("moves right at slow speed", context)

    def test_bundle_keeps_terminal_sequence_and_temporal_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "output"
            input_root = Path(directory) / "input"
            source = output_root / "jobs" / "source" / "final.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")
            input_root.mkdir()
            calls = 0

            def fake_run(*args: object, **kwargs: object) -> object:
                nonlocal calls
                calls += 1
                if calls == 1:
                    return SimpleNamespace(
                        stdout=json.dumps(
                            {
                                "format": {"duration": "5.0"},
                                "streams": [
                                    {
                                        "r_frame_rate": "24/1",
                                        "nb_frames": "121",
                                        "width": 960,
                                        "height": 544,
                                    }
                                ],
                            }
                        ),
                        stderr="",
                    )
                if calls == 3:
                    frames = (
                        output_root
                        / "jobs"
                        / "child"
                        / "continuation_01_frames"
                    )
                    for index in range(1, 4):
                        (frames / f"frame_{index:02d}.png").write_bytes(
                            b"png"
                        )
                return SimpleNamespace(stdout="", stderr="")

            temporal = {
                "subject_count": 1,
                "orientation": "left profile",
                "pose": "walking",
                "motion_direction": "right",
                "motion_speed": "slow",
                "held_objects": ["blue cup"],
                "camera_state": "fixed",
                "confidence": 0.9,
            }
            with (
                patch.object(app, "OUTPUT_ROOT", output_root),
                patch.object(app, "INPUT_ROOT", input_root),
                patch.object(app, "docker_group_run", side_effect=fake_run),
                patch.object(
                    app,
                    "extract_continuation_frame",
                    return_value="continuation-child-01.png",
                ),
                patch.object(
                    app,
                    "analyze_temporal_state",
                    return_value=temporal,
                ),
            ):
                bundle = app.build_continuation_bundle(
                    "child",
                    1,
                    "jobs/source/final.mp4",
                )

        self.assertEqual(bundle["version"], 2)
        self.assertEqual(len(bundle["terminal_frames"]), 3)
        self.assertEqual(
            bundle["terminal_frames"][0]["estimated_timestamp_seconds"],
            3.9,
        )
        self.assertEqual(
            bundle["strict_final_frame"], "continuation-child-01.png"
        )
        self.assertIn("blue cup", bundle["temporal_state_text"])


if __name__ == "__main__":
    unittest.main()
