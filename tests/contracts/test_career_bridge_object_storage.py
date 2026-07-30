"""Contracts for Career Bridge document and large-artifact object storage."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]
RESUME_TAYLOR_ROOT = ROOT / "products" / "resume_taylor"


class FakeS3Client:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(dict(kwargs))
        self.items[(kwargs["Bucket"], kwargs["Key"])] = bytes(kwargs["Body"])
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        value = self.items.get((kwargs["Bucket"], kwargs["Key"]))
        if value is None:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
                "GetObject",
            )
        return {"Body": io.BytesIO(value)}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self.delete_calls.append(dict(kwargs))
        self.items.pop((kwargs["Bucket"], kwargs["Key"]), None)
        return {}


class CareerBridgeObjectStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(RESUME_TAYLOR_ROOT))
        from resume_tailor.object_storage import (
            CareerBridgeObjectStore,
            LocalCareerBridgeObjectStore,
            ObjectNotFoundError,
            ObjectStorageError,
            S3CareerBridgeObjectStore,
            application_object_key,
            configured_document_backend,
            create_document_store,
            owner_namespace,
            workflow_object_key,
        )
        from resume_tailor.storage import StorageBackendConfigurationError

        cls.protocol = CareerBridgeObjectStore
        cls.local_class = LocalCareerBridgeObjectStore
        cls.s3_class = S3CareerBridgeObjectStore
        cls.not_found = ObjectNotFoundError
        cls.storage_error = ObjectStorageError
        cls.configuration_error = StorageBackendConfigurationError
        cls.application_object_key = staticmethod(application_object_key)
        cls.workflow_object_key = staticmethod(workflow_object_key)
        cls.owner_namespace = staticmethod(owner_namespace)
        cls.configured_document_backend = staticmethod(configured_document_backend)
        cls.create_document_store = staticmethod(create_document_store)

    @classmethod
    def tearDownClass(cls) -> None:
        if sys.path and sys.path[0] == str(RESUME_TAYLOR_ROOT):
            sys.path.pop(0)

    def test_local_store_round_trip_delete_and_path_containment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self.local_class(directory)
            self.assertIsInstance(store, self.protocol)
            store.put(
                "career-bridge/users/abc/resume.docx",
                b"resume",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            self.assertEqual(
                store.get("career-bridge/users/abc/resume.docx"), b"resume"
            )
            store.delete("career-bridge/users/abc/resume.docx")
            with self.assertRaises(self.not_found):
                store.get("career-bridge/users/abc/resume.docx")
            with self.assertRaises(self.storage_error):
                store.put("../../outside.bin", b"bad", "application/octet-stream")

    def test_s3_store_uses_private_server_side_encryption(self) -> None:
        client = FakeS3Client()
        store = self.s3_class(
            "career-bridge-documents",
            "us-west-2",
            client=client,
        )
        store.put(
            "career-bridge/users/abc/resume.pdf",
            b"pdf",
            "application/pdf",
            metadata={"artifact-type": "final-resume-pdf"},
        )
        call = client.put_calls[-1]
        self.assertEqual(call["Bucket"], "career-bridge-documents")
        self.assertEqual(call["ServerSideEncryption"], "AES256")
        self.assertEqual(call["ContentType"], "application/pdf")
        self.assertEqual(call["Metadata"]["artifact-type"], "final-resume-pdf")
        self.assertEqual(store.get(call["Key"]), b"pdf")
        store.delete(call["Key"])
        with self.assertRaises(self.not_found):
            store.get(call["Key"])

    def test_s3_store_supports_kms_encryption(self) -> None:
        client = FakeS3Client()
        store = self.s3_class(
            "career-bridge-documents",
            "us-west-2",
            client=client,
            kms_key_id="alias/career-bridge-documents",
        )
        store.put("key", b"value", "application/octet-stream")
        call = client.put_calls[-1]
        self.assertEqual(call["ServerSideEncryption"], "aws:kms")
        self.assertEqual(call["SSEKMSKeyId"], "alias/career-bridge-documents")

    def test_dynamodb_mode_requires_s3_and_an_explicit_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(self.configuration_error):
                self.create_document_store(
                    {
                        "CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND": "local",
                        "CAREER_BRIDGE_DOCUMENTS_LOCAL_PATH": directory,
                    },
                    require_s3=True,
                )
        with self.assertRaises(self.configuration_error):
            self.create_document_store(
                {"CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND": "s3"},
                require_s3=True,
            )

    def test_object_keys_hide_owner_identity_and_include_artifact_scope(self) -> None:
        config = {"CAREER_BRIDGE_DOCUMENTS_PREFIX": "career-bridge"}
        owner = "vincent@example.com"
        application_key = self.application_object_key(
            config,
            owner,
            "app-123",
            "Vincent Resume.docx",
            category="final-resume-docx",
            fingerprint="a" * 64,
        )
        workflow_key = self.workflow_object_key(
            config,
            owner,
            "secret-session-key",
            "original-resume",
            "resume.pdf",
            "b" * 64,
        )
        self.assertNotIn(owner, application_key)
        self.assertNotIn("secret-session-key", workflow_key)
        self.assertIn(self.owner_namespace(owner), application_key)
        self.assertIn("/applications/app-123/final-resume-docx/", application_key)
        self.assertIn("/workflows/", workflow_key)
        self.assertTrue(application_key.endswith("Vincent-Resume.docx"))

    def test_workflow_documents_are_externalized_before_state_save(self) -> None:
        builder = (RESUME_TAYLOR_ROOT / "app.py").read_text(encoding="utf-8")
        persist_start = builder.index("def _persist_workflow_documents(")
        persist_end = builder.index("def _hydrate_workflow_documents(", persist_start)
        helper = builder[persist_start:persist_end]
        after_start = builder.index("@application_builder_bp.after_request")
        after_end = builder.index("@application_builder_bp.context_processor", after_start)
        after_request = builder[after_start:after_end]
        self.assertIn('"final_resume_bytes"', helper)
        self.assertIn('"final_resume_pdf_bytes"', helper)
        self.assertIn("setattr(workflow_state, bytes_field, None)", helper)
        self.assertIn('"original-resume"', builder)
        self.assertIn("original_resume_key=source_object_key", builder)
        self.assertLess(
            after_request.index("_persist_workflow_documents("),
            after_request.index("saved = store.save("),
        )

    def test_invalid_document_backend_is_rejected(self) -> None:
        with self.assertRaises(self.configuration_error):
            self.configured_document_backend(
                {"CAREER_BRIDGE_DOCUMENT_STORAGE_BACKEND": "dynamodb"}
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
