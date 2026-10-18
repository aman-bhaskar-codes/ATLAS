from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atlas.interfaces.api.app import create_app


def test_env_file_api_keys_are_enforced_before_protected_data_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ATLAS_API_KEYS", raising=False)
    (tmp_path / ".env").write_text("ATLAS_API_KEYS=synthetic-test-key\n")
    app = create_app()
    response = TestClient(app).get("/api/v1/tasks")
    assert response.status_code == 401
