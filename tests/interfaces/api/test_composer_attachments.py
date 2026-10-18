from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.interfaces.api import routes_attachments, routes_tasks
from atlas.interfaces.api.dependencies import get_control_plane


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes_attachments, "ATTACHMENT_DIR", str(tmp_path))
    received = []
    created = {}

    async def create_task(command):
        received.append(command)
        task = SimpleNamespace(model_dump=lambda **_: {"id": "task_uploaded", "request": command.request})
        created[command.idempotency_key] = task
        return task

    async def task_for_idempotency_key(key):
        return created.get(key)

    app = FastAPI()
    app.include_router(routes_attachments.router)
    app.include_router(routes_tasks.router)
    app.dependency_overrides[get_control_plane] = lambda: SimpleNamespace(
        create_task=create_task, task_for_idempotency_key=task_for_idempotency_key
    )
    return TestClient(app), received, tmp_path


def test_attachment_only_task_contains_uploaded_content(client):
    http, received, _ = client
    upload = http.post("/attachments", files={"file": ("notes.md", b"Project deadline: Friday", "text/markdown")})
    assert upload.status_code == 200
    response = http.post(
        "/tasks", json={"request": "", "idempotency_key": "composer-request-123", "attachments": [upload.json()]}
    )
    assert response.status_code == 202
    assert "Project deadline: Friday" in received[0].request
    assert "attachment" in received[0].request.lower()


def test_image_upload_ocr_text_reaches_task(client):
    import shutil

    if not shutil.which("tesseract"):
        pytest.skip("local OCR runtime absent")
    pytest.importorskip("PIL")
    http, received, _ = client
    image = Path(__file__).parents[2] / "knowledge" / "fixtures" / "orchid_scan.png"
    upload = http.post("/attachments", files={"file": ("scan.png", image.read_bytes(), "image/png")})
    assert upload.status_code == 200
    assert upload.json()["type"] == "image"
    response = http.post(
        "/tasks", json={"request": "", "idempotency_key": "composer-image-123", "attachments": [upload.json()]}
    )
    assert response.status_code == 202
    assert "275 megawatts" in received[0].request


def test_unknown_attachment_is_rejected_before_execution(client):
    http, received, _ = client
    response = http.post(
        "/tasks",
        json={
            "request": "Read it",
            "idempotency_key": "composer-request-123",
            "attachments": [{"id": "local_fake", "type": "code"}],
        },
    )
    assert response.status_code == 404
    assert received == []


def test_upload_filename_cannot_select_storage_path(client):
    http, _, directory = client
    response = http.post("/attachments", files={"file": ("../../outside.md", b"text", "text/markdown")})
    assert response.status_code == 200
    assert len(list(directory.glob("*.txt"))) == 1
    assert response.json()["type"] == "markdown"


@pytest.mark.parametrize(
    "name,body,mime,status",
    [
        ("large.txt", b"a" * 65_537, "text/plain", 413),
        ("photo.png", b"\x89PNG\r\n", "image/png", 415),
        ("binary.txt", b"\x00\xff", "text/plain", 415),
    ],
)
def test_unusable_uploads_are_rejected_without_files(client, name, body, mime, status):
    http, _, directory = client
    response = http.post("/attachments", files={"file": (name, body, mime)})
    assert response.status_code == status
    assert list(directory.iterdir()) == []


def test_empty_task_is_still_rejected(client):
    http, received, _ = client
    response = http.post("/tasks", json={"request": "   ", "idempotency_key": "composer-request-123"})
    assert response.status_code == 422
    assert received == []


def test_created_task_retry_does_not_require_retained_attachment_files(client):
    http, received, directory = client
    upload = http.post("/attachments", files={"file": ("notes.md", b"Friday deadline", "text/markdown")})
    command = {"request": "Read it", "idempotency_key": "composer-request-123", "attachments": [upload.json()]}
    first = http.post("/tasks", json=command)
    assert first.status_code == 202
    for path in directory.iterdir():
        path.unlink()
    retried = http.post("/tasks", json=command)
    assert retried.status_code == 202
    assert retried.json() == first.json()
    assert len(received) == 1


def test_ref_type_mismatch_is_rejected(client):
    http, received, _ = client
    upload = http.post("/attachments", files={"file": ("notes.md", b"Notes", "text/markdown")})
    ref = upload.json() | {"type": "pdf"}
    response = http.post(
        "/tasks", json={"request": "Read", "idempotency_key": "composer-request-123", "attachments": [ref]}
    )
    assert response.status_code == 422
    assert received == []


def test_pdf_upload_supplies_text_and_rejects_scans_and_encryption(client):
    http, received, _ = client
    pdf = b"%PDF-1.7\nstream\nBT (Synthetic PDF report) Tj ET\nendstream\n%%EOF"
    upload = http.post("/attachments", files={"file": ("report.pdf", pdf, "application/pdf")})
    assert upload.status_code == 200
    assert upload.json()["type"] == "pdf"
    response = http.post(
        "/tasks", json={"request": "", "idempotency_key": "composer-request-123", "attachments": [upload.json()]}
    )
    assert response.status_code == 202
    assert "Synthetic PDF report" in received[0].request
    for body in (b"%PDF-1.7 /Encrypt 5 0 R", b"%PDF-1.7 no text layer"):
        assert http.post("/attachments", files={"file": ("scan.pdf", body, "application/pdf")}).status_code == 415


def test_attachment_retention_and_store_quota(client, monkeypatch):
    import os
    import time

    http, received, directory = client
    upload = http.post("/attachments", files={"file": ("a.txt", b"Notes", "text/plain")})
    for path in directory.iterdir():
        os.utime(path, (time.time() - 90_000, time.time() - 90_000))
    response = http.post(
        "/tasks", json={"request": "Read", "idempotency_key": "composer-request-123", "attachments": [upload.json()]}
    )
    assert response.status_code == 404
    assert received == []
    monkeypatch.setattr(routes_attachments, "MAX_STORED_ATTACHMENTS", 1)
    assert http.post("/attachments", files={"file": ("b.txt", b"Notes", "text/plain")}).status_code == 200
    assert http.post("/attachments", files={"file": ("c.txt", b"Notes", "text/plain")}).status_code == 413


def test_store_total_byte_quota(client, monkeypatch):
    http, _, _ = client
    monkeypatch.setattr(routes_attachments, "MAX_STORE_BYTES", 10)
    assert http.post("/attachments", files={"file": ("a.txt", b"Large notes body", "text/plain")}).status_code == 413


def test_task_attachment_count_and_combined_content_are_bounded(client):
    http, received, _ = client
    upload = http.post("/attachments", files={"file": ("a.txt", b"Notes", "text/plain")})
    refs = [upload.json()] * 9
    assert (
        http.post(
            "/tasks", json={"request": "Read", "idempotency_key": "composer-request-123", "attachments": refs}
        ).status_code
        == 422
    )
    big = http.post("/attachments", files={"file": ("b.txt", b"a" * 21_000, "text/plain")})
    assert big.status_code == 200
    assert (
        http.post(
            "/tasks", json={"request": "", "idempotency_key": "composer-request-123", "attachments": [big.json()]}
        ).status_code
        == 413
    )
    assert received == []
