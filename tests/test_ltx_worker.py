from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workers.ltx.worker import (
    GenerateRequest,
    ModelPaths,
    Profile,
    ValidationError,
    WorkerSettings,
    build_command,
    offline_environment,
)


class LTXWorkerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        root = Path(self.temporary_directory.name)
        self.input_root = root / "input"
        self.output_root = root / "output"
        self.repository_root = root / "ltx"
        self.model_root = root / "models"
        self.input_root.mkdir()
        self.output_root.mkdir()
        self.repository_root.mkdir()
        self.model_root.mkdir()
        self.input_image = self.input_root / "source.png"
        self.input_image.write_bytes(b"\x89PNG\r\n\x1a\n")

        self.models = ModelPaths(
            dev_transformer=self.model_root / "dev.safetensors",
            distilled_transformer=self.model_root / "distilled.safetensors",
            text_encoder=self.model_root / "text.safetensors",
            video_vae=self.model_root / "video-vae.safetensors",
            audio_vae=self.model_root / "audio-vae.safetensors",
            duration_head=self.model_root / "duration-head.safetensors",
            spatial_upsampler=self.model_root / "spatial.safetensors",
            distilled_lora=self.model_root / "distilled-lora.safetensors",
            detailing_lora=self.model_root / "detailing-lora.safetensors",
        )
        self.settings = WorkerSettings(
            input_root=self.input_root,
            output_root=self.output_root,
            repository_root=self.repository_root,
            python_executable="/opt/ltx/.venv/bin/python",
            host="127.0.0.1",
            port=8191,
            max_request_bytes=65536,
            cancel_grace_seconds=10,
            offload_mode="cpu",
            model_paths=self.models,
        )

    def payload(self, profile: str = "ti2_hq") -> dict[str, object]:
        return {
            "profile": profile,
            "input_path": "source.png",
            "output_path": f"jobs/{profile}.mp4",
            "prompt": "A paper kite rises over a quiet green field.",
            "negative": "text, watermark",
            "seed": 42,
            "width": 1024,
            "height": 768,
            "frames": 121,
            "fps": 24,
        }

    def request(self, profile: str = "ti2_hq") -> GenerateRequest:
        payload = self.payload(profile)
        if profile == "dfr":
            payload["negative"] = None
        return GenerateRequest.from_dict(payload, self.settings)

    def test_validates_grid_types_and_unknown_fields(self) -> None:
        cases = [
            ("width", 1000, "divisible by 64"),
            ("frames", 120, r"8k\+1"),
            ("seed", True, "must be an integer"),
            ("fps", float("inf"), "must be finite"),
        ]
        for field, value, message in cases:
            with self.subTest(field=field):
                payload = self.payload()
                payload[field] = value
                with self.assertRaisesRegex(ValidationError, message):
                    GenerateRequest.from_dict(payload, self.settings)

        payload = self.payload()
        payload["extra"] = "not accepted"
        with self.assertRaisesRegex(ValidationError, "unknown fields"):
            GenerateRequest.from_dict(payload, self.settings)

    def test_rejects_path_traversal_and_symlink_escape(self) -> None:
        payload = self.payload()
        payload["output_path"] = "../escaped.mp4"
        with self.assertRaisesRegex(ValidationError, "outside"):
            GenerateRequest.from_dict(payload, self.settings)

        outside = Path(self.temporary_directory.name) / "outside"
        outside.mkdir()
        (self.output_root / "link").symlink_to(outside, target_is_directory=True)
        payload = self.payload()
        payload["output_path"] = "link/escaped.mp4"
        with self.assertRaisesRegex(ValidationError, "outside"):
            GenerateRequest.from_dict(payload, self.settings)

        outside_image = outside / "outside.png"
        outside_image.write_bytes(b"\x89PNG\r\n\x1a\n")
        payload = self.payload()
        payload["input_path"] = str(outside_image)
        with self.assertRaisesRegex(ValidationError, "outside"):
            GenerateRequest.from_dict(payload, self.settings)

    def test_dfr_rejects_unsupported_negative_prompt(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "does not support negative prompts",
        ):
            GenerateRequest.from_dict(self.payload("dfr"), self.settings)

    def test_builds_ti2_hq_official_cli_command(self) -> None:
        request = self.request("ti2_hq")
        command = build_command(request, self.settings)

        self.assertEqual(
            command[:3],
            [
                "/opt/ltx/.venv/bin/python",
                "-m",
                "ltx_pipelines.ti2vid_two_stages_hq",
            ],
        )
        self.assertEqual(
            self.option_value(command, "--transformer-path"),
            str(self.models.dev_transformer),
        )
        self.assertEqual(
            self.option_value(command, "--distilled-lora"),
            str(self.models.distilled_lora),
        )
        self.assertEqual(
            self.option_value(command, "--negative-prompt"),
            "text, watermark",
        )
        self.assertEqual(
            self.option_value(command, "--duration-head-path"),
            str(self.models.duration_head),
        )
        self.assertEqual(self.option_value(command, "--offload"), "cpu")
        image_index = command.index("--image")
        self.assertEqual(
            command[image_index + 1 : image_index + 5],
            [str(self.input_image), "0", "1.0", "--output-path"],
        )
        self.assertNotIn("--chunked", command)
        self.assertNotIn("--detailing-lora", command)

    def test_builds_dfr_with_distilled_checkpoint_and_no_negative(self) -> None:
        request = self.request("dfr")
        command = build_command(request, self.settings)

        self.assertEqual(
            command[2],
            "ltx_pipelines.dfr_pipeline",
        )
        self.assertEqual(
            self.option_value(command, "--transformer-path"),
            str(self.models.distilled_transformer),
        )
        self.assertEqual(
            self.option_value(command, "--detailing-lora"),
            str(self.models.detailing_lora),
        )
        self.assertNotIn("--distilled-lora", command)
        self.assertNotIn("--negative-prompt", command)
        self.assertNotIn("--chunked", command)

    def test_builds_chunked_two_stage_command(self) -> None:
        request = self.request("ti2_chunked")
        command = build_command(request, self.settings)

        self.assertEqual(
            command[2],
            "ltx_pipelines.ti2vid_two_stages",
        )
        self.assertIn("--chunked", command)
        self.assertEqual(
            self.option_value(command, "--chunk-pixel-frames"),
            "97",
        )
        self.assertEqual(
            self.option_value(command, "--chunk-carry-frames"),
            "25",
        )
        self.assertEqual(
            self.option_value(command, "--chunk-blend-frames"),
            "25",
        )
        self.assertEqual(
            self.option_value(command, "--num-frames"),
            "121",
        )
        self.assertEqual(
            self.option_value(command, "--frame-rate"),
            "24",
        )

    def test_forces_offline_subprocess_environment(self) -> None:
        environment = offline_environment(
            {
                "HF_HUB_OFFLINE": "0",
                "TRANSFORMERS_OFFLINE": "0",
                "CUSTOM": "kept",
            }
        )
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")
        self.assertEqual(environment["TRANSFORMERS_OFFLINE"], "1")
        self.assertEqual(environment["UV_OFFLINE"], "1")
        self.assertEqual(environment["PIP_NO_INDEX"], "1")
        self.assertEqual(environment["CUSTOM"], "kept")

    @staticmethod
    def option_value(command: list[str], option: str) -> str:
        return command[command.index(option) + 1]


if __name__ == "__main__":
    unittest.main()
