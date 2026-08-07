"""Contracts and dependency-free unit tests for live deployment validation."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / "scripts" / "deployment" / "validate_lightsail_deployment.py"
VALIDATOR_WRAPPER = ROOT / "scripts" / "deployment" / "validate_lightsail_deployment.bat"
DEPLOYMENT_DOC = ROOT / "docs" / "deployment" / "lightsail.md"
DOCKERFILE = ROOT / "Dockerfile"
HEALTHY_ASYNC_WORKER = {
    "status": "healthy",
    "last_heartbeat_at": "2026-08-04T19:00:00+00:00",
    "age_seconds": 12,
    "max_age_seconds": 90,
    "worker_id": "worker-1",
    "state": "idle",
}


def _load_validator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "career_bridge_lightsail_deployment_validator", VALIDATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load deployment validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


class _Headers:
    @staticmethod
    def get_content_charset() -> str:
        return "utf-8"


class _Response:
    def __init__(self, body: str, *, url: str, status: int = 200) -> None:
        self._body = body.encode("utf-8")
        self._url = url
        self.status = status
        self.headers = _Headers()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def getcode(self) -> int:
        return self.status


class _SmokeOpener:
    def __init__(self) -> None:
        self.application_id = "deployment-smoke-id"
        self.company = ""
        self.role = ""
        self.deleted = False
        self.urls: list[str] = []

    def _applications_page(self) -> str:
        card = ""
        if self.company and not self.deleted:
            card = (
                f'<article id="application-{self.application_id}">'
                f"<h3>{self.role}</h3><p>{self.company}</p>"
                "</article>"
            )
        return (
            '<input type="hidden" name="csrf_token" value="application-csrf">'
            f"{card}"
        )

    def open(self, request, timeout):
        del timeout
        url = request.full_url
        self.urls.append(url)
        data = parse_qs((request.data or b"").decode("utf-8"))

        if url.endswith("/login.html"):
            return _Response(
                '<input type="hidden" name="csrf_token" value="login-csrf">',
                url=url,
            )
        if url.endswith("/api/login"):
            if data.get("csrf_token") != ["login-csrf"]:
                raise AssertionError("login CSRF was not submitted")
            return _Response("signed in", url="https://career.example/app")
        if url.endswith("/applications/applications/create"):
            if data.get("csrf_token") != ["application-csrf"]:
                raise AssertionError("application CSRF was not submitted")
            self.company = data["company"][0]
            self.role = data["role"][0]
            return _Response(self._applications_page(), url=url)
        if url.endswith(f"/applications/applications/{self.application_id}/delete"):
            if data.get("csrf_token") != ["application-csrf"]:
                raise AssertionError("cleanup CSRF was not submitted")
            self.deleted = True
            return _Response(self._applications_page(), url=url)
        if any(
            path in url
            for path in (
                "/applications/career-translation",
                "/applications/?tab=applications",
                "/applications/?tab=tailoring",
                "/applications/?tab=reports",
            )
        ):
            return _Response(self._applications_page(), url=url)
        raise AssertionError(f"Unexpected URL: {url}")


class _HealthOpener:
    def open(self, request, timeout):
        del timeout
        payload = {
            "status": "ok",
            "services": ["reunia", "application-builder"],
            "application_builder": validator.EXPECTED_STORAGE_STATUS,
            "async_worker": HEALTHY_ASYNC_WORKER,
        }
        return _Response(json.dumps(payload), url=request.full_url)


class _DemoHealthOpener:
    def open(self, request, timeout):
        del timeout
        payload = {
            "status": "ok",
            "services": ["reunia", "application-builder"],
            "application_builder": validator.DEMO_STORAGE_STATUS,
            "async_worker": HEALTHY_ASYNC_WORKER,
        }
        return _Response(json.dumps(payload), url=request.full_url)


class _LegacyTableHealthOpener:
    def open(self, request, timeout):
        del timeout
        storage = dict(validator.PERSISTENT_STORAGE_STATUS)
        storage["job_discovery_table"] = "career-bridge-job-discovery"
        payload = {
            "status": "ok",
            "services": ["reunia", "application-builder"],
            "application_builder": storage,
            "async_worker": HEALTHY_ASYNC_WORKER,
        }
        return _Response(json.dumps(payload), url=request.full_url)


class _MemoryDiscoveryHealthOpener:
    def open(self, request, timeout):
        del timeout
        storage = dict(validator.PERSISTENT_STORAGE_STATUS)
        storage.update(
            {
                "job_discovery_storage": "memory",
                "job_discovery_table": "",
                "job_discovery_durability": "ephemeral",
                "durability": "mixed",
                "multi_worker_safe": False,
                "multi_node_safe": False,
            }
        )
        payload = {
            "status": "ok",
            "services": ["reunia", "application-builder"],
            "application_builder": storage,
            "async_worker": HEALTHY_ASYNC_WORKER,
        }
        return _Response(json.dumps(payload), url=request.full_url)


class _MissingWorkerHealthOpener:
    def open(self, request, timeout):
        del timeout
        payload = {
            "status": "ok",
            "services": ["career-bridge", "application-builder"],
            "application_builder": validator.EXPECTED_STORAGE_STATUS,
        }
        return _Response(json.dumps(payload), url=request.full_url)


class _StaleWorkerHealthOpener:
    def open(self, request, timeout):
        del timeout
        worker = dict(HEALTHY_ASYNC_WORKER)
        worker.update({"status": "stale", "age_seconds": 180})
        payload = {
            "status": "ok",
            "services": ["career-bridge", "application-builder"],
            "application_builder": validator.EXPECTED_STORAGE_STATUS,
            "async_worker": worker,
        }
        return _Response(json.dumps(payload), url=request.full_url)


class LightsailDeploymentValidationTests(unittest.TestCase):
    def test_script_and_windows_wrapper_exist(self) -> None:
        self.assertTrue(VALIDATOR_PATH.is_file())
        self.assertTrue(VALIDATOR_WRAPPER.is_file())
        wrapper = VALIDATOR_WRAPPER.read_text(encoding="utf-8")
        self.assertIn("validate_lightsail_deployment.py", wrapper)
        self.assertIn("exit /b %VALIDATION_EXIT%", wrapper)

    def test_demo_storage_scale_must_equal_one(self) -> None:
        self.assertEqual(
            validator._validate_scale({"scale": 1}, require_single_node=True),
            1,
        )
        with self.assertRaisesRegex(validator.ValidationFailure, "expected 1"):
            validator._validate_scale({"scale": 2}, require_single_node=True)

    def test_persistent_storage_allows_scale_greater_than_one(self) -> None:
        self.assertEqual(
            validator._validate_scale({"scale": 3}, require_single_node=False),
            3,
        )

    def test_public_container_must_have_no_command_override(self) -> None:
        service = {
            "publicEndpoint": {"containerName": "career-bridge"},
            "currentDeployment": {
                "containers": {
                    "career-bridge": {
                        "image": ":reunia-career-bridge.test.1",
                        "command": [],
                    }
                }
            },
        }
        container = validator._select_live_container(service, requested_name=None)
        validator._validate_no_command_override(container)
        unsafe = validator.LiveContainer(
            name="career-bridge",
            image=container.image,
            command_override=("gunicorn", "--workers", "2", "app:app"),
        )
        with self.assertRaisesRegex(validator.ValidationFailure, "command override"):
            validator._validate_no_command_override(unsafe)

    def test_docker_image_command_has_one_worker_and_four_threads(self) -> None:
        command = validator._parse_dockerfile_command(DOCKERFILE)
        validator._validate_image_command(command)
        self.assertEqual(validator._flag_values(command, "--workers"), ["1"])
        self.assertEqual(validator._flag_values(command, "--threads"), ["4"])

    def test_demo_image_command_rejects_two_workers(self) -> None:
        with self.assertRaisesRegex(validator.ValidationFailure, "workers 1"):
            validator._validate_image_command(
                ["gunicorn", "--workers", "2", "--threads", "4", "app:app"],
                require_single_worker=True,
            )

    def test_persistent_image_command_allows_two_workers(self) -> None:
        workers, threads = validator._validate_image_command(
            ["gunicorn", "--workers", "2", "--threads", "4", "app:app"],
            require_single_worker=False,
        )
        self.assertEqual((workers, threads), (2, 4))

    def test_health_validation_requires_expected_storage_contract(self) -> None:
        with patch.object(validator, "build_opener", return_value=_HealthOpener()):
            payload = validator._validate_health(
                "https://career.example", timeout=1
            )
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["application_builder"]["multi_worker_safe"])

    def test_health_requires_recent_async_worker_heartbeat(self) -> None:
        with patch.object(
            validator, "build_opener", return_value=_MissingWorkerHealthOpener()
        ):
            with self.assertRaisesRegex(
                validator.ValidationFailure, "does not expose async_worker"
            ):
                validator._validate_health("https://career.example", timeout=1)

        with patch.object(
            validator, "build_opener", return_value=_StaleWorkerHealthOpener()
        ):
            with self.assertRaisesRegex(
                validator.ValidationFailure, "queued AI jobs could remain unprocessed"
            ):
                validator._validate_health("https://career.example", timeout=1)

    def test_health_rejects_legacy_hyphenated_table_name(self) -> None:
        with patch.object(
            validator, "build_opener", return_value=_LegacyTableHealthOpener()
        ):
            with self.assertRaisesRegex(
                validator.ValidationFailure, "can recreate duplicate tables"
            ):
                validator._validate_health("https://career.example", timeout=1)

    def test_health_rejects_in_memory_job_discovery_even_when_other_stores_are_durable(self) -> None:
        for allow_demo_storage in (False, True):
            with self.subTest(allow_demo_storage=allow_demo_storage):
                with patch.object(
                    validator,
                    "build_opener",
                    return_value=_MemoryDiscoveryHealthOpener(),
                ):
                    with self.assertRaisesRegex(
                        validator.ValidationFailure,
                        "CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND=dynamodb",
                    ):
                        validator._validate_health(
                            "https://career.example",
                            timeout=1,
                            allow_demo_storage=allow_demo_storage,
                        )

    def test_workspace_smoke_validation_checks_all_navbar_routes(self) -> None:
        opener = _SmokeOpener()
        body = validator._validate_authenticated_workspace_routes(
            opener, "https://career.example", timeout=1
        )
        self.assertIn("csrf_token", body)
        for _, path in validator.APPLICATION_BUILDER_WORKSPACE_PATHS:
            self.assertTrue(any(path in url for url in opener.urls), path)

    def test_demo_health_requires_explicit_validator_override(self) -> None:
        with patch.object(validator, "build_opener", return_value=_DemoHealthOpener()):
            with self.assertRaisesRegex(
                validator.ValidationFailure, "--allow-demo-storage"
            ):
                validator._validate_health("https://career.example", timeout=1)

        with patch.object(validator, "build_opener", return_value=_DemoHealthOpener()):
            payload = validator._validate_health(
                "https://career.example",
                timeout=1,
                allow_demo_storage=True,
            )
        self.assertEqual(payload["application_builder"]["durability"], "mixed")

    def test_demo_storage_never_allows_lightsail_scale_greater_than_one(self) -> None:
        # The explicit non-durable-storage acknowledgement only relaxes the health
        # durability check. It must never relax the single-node scale guard.
        with patch.object(validator, "build_opener", return_value=_DemoHealthOpener()):
            payload = validator._validate_health(
                "https://career.example",
                timeout=1,
                allow_demo_storage=True,
            )
        self.assertEqual(payload["application_builder"]["durability"], "mixed")
        with self.assertRaisesRegex(validator.ValidationFailure, "expected 1"):
            validator._validate_scale({"scale": 2})

    def test_authenticated_smoke_test_creates_retrieves_and_cleans_up(self) -> None:
        opener = _SmokeOpener()
        with patch.object(validator, "build_opener", return_value=opener):
            application_id, retrieved, cleanup_succeeded = validator._authenticated_application_smoke_test(
                "https://career.example",
                email="validator@example.com",
                password="not-logged",
                timeout=1,
                keep_test_application=False,
            )
        self.assertEqual(application_id, opener.application_id)
        self.assertTrue(retrieved)
        self.assertTrue(opener.deleted)
        self.assertTrue(cleanup_succeeded)
        self.assertTrue(
            any("application_id=deployment-smoke-id" in url for url in opener.urls)
        )

    def test_documentation_has_prominent_redeploy_data_loss_warning(self) -> None:
        validator._validate_documentation(DEPLOYMENT_DOC)
        text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
        self.assertIn("## Data-loss warning", text)
        self.assertIn(validator.EXPECTED_REDEPLOY_WARNING, " ".join(text.split()))
        self.assertIn("ephemeral container-node storage", text)

    def test_documentation_check_rejects_missing_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            document = Path(temporary_directory) / "lightsail.md"
            document.write_text("# Deployment\nNo warning here.\n", encoding="utf-8")
            with self.assertRaisesRegex(validator.ValidationFailure, "data-loss warning"):
                validator._validate_documentation(document)

    def test_validator_uses_live_aws_health_and_real_builder_routes(self) -> None:
        source = VALIDATOR_PATH.read_text(encoding="utf-8")
        self.assertIn('"get-container-services"', source)
        self.assertIn('"scale"', source)
        self.assertIn('"health"', source)
        self.assertIn('"async_worker"', source)
        self.assertIn("Async worker heartbeat is not healthy", source)
        self.assertIn('"applications/applications/create"', source)
        self.assertIn('"api/login"', source)
        self.assertIn('/delete"', source)
        self.assertIn("DEPLOYMENT_VALIDATION_EMAIL", source)
        self.assertIn("DEPLOYMENT_VALIDATION_PASSWORD", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
