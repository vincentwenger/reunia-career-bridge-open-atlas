from __future__ import annotations

import zipfile
from io import BytesIO

from meeting_assistant.repositories.knowledge_file_store import LocalKnowledgeFileStore


def _xlsx_bytes() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return buffer.getvalue()


def _authenticate(client, user_id: str = "user-1") -> None:
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user_id


def _context() -> dict:
    return {
        "enabled": True,
        "company": "",
        "reference_link": "",
        "role": "",
        "type": "general",
        "domain": "",
        "audience": "",
        "answer_style": "",
        "response_mode": "ready_to_say",
        "audio_response_instructions": "",
        "clipboard_response_instructions": "",
        "objective": "",
        "free_text": "",
    }


def test_document_library_collection_and_file_crud(app, tmp_path):
    app.extensions["knowledge_file_store"] = LocalKnowledgeFileStore(tmp_path / "files")
    client = app.test_client()
    _authenticate(client)

    collection_response = client.post(
        "/api/knowledge/collections",
        json={"name": "Interview", "description": "Interview preparation files"},
    )
    assert collection_response.status_code == 201
    collection = collection_response.get_json()["collection"]

    upload_response = client.post(
        "/api/knowledge/files",
        data={
            "collection_id": collection["collection_id"],
            "tags": "job, interview",
            "description": "Role description",
            "files": (BytesIO(b"Principal AI Engineer requirements"), "role.md"),
        },
        content_type="multipart/form-data",
    )
    assert upload_response.status_code == 201
    uploaded = upload_response.get_json()["files"][0]
    assert uploaded["status"] == "ready"
    assert uploaded["collection_name"] == "Interview"
    assert uploaded["tags"] == ["job", "interview"]

    list_response = client.get("/api/knowledge/files")
    assert list_response.status_code == 200
    assert [item["file_id"] for item in list_response.get_json()["files"]] == [
        uploaded["file_id"]
    ]

    collection_list = client.get("/api/knowledge/collections").get_json()["collections"]
    assert collection_list[0]["file_count"] == 1

    non_empty_collection_delete = client.delete(
        f"/api/knowledge/collections/{collection['collection_id']}"
    )
    assert non_empty_collection_delete.status_code == 400
    assert "Delete all files" in non_empty_collection_delete.get_json()["error"]

    preview = client.get(f"/api/knowledge/files/{uploaded['file_id']}/preview")
    assert preview.status_code == 200
    assert preview.data == b"Principal AI Engineer requirements"
    assert preview.headers["Cache-Control"] == "private, no-store"

    download = client.get(f"/api/knowledge/files/{uploaded['file_id']}/download")
    assert download.status_code == 200
    assert "attachment" in download.headers["Content-Disposition"]

    delete = client.delete(f"/api/knowledge/files/{uploaded['file_id']}")
    assert delete.status_code == 200
    assert client.get("/api/knowledge/files").get_json()["files"] == []
    assert client.get(f"/api/knowledge/files/{uploaded['file_id']}/preview").status_code == 404

    collection_delete = client.delete(
        f"/api/knowledge/collections/{collection['collection_id']}"
    )
    assert collection_delete.status_code == 200
    assert collection_delete.get_json()["collection"]["collection_id"] == collection["collection_id"]
    assert client.get("/api/knowledge/collections").get_json()["collections"] == []


def test_empty_collection_can_be_deleted(app):
    client = app.test_client()
    _authenticate(client)

    collection = client.post(
        "/api/knowledge/collections",
        json={"name": "Temporary collection"},
    ).get_json()["collection"]

    response = client.delete(
        f"/api/knowledge/collections/{collection['collection_id']}"
    )

    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert client.get("/api/knowledge/collections").get_json()["collections"] == []


def test_document_library_is_scoped_to_authenticated_user(app, tmp_path):
    app.extensions["knowledge_file_store"] = LocalKnowledgeFileStore(tmp_path / "files")
    owner = app.test_client()
    _authenticate(owner, "owner")
    upload = owner.post(
        "/api/knowledge/files",
        data={"files": (BytesIO(b"private notes"), "notes.txt")},
        content_type="multipart/form-data",
    )
    file_id = upload.get_json()["files"][0]["file_id"]

    other_user = app.test_client()
    _authenticate(other_user, "other")
    assert other_user.get("/api/knowledge/files").get_json()["files"] == []
    assert other_user.get(f"/api/knowledge/files/{file_id}/download").status_code == 404
    assert other_user.delete(f"/api/knowledge/files/{file_id}").status_code == 404

    collection = owner.post(
        "/api/knowledge/collections",
        json={"name": "Owner only"},
    ).get_json()["collection"]
    assert other_user.delete(
        f"/api/knowledge/collections/{collection['collection_id']}"
    ).status_code == 404


