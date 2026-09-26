"""Picker and Drive clients against a fake authorised session.

These cover the code that talks to Google, which no test could otherwise reach
without real credentials: duration parsing, pagination, the video/failure skips,
dedupe, pruning and session cleanup.
"""

from io import BytesIO

import pytest
from PIL import Image

from inky_dash import google, google_drive, google_photos, photos_store


@pytest.fixture(autouse=True)
def connected(tmp_path, monkeypatch):
    """A pool on disk and a token file, so the "not connected" guard passes."""
    monkeypatch.setenv("INKY_DASH_DATA", str(tmp_path))
    google.token_path(google.PHOTOS).write_text("{}")
    google.token_path(google.DRIVE).write_text("{}")
    return tmp_path


def jpeg(colour=(10, 120, 200)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (60, 40), colour).save(buf, format="JPEG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, payload=None, content=b"", status=200):
        self._payload = payload or {}
        self.content = content
        self.status_code = status
        self.ok = status < 400
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    """Stands in for google.auth's AuthorizedSession."""

    def __init__(self, *, gets=None, posts=None):
        self.gets = gets or {}
        self.posts = posts or {}
        self.deleted: list[str] = []
        self.downloads: list[str] = []

    def post(self, url, **kwargs):
        return self.posts.get(url, FakeResponse({}))

    def get(self, url, params=None, **kwargs):
        if "=w" in url:  # a media download
            self.downloads.append(url)
            handler = self.gets.get("download")
            if callable(handler):
                return handler(url)
            # Vary the bytes per item: two identical photos would (correctly) be
            # deduped, which is not what most of these tests are exercising.
            return FakeResponse(content=jpeg((len(self.downloads) * 60 % 255, 120, 200)))
        handler = self.gets.get(url)
        if callable(handler):
            return handler(params or {})
        return handler or FakeResponse({})

    def delete(self, url, **kwargs):
        self.deleted.append(url)
        return FakeResponse({})


def use(monkeypatch, session):
    monkeypatch.setattr(google_photos, "_session", lambda: session)
    return session


# ------------------------------------------------------------------ sessions


def test_create_session_parses_durations(monkeypatch):
    use(
        monkeypatch,
        FakeSession(
            posts={
                f"{google_photos.BASE}/sessions": FakeResponse(
                    {
                        "id": "sess-1",
                        "pickerUri": "https://photos.google.com/pick/sess-1",
                        "pollingConfig": {"pollInterval": "7s", "timeoutIn": "600s"},
                    }
                )
            }
        ),
    )
    session = google_photos.create_session()
    assert session["session_id"] == "sess-1"
    assert session["poll_seconds"] == 7.0
    assert session["timeout_seconds"] == 600.0
    assert session["ready"] is False


def test_duration_parsing_falls_back_on_junk():
    assert google_photos._seconds("3.5s", 5) == 3.5
    assert google_photos._seconds(12, 5) == 12.0
    assert google_photos._seconds("banana", 5) == 5.0
    assert google_photos._seconds(None, 5) == 5.0


def test_api_errors_become_picker_errors(monkeypatch):
    use(
        monkeypatch,
        FakeSession(
            posts={f"{google_photos.BASE}/sessions": FakeResponse({"error": "nope"}, status=403)}
        ),
    )
    with pytest.raises(google_photos.PickerError, match="403"):
        google_photos.create_session()


def test_not_connected_is_reported_not_raised_as_a_crash(monkeypatch, connected):
    google.token_path(google.PHOTOS).unlink()
    with pytest.raises(google_photos.PickerError, match="not connected"):
        google_photos.create_session()


def test_list_media_items_follows_pagination(monkeypatch):
    pages = {
        None: {"mediaItems": [{"id": "a"}], "nextPageToken": "p2"},
        "p2": {"mediaItems": [{"id": "b"}]},
    }
    use(
        monkeypatch,
        FakeSession(
            gets={
                f"{google_photos.BASE}/mediaItems": lambda params: FakeResponse(
                    pages[params.get("pageToken")]
                )
            }
        ),
    )
    assert [i["id"] for i in google_photos.list_media_items("sess-1")] == ["a", "b"]


# -------------------------------------------------------------------- import


def picked(item_id, mime="image/jpeg", filename="p.jpg"):
    return {
        "id": item_id,
        "createTime": "2024-05-04T09:00:00Z",
        "mediaFile": {
            "baseUrl": f"https://lh3.example/{item_id}",
            "mimeType": mime,
            "filename": filename,
        },
    }


def import_with(monkeypatch, items, **kwargs):
    session = use(
        monkeypatch,
        FakeSession(
            gets={
                f"{google_photos.BASE}/mediaItems": lambda params: FakeResponse(
                    {"mediaItems": items}
                ),
                **kwargs,
            },
        ),
    )
    return session, google_photos.import_session("sess-1", 100)


def test_import_stores_pictures_and_tidies_up(monkeypatch):
    session, result = import_with(monkeypatch, [picked("a"), picked("b")])
    assert result["imported"] == 2 and result["skipped"] == 0
    assert len(photos_store.load_index()) == 2
    # the download must ask for an explicit size — Google rejects a bare baseUrl
    assert all("=w" in url for url in session.downloads)
    assert session.deleted == [f"{google_photos.BASE}/sessions/sess-1"]


def test_import_skips_videos(monkeypatch):
    _, result = import_with(monkeypatch, [picked("v", mime="video/mp4"), picked("a")])
    assert (result["imported"], result["skipped"]) == (1, 1)


def test_import_carries_on_past_a_failed_download(monkeypatch):
    def download(url):
        return (
            FakeResponse(status=500)
            if url.endswith("=w1600-h960") and "bad" in url
            else FakeResponse(content=jpeg())
        )

    _, result = import_with(monkeypatch, [picked("bad"), picked("good")], download=download)
    assert result["imported"] == 1 and result["skipped"] == 1 and result["errors"]
    assert len(photos_store.load_index()) == 1


def test_import_records_the_capture_date_for_on_this_day(monkeypatch):
    import_with(monkeypatch, [picked("a")])
    assert photos_store.load_index()[0]["taken"] == "2024-05-04T09:00:00Z"


def test_reimporting_the_same_selection_adds_nothing(monkeypatch):
    import_with(monkeypatch, [picked("a")])
    _, result = import_with(monkeypatch, [picked("a")])
    assert result["imported"] == 0 and result["duplicates"] == 1
    assert len(photos_store.load_index()) == 1


def test_import_prunes_to_the_configured_cap(monkeypatch):
    def download(url):
        # widely-spaced colours per item, or dedupe would collapse them
        index = int(url.split("/i")[1][0])
        return FakeResponse(content=jpeg((index * 60, 50, 50)))

    session = use(
        monkeypatch,
        FakeSession(
            gets={
                f"{google_photos.BASE}/mediaItems": lambda p: FakeResponse(
                    {"mediaItems": [picked(f"i{n}") for n in range(4)]}
                ),
                "download": download,
            }
        ),
    )
    assert session is not None
    result = google_photos.import_session("sess-1", 2)
    assert result["pruned"] == 2
    assert len(photos_store.load_index()) == 2


# --------------------------------------------------------------------- drive


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://drive.google.com/drive/folders/1AbC-dEf_9?usp=sharing", "1AbC-dEf_9"),
        ("https://drive.google.com/drive/u/0/folders/1AbC-dEf_9", "1AbC-dEf_9"),
        ("https://drive.google.com/open?id=1AbC-dEf_9", "1AbC-dEf_9"),
        ("1AbC-dEf_9", "1AbC-dEf_9"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_folder_id_is_recovered_from_whatever_gets_pasted(value, expected):
    assert google_drive.parse_folder_id(value) == expected


class FakeFiles:
    def __init__(self, pages, media=None):
        self.pages = pages
        self.media = media or (lambda file_id: jpeg())
        self.queries: list[str] = []

    def list(self, **kwargs):
        self.queries.append(kwargs["q"])
        page = self.pages[kwargs.get("pageToken")]
        return type("Req", (), {"execute": lambda _s: page})()

    def get_media(self, fileId, **kwargs):
        data = self.media(fileId)
        return type("Req", (), {"execute": lambda _s: data})()


def fake_drive(monkeypatch, pages, media=None):
    files = FakeFiles(pages, media)
    service = type("Service", (), {"files": lambda _s: files})()
    monkeypatch.setattr(google_drive, "_service", lambda: service)
    return files


def test_drive_sync_imports_new_images_only(monkeypatch):
    files = fake_drive(
        monkeypatch,
        {
            None: {
                "files": [
                    {"id": "f1", "name": "one.jpg", "createdTime": "2024-01-01T00:00:00Z"},
                    {"id": "f2", "name": "two.jpg", "createdTime": "2024-01-02T00:00:00Z"},
                ]
            }
        },
        media=lambda file_id: jpeg((10, 20, 30) if file_id == "f1" else (200, 100, 0)),
    )
    first = google_drive.sync_folder("FOLDER", 100)
    assert (first["added"], first["found"]) == (2, 2)
    # the query must be confined to the folder and to images
    assert "'FOLDER' in parents" in files.queries[0] and "image/" in files.queries[0]

    second = google_drive.sync_folder("FOLDER", 100)
    assert (second["added"], second["skipped"]) == (0, 2)
    assert len(photos_store.load_index()) == 2


def test_drive_sync_paginates(monkeypatch):
    fake_drive(
        monkeypatch,
        {
            None: {"files": [{"id": "f1", "name": "a.jpg"}], "nextPageToken": "p2"},
            "p2": {"files": [{"id": "f2", "name": "b.jpg"}]},
        },
        media=lambda file_id: jpeg((10, 20, 30) if file_id == "f1" else (200, 100, 0)),
    )
    assert google_drive.sync_folder("FOLDER", 100)["found"] == 2


def test_drive_sync_prefers_the_exif_capture_time(monkeypatch):
    fake_drive(
        monkeypatch,
        {
            None: {
                "files": [
                    {
                        "id": "f1",
                        "name": "a.jpg",
                        "createdTime": "2026-01-01T00:00:00Z",
                        "imageMediaMetadata": {"time": "2019-06-15T10:00:00"},
                    }
                ]
            }
        },
    )
    google_drive.sync_folder("FOLDER", 100)
    assert photos_store.load_index()[0]["taken"] == "2019-06-15T10:00:00"


def test_drive_sync_without_a_folder():
    with pytest.raises(google_drive.DriveError, match="No Drive folder"):
        google_drive.sync_folder("", 100)


def test_drive_not_connected(monkeypatch, connected):
    google.token_path(google.DRIVE).unlink()
    with pytest.raises(google_drive.DriveError, match="not connected"):
        google_drive.list_images("FOLDER")


def test_a_missing_folder_is_explained_in_english(monkeypatch):
    class Boom:
        def files(self):
            raise RuntimeError("HTTP 404 notFound")

    monkeypatch.setattr(google_drive, "_service", lambda: Boom())
    with pytest.raises(google_drive.DriveError, match="does not exist"):
        google_drive.list_images("FOLDER")
