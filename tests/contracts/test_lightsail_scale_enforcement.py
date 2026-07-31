"""Contract tests for conditional Lightsail deployment safeguards."""

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
    """Keep single-process enforcement limited to explicit demo storage."""

    def setUp(self) -> None:
        self.script = DEPLOYMENT_SCRIPT.read_text(encoding="utf-8")

    def test_script_detects_explicit_demo_override(self) -> None:
        self.assertIn('set "DEMO_STORAGE=0"', self.script)
        self.assertIn(
            'CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION%"=="true"',
            self.script,
        )

    def test_script_enforces_scale_one_only_for_demo_storage(self) -> None:
        self.assertIn('if "%DEMO_STORAGE%"=="1" (', self.script)
        self.assertIn("Demo storage override detected; enforcing Lightsail scale 1", self.script)
        self.assertIn("--scale 1", self.script)
        self.assertIn(
            'if "%DEMO_STORAGE%"=="1" if not "%ACTUAL_SCALE%"=="1"',
            self.script,
        )

    def test_script_preserves_persistent_service_scale(self) -> None:
        self.assertIn(
            "Persistent storage mode; preserving the configured Lightsail scale",
            self.script,
        )
        self.assertIn(
            "Multiple nodes and Gunicorn workers are permitted after successful validation",
            self.script,
        )

    def test_script_queries_returned_scale(self) -> None:
        self.assertIn("aws lightsail get-container-services", self.script)
        self.assertIn('--query "containerServices[0].scale"', self.script)
        self.assertIn('--output text > "%SCALE_OUTPUT%"', self.script)

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

    def test_script_checks_command_before_any_scale_change(self) -> None:
        command_check = self.script.index("Verifying Lightsail uses the image command")
        scale_update = self.script.index("aws lightsail update-container-service")
        self.assertLess(command_check, scale_update)

    def test_script_fails_prominently_on_aws_or_demo_scale_failure(self) -> None:
        self.assertIn("ERROR: DEPLOYMENT STOPPED", self.script)
        self.assertIn("Demo-storage scale verification failed", self.script)
        self.assertIn("Demo storage requires Lightsail scale = 1", self.script)
        self.assertIn("Demo storage requires Gunicorn workers = 1", self.script)
        self.assertIn("exit /b 1", self.script)
        self.assertGreaterEqual(self.script.count("if errorlevel 1"), 5)

    def test_docker_image_keeps_one_worker_as_conservative_default(self) -> None:
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            'CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4",',
            dockerfile,
        )
        self.assertIn("not a storage-correctness", dockerfile)
        self.assertIn("should not override this versioned image command", dockerfile)

    def test_legacy_production_launcher_allows_explicit_worker_configuration(self) -> None:
        launcher = PRODUCTION_LAUNCHER.read_text(encoding="utf-8")
        self.assertIn('_positive_int_env("GUNICORN_WORKERS", 1)', launcher)
        self.assertIn('_positive_int_env("GUNICORN_THREADS", 4)', launcher)
        self.assertIn('"--workers", gunicorn_workers', launcher)
        self.assertIn('"--threads", gunicorn_threads', launcher)

    def test_document_explains_conditional_runtime_invariant(self) -> None:
        text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
        self.assertIn("## Conditional deployment policy", text)
        self.assertIn("Durable DynamoDB/DynamoDB/S3", text)
        self.assertIn("Demo memory/DynamoDB/local", text)
        self.assertIn("exactly 1", text)
        self.assertIn("1 or greater", text)
        self.assertIn("Leave the Lightsail **Command** field empty", text)
        self.assertIn("The script never supplies a Lightsail command override", text)
        self.assertIn("exits with a nonzero", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
