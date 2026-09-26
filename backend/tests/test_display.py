from PIL import Image

from inky_dash.config import DisplayConfig
from inky_dash.display import create_display
from inky_dash.display.file_display import FileDisplay


def test_file_display_writes_png(tmp_path):
    path = tmp_path / "out" / "display.png"
    display = FileDisplay((800, 480), path=path)
    display.show(Image.new("RGB", (800, 480), (255, 255, 255)))
    saved = Image.open(path)
    assert saved.size == (800, 480)
    assert saved.format == "PNG"


def test_create_display_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("INKY_DASH_DISPLAY", "file")
    monkeypatch.setenv("INKY_DASH_OUTPUT", str(tmp_path / "display.png"))
    display = create_display(DisplayConfig(kind="auto"))
    assert isinstance(display, FileDisplay)
    assert display.resolution == (800, 480)
