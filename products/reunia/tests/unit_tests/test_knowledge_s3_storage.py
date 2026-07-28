from __future__ import annotations

from meeting_assistant.repositories.knowledge_file_store import S3KnowledgeFileStore


def test_s3_store_uses_configured_bucket_and_dedicated_credentials(monkeypatch):
    captured: dict = {}

    class FakeClient:
        def put_object(self, **kwargs):
            captured["put_object"] = kwargs

    def fake_client(service_name: str, **kwargs):
        captured["service_name"] = service_name
        captured["client_options"] = kwargs
        return FakeClient()

    monkeypatch.setattr(
        "meeting_assistant.repositories.knowledge_file_store.boto3.client",
        fake_client,
    )

    store = S3KnowledgeFileStore(
        "meeting-assistant-documents-dev-usw2",
        "us-west-2",
        access_key_id="bucket-key-id",
        secret_access_key="bucket-secret",
    )
    store.put("knowledge/user/file/document.txt", b"hello", "text/plain")

    assert captured["service_name"] == "s3"
    assert captured["client_options"] == {
        "region_name": "us-west-2",
        "aws_access_key_id": "bucket-key-id",
        "aws_secret_access_key": "bucket-secret",
    }
    assert captured["put_object"]["Bucket"] == "meeting-assistant-documents-dev-usw2"
    assert captured["put_object"]["Key"] == "knowledge/user/file/document.txt"
    assert captured["put_object"]["ServerSideEncryption"] == "AES256"


def test_s3_store_uses_standard_boto3_credentials_when_dedicated_key_is_absent(monkeypatch):
    captured: dict = {}

    def fake_client(service_name: str, **kwargs):
        captured["service_name"] = service_name
        captured["client_options"] = kwargs
        return object()

    monkeypatch.setattr(
        "meeting_assistant.repositories.knowledge_file_store.boto3.client",
        fake_client,
    )

    S3KnowledgeFileStore(
        "meeting-assistant-documents-dev-usw2",
        "us-west-2",
    )

    assert captured["service_name"] == "s3"
    assert captured["client_options"] == {"region_name": "us-west-2"}
