#!/usr/bin/env python3
"""Validate a deployed Réunia Career Bridge Lightsail service.

The validator intentionally uses only the Python standard library plus the AWS
CLI. It checks the live Lightsail service, the image startup contract in the
local deployment package, the public health endpoint, and the real authenticated
Application Builder create/retrieve workflow.
"""

from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import os
import re
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

DEFAULT_REGION = "us-west-2"
DEFAULT_SERVICE_NAME = "reunia-career-bridge"
EXPECTED_REDEPLOY_WARNING = (
    "WARNING: Redeploying or replacing the Lightsail container can erase "
    "Application Builder records."
)
PERSISTENT_STORAGE_STATUS = {
    "workflow_storage": "dynamodb",
    "application_storage": "dynamodb",
    "job_discovery_storage": "dynamodb",
    "job_discovery_table": "careerbridge_job_discovery",
    "job_discovery_durability": "persistent",
    "document_storage": "s3",
    "durability": "persistent",
    "multi_worker_safe": True,
    "multi_node_safe": True,
}
DEMO_STORAGE_STATUS = {
    "workflow_storage": "memory",
    "application_storage": "dynamodb",
    "job_discovery_storage": "dynamodb",
    "job_discovery_table": "careerbridge_job_discovery",
    "job_discovery_durability": "persistent",
    "document_storage": "local",
    "durability": "mixed",
    "multi_worker_safe": False,
    "multi_node_safe": False,
}
PERSISTENT_STORAGE_REQUIREMENTS = {
    key: value
    for key, value in PERSISTENT_STORAGE_STATUS.items()
    if key != "job_discovery_table"
}
DEMO_STORAGE_REQUIREMENTS = {
    key: value
    for key, value in DEMO_STORAGE_STATUS.items()
    if key != "job_discovery_table"
}
# Backward-compatible name used by contract tests and external imports.
EXPECTED_STORAGE_STATUS = PERSISTENT_STORAGE_STATUS
APPLICATION_BUILDER_WORKSPACE_PATHS = (
    ("Career Translation", "applications/career-translation"),
    ("Job Applications", "applications/?tab=applications"),
    ("Resume Workflow", "applications/?tab=tailoring"),
    ("Resume Reports", "applications/?tab=reports"),
)


class ValidationFailure(RuntimeError):
    """Raised when a deployment invariant or smoke test fails."""


@dataclass(frozen=True)
class LiveContainer:
    """The application container selected from a Lightsail deployment."""

    name: str
    image: str
    command_override: tuple[str, ...]


