from __future__ import annotations

from io import BytesIO

from werkzeug.datastructures import FileStorage

from meeting_assistant.services.browser_recorder_service import BrowserRecorderService


class _FakeTranscriptions:
    def __init__(self, responses):
        self.responses = list(responses)

    def create(self, **kwargs):
        return self.responses.pop(0)


class _FakeAudio:
    def __init__(self, responses):
        self.transcriptions = _FakeTranscriptions(responses)


class _FakeOpenAIClient:
    def __init__(self, responses):
        self.audio = _FakeAudio(responses)


class _FakeTranscriptService:
    def __init__(self):
        self.user_id = None
        self.data = None

    def create(self, user_id, data):
        self.user_id = user_id
        self.data = data
        return {
            "meeting_id": data["meeting_id"],
            "timestamp": data["timestamp"],
        }


def _upload(name):
    return FileStorage(
        stream=BytesIO(b"fake browser audio"),
        filename=name,
        content_type="audio/webm",
    )


def test_recorder_page_requires_login(app):
    response = app.test_client().get("/meeting-recorder")
    assert response.status_code == 302
    assert "/login.html" in response.headers["Location"]


def test_recorder_page_is_available_to_authenticated_user(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "user-123"

    response = client.get("/meeting-recorder")
    assert response.status_code == 200
    assert b"Browser Meeting Recorder" in response.data
    assert b'id="captureMeetingAudio" type="checkbox" checked' in response.data
    assert b'id="openLiveQALink"' in response.data
    assert b'href="/live-qa.html"' in response.data
    assert b'target="_blank"' in response.data
    assert b'id="sendErrorToSupportButton"' in response.data
    assert b'Send Error to Support' in response.data
    assert b'Copy Error Details' not in response.data



def test_guided_recorder_is_required_final_step(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "user-123"

    response = client.get("/meeting-recorder?guided=1")

    assert response.status_code == 200
    assert b"Final step: record your meeting" in response.data
    assert b"There is no Skip option on this step" in response.data
    assert b"recorder-required-badge" in response.data

def test_recorder_api_rejects_unauthenticated_user(app):
    response = app.test_client().post("/api/meeting-recorder")
    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication required."}


def test_browser_recorder_merges_timestamped_sources(app):
    transcript_service = _FakeTranscriptService()
    client = _FakeOpenAIClient(
        [
            {
                "text": "My answer",
                "segments": [
                    {"start": 2.0, "end": 3.0, "text": "My answer"},
                ],
            },
            {
                "text": "The question",
                "segments": [
                    {"start": 1.0, "end": 1.8, "text": "The question"},
                ],
            },
        ]
    )

    with app.app_context():
        result = BrowserRecorderService(
            transcript_service=transcript_service,
            client=client,
        ).create_meeting(
            user_id="user-123",
            started_at="2026-07-12T18:00:00Z",
            microphone_audio=_upload("microphone.webm"),
            speaker_audio=_upload("meeting.webm"),
        )

    assert result["source_count"] == 2
    assert transcript_service.user_id == "user-123"
    assert "meeting_name" not in transcript_service.data
    assert transcript_service.data["transcript"].splitlines() == [
        "00:00:01 [SPEAKER] The question",
        "00:00:02 [MICROPHONE] My answer",
    ]


def test_recorder_api_accepts_authenticated_multipart_upload(app, monkeypatch):
    from meeting_assistant.blueprints.recorder import routes as recorder_routes

    class _FakeRecorderJobService:
        def queue_meeting(self, **kwargs):
            assert kwargs["user_id"] == "user-123"
            assert kwargs["microphone_audio"].filename == "microphone.webm"
            assert kwargs["requested_reference_id"] == "test-reference-1234"
            return {
                "job_id": "test-reference-1234",
                "reference_id": "test-reference-1234",
                "status": "queued",
                "stage": "queued",
            }

    monkeypatch.setattr(
        recorder_routes,
        "BrowserRecorderJobService",
        _FakeRecorderJobService,
    )

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "user-123"

    response = client.post(
        "/api/meeting-recorder",
        data={
            "client_reference_id": "test-reference-1234",
            "started_at": "2026-07-12T18:00:00Z",
            "microphone_audio": (
                BytesIO(b"browser audio"),
                "microphone.webm",
                "audio/webm",
            ),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["job_id"] == "test-reference-1234"
    assert payload["status_url"] == "/api/meeting-recorder/jobs/test-reference-1234"
    assert payload["review_url"] == "/meeting-review.html"


def test_recorder_job_status_is_available_to_owner(app, monkeypatch):
    from meeting_assistant.blueprints.recorder import routes as recorder_routes

    class _FakeRecorderJobService:
        def get_job(self, **kwargs):
            assert kwargs == {"job_id": "job-123456789012", "user_id": "user-123"}
            return {
                "job_id": "job-123456789012",
                "reference_id": "job-123456789012",
                "status": "complete",
                "stage": "complete",
            }

    monkeypatch.setattr(
        recorder_routes,
        "BrowserRecorderJobService",
        _FakeRecorderJobService,
    )

    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "user-123"

    response = client.get("/api/meeting-recorder/jobs/job-123456789012")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "complete"
    assert payload["review_url"] == "/meeting-review.html"


def test_recorder_job_runs_asynchronously_and_exposes_timeline(app, tmp_path):
    import time
    from pathlib import Path

    from meeting_assistant.services.browser_recorder_job_service import BrowserRecorderJobService
    from meeting_assistant.services.browser_recorder_service import SavedUpload

    class _FakeRecorder:
        def save_upload(self, upload, *, destination_directory=None, source="AUDIO"):
            path = Path(destination_directory) / f"{source.lower()}.webm"
            content = upload.stream.read()
            path.write_bytes(content)
            return SavedUpload(path, "audio/webm", len(content))

        def create_meeting_from_paths(self, **kwargs):
            progress = kwargs["progress_callback"]
            progress("transcribing_microphone", "Transcribing microphone audio.")
            progress("analyzing", "Analyzing meeting.")
            progress("saving", "Saving meeting.")
            return {
                "meeting_id": "browser-async-test",
                "timestamp": "2026-07-12T18:00:00+00:00",
                "message": "saved",
                "source_count": len(kwargs["source_paths"]),
            }

    app.config["RECORDER_JOB_DIR"] = str(tmp_path)
    service = BrowserRecorderJobService(_FakeRecorder())

    with app.app_context():
        queued = service.queue_meeting(
            user_id="user-123",
            started_at="2026-07-12T18:00:00Z",
            microphone_audio=_upload("microphone.webm"),
            speaker_audio=None,
            requested_reference_id="job-async-1234567890",
        )

        status = queued
        for _ in range(100):
            status = service.get_job(job_id=queued["job_id"], user_id="user-123")
            if status["status"] in {"complete", "failed"}:
                break
            time.sleep(0.01)

    assert status["status"] == "complete"
    assert status["meeting_id"] == "browser-async-test"
    assert [event["stage"] for event in status["events"]] == [
        "uploading",
        "queued",
        "transcribing_microphone",
        "analyzing",
        "saving",
        "complete",
    ]
    assert not list(tmp_path.glob("job-async-1234567890/*.webm"))


def test_recorder_filters_silent_low_confidence_and_repeated_segments(app):
    transcript_service = _FakeTranscriptService()
    repeated_text = "Oh, I'm going to take you back home."
    client = _FakeOpenAIClient(
        [
            {
                "text": repeated_text,
                "segments": [
                    {
                        "start": 10.0,
                        "end": 11.0,
                        "text": "This is genuine speech.",
                        "no_speech_prob": 0.05,
                        "avg_logprob": -0.20,
                        "compression_ratio": 1.1,
                    },
                    {
                        "start": 20.0,
                        "end": 21.0,
                        "text": "Invented during silence.",
                        "no_speech_prob": 0.91,
                        "avg_logprob": -1.20,
                        "compression_ratio": 1.2,
                    },
                    {
                        "start": 30.0,
                        "end": 31.0,
                        "text": repeated_text,
                        "no_speech_prob": 0.10,
                        "avg_logprob": -0.30,
                        "compression_ratio": 1.1,
                    },
                    {
                        "start": 40.0,
                        "end": 41.0,
                        "text": repeated_text,
                        "no_speech_prob": 0.10,
                        "avg_logprob": -0.30,
                        "compression_ratio": 1.1,
                    },
                    {
                        "start": 50.0,
                        "end": 51.0,
                        "text": repeated_text,
                        "no_speech_prob": 0.10,
                        "avg_logprob": -0.30,
                        "compression_ratio": 1.1,
                    },
                    {
                        "start": 60.0,
                        "end": 61.0,
                        "text": "Low confidence compressed text.",
                        "no_speech_prob": 0.20,
                        "avg_logprob": -1.80,
                        "compression_ratio": 3.0,
                    },
                ],
            }
        ]
    )

    with app.app_context():
        result = BrowserRecorderService(
            transcript_service=transcript_service,
            client=client,
        ).create_meeting(
            user_id="user-123",
            started_at="2026-07-12T18:00:00Z",
            microphone_audio=_upload("microphone.webm"),
            speaker_audio=None,
        )

    assert transcript_service.data["transcript"].splitlines() == [
        "00:00:10 [MICROPHONE] This is genuine speech.",
        f"00:00:30 [MICROPHONE] {repeated_text}",
    ]
    assert "Invented during silence." in transcript_service.data["raw_transcript"]
    assert transcript_service.data["transcript_quality"] == {
        "total_segments": 6,
        "kept_segments": 2,
        "removed_no_speech": 1,
        "removed_low_confidence": 1,
        "removed_repetitions": 2,
        "removed_total": 4,
        "adjusted": True,
    }
    assert result["quality_warning"].startswith(
        "Transcript quality protection removed 4"
    )


def test_recorder_supplies_configured_transcription_language(app):
    transcript_service = _FakeTranscriptService()
    client = _FakeOpenAIClient(
        [{"text": "Hello", "segments": [{"start": 0, "end": 1, "text": "Hello"}]}]
    )
    calls = []
    original_create = client.audio.transcriptions.create

    def capture_create(**kwargs):
        calls.append(kwargs)
        return original_create(**kwargs)

    client.audio.transcriptions.create = capture_create
    app.config["AUDIO_TRANSCRIPTION_LANGUAGE"] = "en"

    with app.app_context():
        BrowserRecorderService(
            transcript_service=transcript_service,
            client=client,
        ).create_meeting(
            user_id="user-123",
            started_at="2026-07-12T18:00:00Z",
            microphone_audio=_upload("microphone.webm"),
            speaker_audio=None,
        )

    assert calls[0]["language"] == "en"
    assert calls[0]["response_format"] == "verbose_json"
    assert calls[0]["timestamp_granularities"] == ["segment"]


def test_recorder_retains_raw_transcription_diagnostics_for_job(app, tmp_path):
    import json

    transcript_service = _FakeTranscriptService()
    audio_path = tmp_path / "microphone.webm"
    audio_path.write_bytes(b"fake browser audio")
    client = _FakeOpenAIClient(
        [
            {
                "language": "en",
                "duration": 12.0,
                "text": "Hello from the meeting.",
                "segments": [
                    {
                        "start": 1.0,
                        "end": 2.0,
                        "text": "Hello from the meeting.",
                        "no_speech_prob": 0.03,
                        "avg_logprob": -0.2,
                        "compression_ratio": 1.1,
                    }
                ],
            }
        ]
    )

    with app.app_context():
        BrowserRecorderService(
            transcript_service=transcript_service,
            client=client,
        ).create_meeting_from_paths(
            user_id="user-123",
            started_at="2026-07-12T18:00:00Z",
            source_paths=[("MICROPHONE", audio_path)],
            reference_id="job-raw-diagnostics-1234",
        )

    diagnostics = json.loads(
        (tmp_path / "transcription_raw.json").read_text(encoding="utf-8")
    )
    assert diagnostics["reference_id"] == "job-raw-diagnostics-1234"
    assert diagnostics["responses"]["MICROPHONE"]["language"] == "en"
    assert diagnostics["raw_transcript"] == (
        "00:00:01 [MICROPHONE] Hello from the meeting."
    )
    assert diagnostics["quality_report"]["removed_segments"] == []


def test_segmented_recording_applies_each_segment_offset(app, tmp_path):
    transcript_service = _FakeTranscriptService()
    first_path = tmp_path / "microphone-0000.webm"
    second_path = tmp_path / "microphone-0001.webm"
    first_path.write_bytes(b"first segment")
    second_path.write_bytes(b"second segment")
    client = _FakeOpenAIClient(
        [
            {
                "text": "Opening comment",
                "segments": [{"start": 1.0, "end": 2.0, "text": "Opening comment"}],
            },
            {
                "text": "Later comment",
                "segments": [{"start": 2.0, "end": 3.0, "text": "Later comment"}],
            },
        ]
    )

    with app.app_context():
        result = BrowserRecorderService(
            transcript_service=transcript_service,
            client=client,
        ).create_meeting_from_paths(
            user_id="user-123",
            started_at="2026-07-12T18:00:00Z",
            source_paths=[
                {
                    "source": "MICROPHONE",
                    "path": first_path,
                    "sequence": 0,
                    "offset_seconds": 0,
                    "duration_seconds": 600,
                },
                {
                    "source": "MICROPHONE",
                    "path": second_path,
                    "sequence": 1,
                    "offset_seconds": 600,
                    "duration_seconds": 600,
                },
            ],
        )

    assert transcript_service.data["transcript"].splitlines() == [
        "00:00:01 [MICROPHONE] Opening comment",
        "00:10:02 [MICROPHONE] Later comment",
    ]
    assert result["source_count"] == 1
    assert result["segment_count"] == 2


def test_oversized_recording_segment_uses_http_413_error(app):
    from meeting_assistant.utils.exceptions import PayloadTooLargeError

    app.config["RECORDER_MAX_FILE_BYTES"] = 4
    upload = FileStorage(
        stream=BytesIO(b"12345"),
        filename="segment.webm",
        content_type="audio/webm",
    )

    with app.app_context():
        try:
            BrowserRecorderService().save_upload(upload, source="MICROPHONE")
        except PayloadTooLargeError as exc:
            assert exc.status_code == 413
            assert "segment" in str(exc).lower()
        else:  # pragma: no cover - assertion guard
            raise AssertionError("Expected PayloadTooLargeError")


def test_segmented_upload_session_queues_persisted_segments(app, tmp_path):
    from pathlib import Path

    from meeting_assistant.services.browser_recorder_job_service import BrowserRecorderJobService
    from meeting_assistant.services.browser_recorder_service import SavedUpload

    captured = {}

    class _FakeRecorder:
        def save_upload(self, upload, *, destination_directory=None, source="AUDIO"):
            path = Path(destination_directory) / f"{source.lower()}.webm"
            content = upload.stream.read()
            path.write_bytes(content)
            return SavedUpload(path, "audio/webm", len(content))

        def create_meeting_from_paths(self, **kwargs):
            captured["source_paths"] = kwargs["source_paths"]
            return {
                "meeting_id": "browser-segment-test",
                "timestamp": "2026-07-12T18:00:00+00:00",
                "message": "saved",
                "source_count": 1,
                "segment_count": len(kwargs["source_paths"]),
            }

    app.config["RECORDER_JOB_DIR"] = str(tmp_path)
    app.config["RECORDER_JOB_QUEUE_BACKEND"] = "inline"
    service = BrowserRecorderJobService(_FakeRecorder())

    with app.app_context():
        session = service.create_upload_session(
            user_id="user-123",
            started_at="2026-07-12T18:00:00Z",
            requested_reference_id="segment-job-1234567890",
        )
        service.append_segment(
            job_id=session["job_id"],
            user_id="user-123",
            source="MICROPHONE",
            sequence=0,
            offset_seconds=0,
            duration_seconds=600,
            audio_segment=_upload("microphone-0000.webm"),
        )
        service.append_segment(
            job_id=session["job_id"],
            user_id="user-123",
            source="MICROPHONE",
            sequence=1,
            offset_seconds=600,
            duration_seconds=120,
            audio_segment=_upload("microphone-0001.webm"),
        )
        result = service.finalize_upload_session(
            job_id=session["job_id"],
            user_id="user-123",
            duration_seconds=720,
        )

    assert result["status"] == "complete"
    assert result["meeting_id"] == "browser-segment-test"
    assert result["segment_count"] == 2
    assert [item["offset_seconds"] for item in captured["source_paths"]] == [0.0, 600.0]


def test_recorder_records_transcription_cost(app):
    transcript_service = _FakeTranscriptService()
    client = _FakeOpenAIClient(
        [
            {
                "language": "en",
                "duration": 120.0,
                "text": "Hello",
                "segments": [{"start": 0, "end": 1, "text": "Hello"}],
            }
        ]
    )

    with app.app_context():
        BrowserRecorderService(
            transcript_service=transcript_service,
            client=client,
        ).create_meeting(
            user_id="user-123",
            started_at="2026-07-12T18:00:00Z",
            microphone_audio=_upload("microphone.webm"),
            speaker_audio=None,
        )

    events = app.extensions["analytics_repository"].list_usage_events(
        "ai_request",
        "user-123",
    )
    transcription_event = next(
        item for item in events
        if item.get("request_type") == "transcription"
    )
    assert transcription_event["feature"] == "meeting_transcription"
    assert transcription_event["audio_seconds"] == 120.0
    assert transcription_event["estimated_cost_usd"] == 0.012