def test_document_library_validates_uploaded_content(app, tmp_path):
    app.extensions["knowledge_file_store"] = LocalKnowledgeFileStore(tmp_path / "files")
    client = app.test_client()
    _authenticate(client)

    response = client.post(
        "/api/knowledge/files",
        data={"files": (BytesIO(b"not really a PDF"), "fake.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "valid PDF" in response.get_json()["error"]


def test_document_library_accepts_excel_files(app, tmp_path):
    app.extensions["knowledge_file_store"] = LocalKnowledgeFileStore(tmp_path / "files")
    client = app.test_client()
    _authenticate(client)

    xlsx_response = client.post(
        "/api/knowledge/files",
        data={"files": (BytesIO(_xlsx_bytes()), "forecast.xlsx")},
        content_type="multipart/form-data",
    )
    assert xlsx_response.status_code == 201
    xlsx_file = xlsx_response.get_json()["files"][0]
    assert xlsx_file["extension"] == "xlsx"
    assert xlsx_file["content_type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    xls_content = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + (b"\x00" * 64)
    xls_response = client.post(
        "/api/knowledge/files",
        data={"files": (BytesIO(xls_content), "budget.xls")},
        content_type="multipart/form-data",
    )
    assert xls_response.status_code == 201
    xls_file = xls_response.get_json()["files"][0]
    assert xls_file["extension"] == "xls"
    assert xls_file["content_type"] == "application/vnd.ms-excel"


def test_document_library_rejects_invalid_excel_content(app, tmp_path):
    app.extensions["knowledge_file_store"] = LocalKnowledgeFileStore(tmp_path / "files")
    client = app.test_client()
    _authenticate(client)

    response = client.post(
        "/api/knowledge/files",
        data={"files": (BytesIO(b"not an Excel workbook"), "fake.xlsx")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "valid XLSX" in response.get_json()["error"]


def test_document_library_page_renders_persisted_files(app, tmp_path, monkeypatch):
    app.extensions["knowledge_file_store"] = LocalKnowledgeFileStore(tmp_path / "files")
    monkeypatch.setattr(
        "meeting_assistant.blueprints.knowledge.routes.UserService",
        lambda: type("FakeUserService", (), {"get_assistant_context": lambda self, user_id: _context()})(),
    )
    client = app.test_client()
    _authenticate(client)
    upload = client.post(
        "/api/knowledge/files",
        data={"files": (BytesIO(b"agenda"), "agenda.txt")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 201

    page = client.get("/knowledge.html?view=library")
    assert page.status_code == 200
    assert b"agenda.txt" in page.data
    assert b'accept=".pdf,.docx,.xlsx,.xls,.txt,.md"' in page.data
    assert b"Excel (XLSX or XLS)" in page.data



def test_guided_preparation_flow_renders_skip_and_continue_actions(app, monkeypatch):
    monkeypatch.setattr(
        "meeting_assistant.blueprints.knowledge.routes.UserService",
        lambda: type("FakeUserService", (), {"get_assistant_context": lambda self, user_id: _context()})(),
    )
    client = app.test_client()
    _authenticate(client)

    materials = client.get("/knowledge.html?view=materials&guided=1")
    assert materials.status_code == 200
    assert b"Guided meeting flow" in materials.data
    assert b"Continue to AI Context" in materials.data
    assert b"view=context&amp;guided=1" in materials.data

    context = client.get("/knowledge.html?view=context&guided=1")
    assert context.status_code == 200
    assert b"Continue to Preparation Check" in context.data
    assert b"view=search&amp;guided=1" in context.data

    search = client.get("/knowledge.html?view=search&guided=1")
    assert search.status_code == 200
    assert b"Continue to Recorder" in search.data
    assert b"meeting-recorder?guided=1" in search.data
    assert b"Skip this step" in search.data

def test_meeting_materials_renders_editable_meeting_details(app, monkeypatch):
    monkeypatch.setattr(
        "meeting_assistant.blueprints.knowledge.routes.UserService",
        lambda: type("FakeUserService", (), {"get_assistant_context": lambda self, user_id: _context()})(),
    )
    client = app.test_client()
    _authenticate(client)

    response = client.get("/knowledge.html?view=materials")

    assert response.status_code == 200
    assert b'id="meetingDetailsTitleInput"' in response.data
    assert b'id="meetingDetailsParticipants"' in response.data
    assert b'id="meetingDetailsPurpose"' in response.data
    assert b'id="saveUpcomingMeetingDetails"' in response.data


def test_upcoming_meeting_details_can_be_updated(app):
    client = app.test_client()
    _authenticate(client)

    created = client.post(
        "/api/knowledge/upcoming-meetings",
        json={
            "title": "Initial planning",
            "purpose": "Draft the launch plan",
            "participants": ["Ana", "Marcus"],
        },
    ).get_json()["meeting"]

    updated_response = client.put(
        f"/api/knowledge/upcoming-meetings/{created['id']}",
        json={
            "title": "Launch planning",
            "purpose": "Approve the launch plan",
            "participants": ["Ana", "Marcus", "Priya"],
            "scheduled_at": "2026-08-03T17:00:00.000Z",
        },
    )

    assert updated_response.status_code == 200
    updated = updated_response.get_json()["meeting"]
    assert updated["title"] == "Launch planning"
    assert updated["purpose"] == "Approve the launch plan"
    assert updated["participants"] == ["Ana", "Marcus", "Priya"]
    assert updated["scheduled_at"] == "2026-08-03T17:00:00.000Z"

    listed = client.get("/api/knowledge/upcoming-meetings").get_json()["meetings"]
    assert listed[0]["title"] == "Launch planning"
    assert listed[0]["purpose"] == "Approve the launch plan"
    assert listed[0]["participants"] == ["Ana", "Marcus", "Priya"]


def test_upcoming_meeting_can_be_deleted_with_temporary_files(app, tmp_path):
    storage_root = tmp_path / "files"
    app.extensions["knowledge_file_store"] = LocalKnowledgeFileStore(storage_root)
    client = app.test_client()
    _authenticate(client)

    created = client.post(
        "/api/knowledge/upcoming-meetings",
        json={"title": "Planning session", "activate": True},
    )
    assert created.status_code == 201
    meeting_id = created.get_json()["meeting"]["id"]

    uploaded = client.post(
        f"/api/knowledge/meeting-materials/{meeting_id}/temporary-files",
        data={"files": (BytesIO(b"temporary agenda"), "agenda.txt")},
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 201
    temporary_file = uploaded.get_json()["files"][0]

    repository = app.extensions["knowledge_repository"]
    stored_meeting = repository.get_meeting("user-1", meeting_id)
    object_key = stored_meeting["temporary_files"][0]["object_key"]
    assert (storage_root / object_key).exists()

    deleted = client.delete(f"/api/knowledge/upcoming-meetings/{meeting_id}")
    assert deleted.status_code == 200
    assert deleted.get_json()["meeting"]["id"] == meeting_id
    assert repository.get_meeting("user-1", meeting_id) is None
    assert repository.get_active_meeting_id("user-1") == ""
    assert not (storage_root / object_key).exists()
    assert client.get("/api/knowledge/upcoming-meetings").get_json()["meetings"] == []
    assert temporary_file["name"] == "agenda.txt"


def test_upcoming_meeting_delete_is_scoped_to_owner(app):
    owner = app.test_client()
    _authenticate(owner, "owner")
    meeting = owner.post(
        "/api/knowledge/upcoming-meetings",
        json={"title": "Owner meeting"},
    ).get_json()["meeting"]

    other = app.test_client()
    _authenticate(other, "other")
    response = other.delete(
        f"/api/knowledge/upcoming-meetings/{meeting['id']}"
    )

    assert response.status_code == 404
    assert owner.get("/api/knowledge/upcoming-meetings").get_json()["meetings"][0]["id"] == meeting["id"]



def test_deleting_inactive_upcoming_meeting_preserves_active_meeting(app):
    client = app.test_client()
    _authenticate(client)

    active = client.post(
        "/api/knowledge/upcoming-meetings",
        json={"title": "Active meeting", "activate": True},
    ).get_json()["meeting"]
    inactive = client.post(
        "/api/knowledge/upcoming-meetings",
        json={"title": "Inactive meeting", "activate": False},
    ).get_json()["meeting"]

    response = client.delete(
        f"/api/knowledge/upcoming-meetings/{inactive['id']}"
    )

    assert response.status_code == 200
    payload = client.get("/api/knowledge/upcoming-meetings").get_json()
    assert payload["active_meeting_id"] == active["id"]
    assert [meeting["id"] for meeting in payload["meetings"]] == [active["id"]]