class PageInspector(HTMLParser):
    """Extract CSRF tokens and Application Builder cards from rendered HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.csrf_tokens: list[str] = []
        self.application_cards: dict[str, str] = {}
        self._active_card_id: str | None = None
        self._active_card_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag.casefold() == "input" and attributes.get("name") == "csrf_token":
            token = attributes.get("value", "").strip()
            if token:
                self.csrf_tokens.append(token)

        if tag.casefold() == "article":
            element_id = attributes.get("id", "").strip()
            if element_id.startswith("application-"):
                self._active_card_id = element_id.removeprefix("application-")
                self._active_card_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "article" and self._active_card_id is not None:
            normalized = " ".join(" ".join(self._active_card_text).split())
            self.application_cards[self._active_card_id] = normalized
            self._active_card_id = None
            self._active_card_text = []

    def handle_data(self, data: str) -> None:
        if self._active_card_id is not None and data.strip():
            self._active_card_text.append(data.strip())


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationFailure(
            "Base URL must be an absolute http:// or https:// URL."
        )
    return base_url


def _parse_page(body: str) -> PageInspector:
    parser = PageInspector()
    parser.feed(body)
    return parser


def _csrf_token(body: str, *, page_name: str) -> str:
    parser = _parse_page(body)
    if not parser.csrf_tokens:
        raise ValidationFailure(f"No CSRF token was found on {page_name}.")
    return parser.csrf_tokens[0]


def _find_application_card(body: str, *, company: str, role: str) -> str | None:
    parser = _parse_page(body)
    expected_company = html.unescape(company).casefold()
    expected_role = html.unescape(role).casefold()
    for application_id, card_text in parser.application_cards.items():
        normalized = html.unescape(card_text).casefold()
        if expected_company in normalized and expected_role in normalized:
            return application_id
    return None


def _run_aws_get_container_services(
    *, aws_cli: str, region: str, service_name: str
) -> dict[str, Any]:
    command = [
        aws_cli,
        "lightsail",
        "get-container-services",
        "--region",
        region,
        "--service-name",
        service_name,
        "--output",
        "json",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ValidationFailure(f"Could not execute AWS CLI: {exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown AWS CLI error").strip()
        raise ValidationFailure(f"AWS Lightsail query failed: {detail}")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValidationFailure("AWS CLI returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValidationFailure("AWS CLI returned an unexpected response type.")
    return payload


def _select_live_service(
    payload: dict[str, Any], *, service_name: str
) -> dict[str, Any]:
    services = payload.get("containerServices")
    if not isinstance(services, list) or not services:
        raise ValidationFailure(
            f"Lightsail service {service_name!r} was not returned by AWS."
        )

    matching = [
        service
        for service in services
        if isinstance(service, dict)
        and service.get("containerServiceName") == service_name
    ]
    if not matching:
        returned_names = ", ".join(
            str(service.get("containerServiceName") or "<unnamed>")
            for service in services
            if isinstance(service, dict)
        )
        raise ValidationFailure(
            f"AWS did not return the requested Lightsail service {service_name!r}. "
            f"Returned: {returned_names or '<none>'}."
        )
    return matching[0]


def _validate_scale(
    service: dict[str, Any], *, require_single_node: bool = True
) -> int:
    """Validate service capacity for the active storage durability mode.

    Non-durable storage is process-local and therefore requires exactly one node.
    Durable DynamoDB/S3 storage permits more than one node, but the service must
    still report a positive scale.
    """

    raw_scale = service.get("scale")
    try:
        scale = int(raw_scale)
    except (TypeError, ValueError) as exc:
        raise ValidationFailure(
            f"Lightsail returned an invalid service scale: {raw_scale!r}"
        ) from exc
    if scale < 1:
        raise ValidationFailure(
            f"Lightsail service scale must be at least 1; received {scale}."
        )
    if require_single_node and scale != 1:
        raise ValidationFailure(
            f"Unsafe non-durable-storage Lightsail scale: expected 1, received {scale}."
        )
    return scale


def _command_tuple(raw_command: Any) -> tuple[str, ...]:
    if raw_command in (None, "", []):
        return ()
    if isinstance(raw_command, str):
        return tuple(shlex.split(raw_command, posix=True))
    if isinstance(raw_command, list):
        return tuple(str(item) for item in raw_command if str(item).strip())
    raise ValidationFailure("Lightsail container command metadata is malformed.")


def _select_live_container(
    service: dict[str, Any], *, requested_name: str | None
) -> LiveContainer:
    deployment = service.get("currentDeployment")
    if not isinstance(deployment, dict):
        raise ValidationFailure("Lightsail has no current deployment to validate.")
    containers = deployment.get("containers")
    if not isinstance(containers, dict) or not containers:
        raise ValidationFailure("The current Lightsail deployment has no containers.")

    public_endpoint = service.get("publicEndpoint") or {}
    endpoint_name = (
        str(public_endpoint.get("containerName") or "").strip()
        if isinstance(public_endpoint, dict)
        else ""
    )
    selected_name = (requested_name or endpoint_name).strip()
    if not selected_name:
        if len(containers) == 1:
            selected_name = next(iter(containers))
        else:
            raise ValidationFailure(
                "Could not identify the public application container. Pass --container-name."
            )

    config = containers.get(selected_name)
    if not isinstance(config, dict):
        available = ", ".join(sorted(str(name) for name in containers))
        raise ValidationFailure(
            f"Container {selected_name!r} is not in the current deployment. "
            f"Available containers: {available}."
        )

    return LiveContainer(
        name=selected_name,
        image=str(config.get("image") or "").strip(),
        command_override=_command_tuple(config.get("command")),
    )


def _validate_no_command_override(container: LiveContainer) -> None:
    if container.command_override:
        rendered = " ".join(container.command_override)
        raise ValidationFailure(
            "Unsafe Lightsail command override detected for container "
            f"{container.name!r}: {rendered}. Leave Command empty."
        )


def _parse_dockerfile_command(dockerfile: Path) -> list[str]:
    try:
        text = dockerfile.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationFailure(f"Could not read Dockerfile {dockerfile}: {exc}") from exc

    command_lines = [
        match.group(1).strip()
        for match in re.finditer(r"(?im)^\s*CMD\s+(.+?)\s*$", text)
    ]
    if not command_lines:
        raise ValidationFailure(f"Dockerfile {dockerfile} has no CMD instruction.")

    raw_command = command_lines[-1]
    if raw_command.startswith("["):
        try:
            parsed = json.loads(raw_command)
        except json.JSONDecodeError as exc:
            raise ValidationFailure("Dockerfile CMD is not valid JSON array syntax.") from exc
        if not isinstance(parsed, list):
            raise ValidationFailure("Dockerfile CMD JSON must be an array.")
        return [str(item) for item in parsed]
    return shlex.split(raw_command, posix=True)


def _flag_values(arguments: Sequence[str], flag: str) -> list[str]:
    values: list[str] = []
    for index, argument in enumerate(arguments):
        if argument == flag:
            if index + 1 >= len(arguments):
                raise ValidationFailure(f"Docker image command is missing a value for {flag}.")
            values.append(arguments[index + 1])
        elif argument.startswith(f"{flag}="):
            values.append(argument.split("=", 1)[1])
    return values


def _positive_flag_value(arguments: Sequence[str], flag: str) -> int:
    values = _flag_values(arguments, flag)
    if len(values) != 1:
        raise ValidationFailure(
            f"Docker image CMD must contain exactly one {flag!r} setting."
        )
    try:
        value = int(values[0])
    except ValueError as exc:
        raise ValidationFailure(
            f"Docker image CMD has an invalid integer value for {flag}: {values[0]!r}."
        ) from exc
    if value < 1:
        raise ValidationFailure(
            f"Docker image CMD requires {flag} to be at least 1; received {value}."
        )
    return value


def _validate_image_command(
    arguments: Sequence[str], *, require_single_worker: bool = True
) -> tuple[int, int]:
    """Validate the image command for non-durable or durable storage operation."""

    if not arguments or Path(arguments[0]).name.casefold() != "gunicorn":
        raise ValidationFailure("Docker image CMD must start Gunicorn.")

    workers = _positive_flag_value(arguments, "--workers")
    threads = _positive_flag_value(arguments, "--threads")
    if require_single_worker and workers != 1:
        raise ValidationFailure(
            "Non-durable storage requires the Docker image CMD to use '--workers 1'."
        )
    return workers, threads


def _validate_documentation(document_path: Path) -> None:
    try:
        text = document_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationFailure(
            f"Could not read deployment documentation {document_path}: {exc}"
        ) from exc

    normalized = " ".join(text.split())
    if EXPECTED_REDEPLOY_WARNING not in normalized:
        raise ValidationFailure(
            "Deployment documentation is missing the required prominent redeploy "
            "data-loss warning."
        )


def _read_response(response: Any) -> tuple[int, str, str]:
    status = int(getattr(response, "status", response.getcode()))
    charset = response.headers.get_content_charset() or "utf-8"
    body = response.read().decode(charset, errors="replace")
    return status, response.geturl(), body


def _open(
    opener: Any,
    request: Request,
    *, timeout: float,
    action: str,
) -> tuple[int, str, str]:
    try:
        with opener.open(request, timeout=timeout) as response:
            return _read_response(response)
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        detail = " ".join(re.sub(r"<[^>]+>", " ", body).split())[:240]
        suffix = f" Response: {detail}" if detail else ""
        raise ValidationFailure(
            f"{action} failed with HTTP {exc.code}.{suffix}"
        ) from exc
    except URLError as exc:
        raise ValidationFailure(f"{action} could not connect: {exc.reason}") from exc


def _request(
    url: str,
    *, data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Request:
    request_headers = {
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "User-Agent": "ReuniaCareerBridgeDeploymentValidator/1.0",
    }
    if headers:
        request_headers.update(headers)
    encoded = urlencode(data).encode("utf-8") if data is not None else None
    if encoded is not None:
        request_headers.setdefault(
            "Content-Type", "application/x-www-form-urlencoded"
        )
    return Request(url, data=encoded, headers=request_headers)


def _validate_health(
    base_url: str,
    *,
    timeout: float,
    allow_demo_storage: bool = False,
) -> dict[str, Any]:
    opener = build_opener()
    status, _, body = _open(
        opener,
        _request(urljoin(f"{base_url}/", "health"), headers={"Accept": "application/json"}),
        timeout=timeout,
        action="Health check",
    )
    if status != 200:
        raise ValidationFailure(f"Health endpoint returned HTTP {status}, expected 200.")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValidationFailure("Health endpoint did not return valid JSON.") from exc
    if payload.get("status") != "ok":
        raise ValidationFailure(
            f"Health endpoint status is not 'ok': {payload.get('status')!r}."
        )
    storage_status = payload.get("application_builder")
    if not isinstance(storage_status, dict):
        raise ValidationFailure(
            "Health endpoint does not expose Application Builder storage metadata."
        )

    persistent_match = all(
        storage_status.get(key) == value
        for key, value in PERSISTENT_STORAGE_REQUIREMENTS.items()
    )
    discovery_table = str(
        storage_status.get("job_discovery_table") or ""
    ).strip()
    if persistent_match and discovery_table:
        return payload

    demo_match = all(
        storage_status.get(key) == value
        for key, value in DEMO_STORAGE_REQUIREMENTS.items()
    )
    if storage_status.get("job_discovery_storage") != "dynamodb":
        raise ValidationFailure(
            "Health endpoint reports non-persistent Job Discovery storage "
            f"({storage_status.get('job_discovery_storage')!r}). Production "
            "validation requires CAREER_BRIDGE_JOB_DISCOVERY_STORAGE_BACKEND="
            "dynamodb and a non-empty CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME; "
            "--allow-demo-storage does not relax this requirement."
        )
    if not discovery_table:
        raise ValidationFailure(
            "Health endpoint reports DynamoDB Job Discovery storage without a table "
            "name. Set CAREER_BRIDGE_JOB_DISCOVERY_TABLE_NAME before deployment."
        )

    if allow_demo_storage and demo_match:
        return payload
    if demo_match:
        raise ValidationFailure(
            "Health endpoint reports mixed-durability Career Bridge storage. Production "
            "validation requires DynamoDB/S3 unless --allow-demo-storage is supplied."
        )

    raise ValidationFailure(
        "Health endpoint does not expose a recognized persistent Career Bridge "
        "storage configuration, including Job Discovery durability."
    )


def _validate_authenticated_workspace_routes(
    opener: Any,
    base_url: str,
    *,
    timeout: float,
) -> str:
    """Require every navbar-backed Application Builder workspace to render."""

    applications_body = ""
    for label, relative_path in APPLICATION_BUILDER_WORKSPACE_PATHS:
        workspace_url = urljoin(f"{base_url}/", relative_path)
        status, final_url, body = _open(
            opener,
            _request(workspace_url),
            timeout=timeout,
            action=f"{label} workspace request",
        )
        if status != 200:
            raise ValidationFailure(
                f"{label} workspace returned HTTP {status}, expected 200."
            )
        if "/login" in urlparse(final_url).path:
            raise ValidationFailure(f"{label} workspace request was not authenticated.")
        if label == "Job Applications":
            applications_body = body
    if not applications_body:
        raise ValidationFailure("Job Applications workspace did not return a page body.")
    return applications_body


def _authenticated_application_smoke_test(
    base_url: str,
    *,
    email: str,
    password: str,
    timeout: float,
    keep_test_application: bool,
) -> tuple[str, bool, bool]:
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))

    login_url = urljoin(f"{base_url}/", "login.html")
    _, _, login_body = _open(
        opener,
        _request(login_url),
        timeout=timeout,
        action="Login page request",
    )
    login_csrf = _csrf_token(login_body, page_name="the login page")

    login_post_url = urljoin(f"{base_url}/", "api/login")
    _, final_login_url, _ = _open(
        opener,
        _request(
            login_post_url,
            data={"email": email, "password": password, "csrf_token": login_csrf},
        ),
        timeout=timeout,
        action="Deployment validation sign-in",
    )
    if urlparse(final_login_url).path.endswith("/login.html"):
        raise ValidationFailure("Sign-in redirected back to the login page.")

    applications_url = urljoin(f"{base_url}/", "applications/?tab=applications")
    applications_body = _validate_authenticated_workspace_routes(
        opener,
        base_url,
        timeout=timeout,
    )
    create_csrf = _csrf_token(
        applications_body, page_name="the Application Builder page"
    )

    unique_suffix = f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
    company = f"Deployment Validation {unique_suffix}"
    role = f"Storage Smoke Test {unique_suffix}"
    create_url = urljoin(
        f"{base_url}/", "applications/applications/create"
    )

    application_id: str | None = None
    retrieved = False
    cleanup_succeeded = False
    latest_body = applications_body
    try:
        _, _, create_body = _open(
            opener,
            _request(
                create_url,
                data={
                    "csrf_token": create_csrf,
                    "company": company,
                    "role": role,
                    "status": "draft",
                    "notes": "Created automatically by deployment validation.",
                },
            ),
            timeout=timeout,
            action="Test application creation",
        )
        latest_body = create_body
        application_id = _find_application_card(
            create_body, company=company, role=role
        )
        if application_id is None:
            _, _, refreshed_body = _open(
                opener,
                _request(applications_url),
                timeout=timeout,
                action="Test application listing",
            )
            latest_body = refreshed_body
            application_id = _find_application_card(
                refreshed_body, company=company, role=role
            )
        if application_id is None:
            raise ValidationFailure(
                "The test application POST completed, but the created record was not listed."
            )

        retrieve_url = (
            f"{applications_url}&application_id={quote(application_id, safe='')}"
        )
        _, _, retrieve_body = _open(
            opener,
            _request(retrieve_url),
            timeout=timeout,
            action="Test application retrieval",
        )
        latest_body = retrieve_body
        retrieved_id = _find_application_card(
            retrieve_body, company=company, role=role
        )
        if retrieved_id != application_id:
            raise ValidationFailure(
                "The created test application could not be retrieved from the deployed store."
            )
        retrieved = True
    finally:
        if application_id and not keep_test_application:
            try:
                cleanup_csrf = _csrf_token(
                    latest_body, page_name="the Application Builder cleanup page"
                )
                delete_url = urljoin(
                    f"{base_url}/",
                    f"applications/applications/{quote(application_id, safe='')}/delete",
                )
                _open(
                    opener,
                    _request(delete_url, data={"csrf_token": cleanup_csrf}),
                    timeout=timeout,
                    action="Test application cleanup",
                )
                cleanup_succeeded = True
            except ValidationFailure as exc:
                print(
                    f"WARNING: Test application {application_id} was not removed: {exc}",
                    file=sys.stderr,
                )
        if application_id and not keep_test_application and not cleanup_succeeded and retrieved:
            print(
                "WARNING: The deployment validation record remains in Job Applications.",
                file=sys.stderr,
            )

    if application_id is None:
        raise ValidationFailure("The deployment smoke test did not create an application ID.")
    return application_id, retrieved, cleanup_succeeded


def _environment_flag(name: str) -> bool:
    return os.getenv(name, "").strip().casefold() in {"1", "true", "yes", "on"}


def _build_parser() -> argparse.ArgumentParser:
    root = _repo_root()
    parser = argparse.ArgumentParser(
        description="Validate the live Réunia Career Bridge Lightsail deployment."
    )
    parser.add_argument(
        "--region",
        default=os.getenv("AWS_REGION", DEFAULT_REGION),
        help=f"AWS region (default: AWS_REGION or {DEFAULT_REGION}).",
    )
    parser.add_argument(
        "--service-name",
        default=os.getenv("LIGHTSAIL_SERVICE", DEFAULT_SERVICE_NAME),
        help=(
            "Lightsail container service name "
            f"(default: LIGHTSAIL_SERVICE or {DEFAULT_SERVICE_NAME})."
        ),
    )
    parser.add_argument(
        "--container-name",
        default=os.getenv("LIGHTSAIL_CONTAINER_NAME", "") or None,
        help="Application container name when it cannot be inferred from publicEndpoint.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("CAREER_BRIDGE_BASE_URL", ""),
        help="Deployed application base URL (or CAREER_BRIDGE_BASE_URL).",
    )
    parser.add_argument(
        "--email",
        default=os.getenv("DEPLOYMENT_VALIDATION_EMAIL", ""),
        help="Deployment smoke-test account email (or DEPLOYMENT_VALIDATION_EMAIL).",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("DEPLOYMENT_VALIDATION_PASSWORD", ""),
        help="Deployment smoke-test account password (or DEPLOYMENT_VALIDATION_PASSWORD).",
    )
    parser.add_argument(
        "--aws-cli",
        default=os.getenv("AWS_CLI", "aws"),
        help="AWS CLI executable (default: aws).",
    )
    parser.add_argument(
        "--dockerfile",
        type=Path,
        default=root / "Dockerfile",
        help="Dockerfile whose image CMD is deployed.",
    )
    parser.add_argument(
        "--deployment-doc",
        type=Path,
        default=root / "docs" / "deployment" / "lightsail.md",
        help="Deployment document that must contain the data-loss warning.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("DEPLOYMENT_VALIDATION_TIMEOUT", "20")),
        help="HTTP timeout in seconds (default: 20).",
    )
    parser.add_argument(
        "--keep-test-application",
        action="store_true",
        help="Do not remove the created smoke-test application.",
    )
    parser.add_argument(
        "--allow-demo-storage",
        action="store_true",
        default=_environment_flag(
            "CAREER_BRIDGE_ALLOW_DEMO_STORAGE_IN_PRODUCTION"
        ),
        help=(
            "Accept non-durable workflow-memory/DynamoDB/local-document storage. The flag name is retained for compatibility; use it only "
            "with the explicit non-durable production override."
        ),
    )
    return parser


def _require(value: str, description: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValidationFailure(f"{description} is required.")
    return normalized


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    try:
        base_url = _normalize_base_url(
            _require(args.base_url, "--base-url or CAREER_BRIDGE_BASE_URL")
        )
        email = _require(
            args.email, "--email or DEPLOYMENT_VALIDATION_EMAIL"
        )
        password = _require(
            args.password, "--password or DEPLOYMENT_VALIDATION_PASSWORD"
        )

        _validate_documentation(args.deployment_doc)
        print("PASS  Deployment documentation contains the redeploy data-loss warning.")

        service_payload = _run_aws_get_container_services(
            aws_cli=args.aws_cli,
            region=args.region,
            service_name=args.service_name,
        )
        service = _select_live_service(
            service_payload, service_name=args.service_name
        )
        live_container = _select_live_container(
            service, requested_name=args.container_name
        )
        _validate_no_command_override(live_container)

        health = _validate_health(
            base_url,
            timeout=args.timeout,
            allow_demo_storage=args.allow_demo_storage,
        )
        durability = health["application_builder"]["durability"]
        demo_storage = health["application_builder"] == DEMO_STORAGE_STATUS
        print(
            "PASS  /health returned 200, status=ok, and approved storage "
            f"configuration ({durability})."
        )

        scale = _validate_scale(
            service, require_single_node=demo_storage
        )
        image_command = _parse_dockerfile_command(args.dockerfile)
        workers, threads = _validate_image_command(
            image_command, require_single_worker=demo_storage
        )
        if demo_storage:
            print(
                "PASS  Non-durable storage is constrained to Lightsail scale=1 and "
                "Gunicorn workers=1."
            )
        else:
            print(
                "PASS  Persistent storage permits multi-node/multi-worker operation "
                f"(Lightsail scale={scale}; Gunicorn workers={workers}; "
                f"threads={threads})."
            )
        print("PASS  Live container has no Lightsail command override.")
        if live_container.image:
            print(f"INFO  Current Lightsail image: {live_container.image}")

        application_id, _, cleanup_succeeded = _authenticated_application_smoke_test(
            base_url,
            email=email,
            password=password,
            timeout=args.timeout,
            keep_test_application=args.keep_test_application,
        )
        if args.keep_test_application:
            cleanup_status = "kept by request"
        elif cleanup_succeeded:
            cleanup_status = "removed"
        else:
            cleanup_status = "cleanup failed; remove it manually"
        print(
            "PASS  Test application was created and retrieved successfully "
            f"(id={application_id}; cleanup={cleanup_status})."
        )
    except ValidationFailure as exc:
        print("", file=sys.stderr)
        print("=" * 68, file=sys.stderr)
        print("DEPLOYMENT VALIDATION FAILED", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("=" * 68, file=sys.stderr)
        return 1

    print("\nDEPLOYMENT VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
