"""Contract tests for the Lightsail single-process deployment safeguard."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT_SCRIPT = ROOT / "scripts" / "deployment" / "upload_to_lightsail.bat"
DEPLOYMENT_DOC = ROOT / "docs" / "deployment" / "lightsail.md"
DOCKERFILE = ROOT / "Dockerfile"
PRODUCTION_LAUNCHER = (
    ROOT / "products" / "reunia" / "meeting_assistant" / "run_production.py"
)


class LightsailScaleEnforcementContractTests(unittest.TestCase):
    """Prevent deployment automation from splitting process-local state."""

    def setUp(self) -> None:
        self.script = DEPLOYMENT_SCRIPT.read_text(encoding="utf-8")

    def test_script_sets_scale_to_one(self) -> None:
        self.assertIn('set "REQUIRED_SCALE=1"', self.script)
        self.assertIn("aws lightsail update-container-service", self.script)
        self.assertIn("--scale %REQUIRED_SCALE%", self.script)

    def test_script_queries_and_compares_returned_scale(self) -> None:
        self.assertIn("aws lightsail get-container-services", self.script)
        self.assertIn('--query "containerServices[0].scale"', self.script)
        self.assertIn('--output text > "%SCALE_OUTPUT%"', self.script)
        self.assertIn(
            'if not "%ACTUAL_SCALE%"=="%REQUIRED_SCALE%"',
            self.script,
        )

    def test_script_rejects_any_lightsail_command_override(self) -> None:
        self.assertIn('set "REQUIRED_COMMAND_OVERRIDE_COUNT=0"', self.script)
        self.assertIn(
            '--query "length(containerServices[0].currentDeployment.containers.*.command[])"',
            self.script,
        )
        self.assertIn(
            'if not "%COMMAND_OVERRIDE_COUNT%"=="%REQUIRED_COMMAND_OVERRIDE_COUNT%"',
            self.script,
        )
        self.assertIn("Unsafe Lightsail command override detected.", self.script)
        self.assertNotIn("--command ", self.script.lower())

    def test_script_checks_command_before_scale_redeploy(self) -> None:
        command_check = self.script.index("Verifying Lightsail uses the image command")
        scale_update = self.script.index("aws lightsail update-container-service")
        self.assertLess(command_check, scale_update)

    def test_script_fails_prominently_on_mismatch_or_aws_failure(self) -> None:
        self.assertIn("ERROR: DEPLOYMENT STOPPED", self.script)
        self.assertIn("Scale verification failed.", self.script)
        self.assertIn("Gunicorn workers = 1", self.script)
        self.assertIn("Gunicorn threads = 4", self.script)
        self.assertIn("exit /b 1", self.script)
        self.assertGreaterEqual(self.script.count("if errorlevel 1"), 5)

    def test_docker_image_command_uses_one_worker_and_four_threads(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            'CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4",',
            dockerfile,
        )
        self.assertIn("Lightsail must not override this image command", dockerfile)

    def test_legacy_production_launcher_cannot_start_two_workers(self) -> None:
        launcher = PRODUCTION_LAUNCHER.read_text(encoding="utf-8")
        self.assertIn('"--workers", "1"', launcher)
        self.assertIn('"--threads", "4"', launcher)
        self.assertNotIn("GUNICORN_WORKERS", launcher)
        self.assertNotIn("GUNICORN_THREADS", launcher)

    def test_document_explains_full_runtime_invariant(self) -> None:
        text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
        self.assertIn("Lightsail scale = 1", text)
        self.assertIn("Lightsail command override = none", text)
        self.assertIn("Gunicorn workers = 1", text)
        self.assertIn("Gunicorn threads = 4", text)
        self.assertIn("Leave the Lightsail **Command** field empty", text)
        self.assertIn("The script never supplies a Lightsail command override", text)
        self.assertIn("exits with a nonzero", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
