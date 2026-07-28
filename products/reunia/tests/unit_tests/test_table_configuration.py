from __future__ import annotations

import pytest
from flask import Flask

from meeting_assistant import _validate_dynamodb_table_configuration
from scripts import (
    create_actions_table,
    create_admin_analytics_table,
    create_knowledge_table,
    create_live_qa_table,
    create_meeting_shares_table,
)


def _configured_app() -> Flask:
    app = Flask(__name__)
    app.config.update(
        USERS_TABLE_NAME="users-explicit",
        TRANSCRIPTS_TABLE_NAME="transcripts-explicit",
        ACTIONS_STORAGE_BACKEND="memory",
        ACTIONS_TABLE_NAME="",
        ANALYTICS_STORAGE_BACKEND="memory",
        ANALYTICS_TABLE_NAME="",
        MEETING_SHARES_STORAGE_BACKEND="local",
        MEETING_SHARES_TABLE_NAME="",
        LIVE_QA_STORAGE_BACKEND="redis",
        LIVE_QA_TABLE_NAME="",
        SUPPORT_STORAGE_BACKEND="memory",
        SUPPORT_REQUESTS_TABLE_NAME="",
        KNOWLEDGE_STORAGE_BACKEND="local",
        KNOWLEDGE_TABLE_NAME="",
    )
    return app


def test_optional_table_names_are_not_required_for_non_dynamodb_backends():
    _validate_dynamodb_table_configuration(_configured_app())


@pytest.mark.parametrize(
    ("backend_variable", "table_variable"),
    (
        ("ACTIONS_STORAGE_BACKEND", "ACTIONS_TABLE_NAME"),
        ("ANALYTICS_STORAGE_BACKEND", "ANALYTICS_TABLE_NAME"),
        ("MEETING_SHARES_STORAGE_BACKEND", "MEETING_SHARES_TABLE_NAME"),
        ("LIVE_QA_STORAGE_BACKEND", "LIVE_QA_TABLE_NAME"),
        ("SUPPORT_STORAGE_BACKEND", "SUPPORT_REQUESTS_TABLE_NAME"),
        ("KNOWLEDGE_STORAGE_BACKEND", "KNOWLEDGE_TABLE_NAME"),
    ),
)
def test_active_dynamodb_backends_require_explicit_table_names(
    backend_variable,
    table_variable,
):
    app = _configured_app()
    app.config[backend_variable] = "dynamodb"

    with pytest.raises(RuntimeError, match=table_variable):
        _validate_dynamodb_table_configuration(app)


@pytest.mark.parametrize("table_variable", ("USERS_TABLE_NAME", "TRANSCRIPTS_TABLE_NAME"))
def test_core_dynamodb_tables_always_require_explicit_names(table_variable):
    app = _configured_app()
    app.config[table_variable] = ""

    with pytest.raises(RuntimeError, match=table_variable):
        _validate_dynamodb_table_configuration(app)


@pytest.mark.parametrize(
    ("module", "environment_variable"),
    (
        (create_actions_table, "ACTIONS_TABLE_NAME"),
        (create_admin_analytics_table, "ANALYTICS_TABLE_NAME"),
        (create_knowledge_table, "KNOWLEDGE_TABLE_NAME"),
        (create_live_qa_table, "LIVE_QA_TABLE_NAME"),
        (create_meeting_shares_table, "MEETING_SHARES_TABLE_NAME"),
    ),
)
def test_table_creation_scripts_reject_missing_environment_variables(
    monkeypatch,
    module,
    environment_variable,
):
    monkeypatch.delenv(environment_variable, raising=False)

    with pytest.raises(RuntimeError, match=environment_variable):
        module._configured_table_name()


@pytest.mark.parametrize(
    ("module", "environment_variable"),
    (
        (create_actions_table, "ACTIONS_TABLE_NAME"),
        (create_admin_analytics_table, "ANALYTICS_TABLE_NAME"),
        (create_knowledge_table, "KNOWLEDGE_TABLE_NAME"),
        (create_live_qa_table, "LIVE_QA_TABLE_NAME"),
        (create_meeting_shares_table, "MEETING_SHARES_TABLE_NAME"),
    ),
)
def test_table_creation_scripts_use_exact_configured_names(
    monkeypatch,
    module,
    environment_variable,
):
    monkeypatch.setenv(environment_variable, "explicit-table-name")

    assert module._configured_table_name() == "explicit-table-name"
