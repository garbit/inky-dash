import pytest
from starlette.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("INKY_DASH_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("INKY_DASH_OUTPUT", str(tmp_path / "display.png"))
    monkeypatch.setenv("INKY_DASH_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("INKY_DASH_DISPLAY", "file")
    from inky_dash.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
