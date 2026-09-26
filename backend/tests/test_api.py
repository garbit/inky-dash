import io


def test_get_config(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json()["refresh_seconds"] == 600


def test_put_config_roundtrip(client):
    config = client.get("/api/config").json()
    config["refresh_seconds"] = 1200
    config["layout"]["active"] = "mine"
    config["layout"]["layouts"] = [
        {
            "id": "mine",
            "name": "Mine",
            "regions": [{"x": 0, "y": 0, "w": 50, "h": 30, "module": "clock"}],
        }
    ]
    response = client.put("/api/config", json=config)
    assert response.status_code == 200
    saved = client.get("/api/config").json()
    assert saved["refresh_seconds"] == 1200
    assert saved["layout"]["active"] == "mine"


def test_put_config_rejects_invalid(client):
    config = client.get("/api/config").json()
    config["refresh_seconds"] = 1  # below the 60s floor
    assert client.put("/api/config", json=config).status_code == 422


def test_put_config_rejects_overlapping_regions(client):
    config = client.get("/api/config").json()
    config["layout"]["layouts"] = [
        {
            "id": "bad",
            "name": "Bad",
            "regions": [
                {"x": 0, "y": 0, "w": 10, "h": 10, "module": "clock"},
                {"x": 5, "y": 5, "w": 10, "h": 10, "module": "weather"},
            ],
        }
    ]
    assert client.put("/api/config", json=config).status_code == 422


def test_put_config_rejects_unknown_module(client):
    config = client.get("/api/config").json()
    config["layout"]["active"] = "bad"
    config["layout"]["layouts"] = [
        {
            "id": "bad",
            "name": "Bad",
            "regions": [{"x": 0, "y": 0, "w": 10, "h": 10, "module": "nope"}],
        }
    ]
    assert client.put("/api/config", json=config).status_code == 422


def test_meta(client):
    response = client.get("/api/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["resolution"] == [800, 480]
    assert {m["id"] for m in body["modules"]} == {
        "clock",
        "weather",
        "govee",
        "calendar",
        "economist",
        "xkcd",
        "vox",
        "moon",
        "photos",
    }
    # every module ships the schema the config UI renders its options panel from
    for module in body["modules"]:
        assert module["name"]
        assert module["config_key"]
        assert isinstance(module["options"], list)
    weather = next(m for m in body["modules"] if m["id"] == "weather")
    forecast = next(item for item in weather["options"] if item.get("key") == "forecast_days")
    # numeric limits come from the Pydantic model, not the schema declaration
    assert (forecast["min"], forecast["max"]) == (1, 7)
    preset_ids = [p["id"] for p in body["presets"]]
    assert "dashboard_clock" in preset_ids
    assert body["limits"] == {
        "min_region": 2,
        "max_regions": 12,
        "default_cols": 50,
        "default_rows": 30,
        "max_playlist_items": 12,
    }


def test_preview_png(client):
    response = client.get("/api/preview.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_candidate_preview_png(client):
    config = client.get("/api/config").json()
    config["layout"]["active"] = "full"
    response = client.post("/api/preview.png", json=config)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
    # previewing must not save the candidate
    assert client.get("/api/config").json()["layout"]["active"] == "dashboard_clock"


def test_candidate_preview_rejects_dangling_active(client):
    config = client.get("/api/config").json()
    config["layout"]["active"] = "nonexistent"
    assert client.post("/api/preview.png", json=config).status_code == 422


def test_candidate_preview_renders_the_requested_layout(client):
    """The UI previews the layout being edited (or a playlist screen), which is
    not necessarily the active one."""
    config = client.get("/api/config").json()
    response = client.post("/api/preview.png?layout=full", json=config)
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")
    # a different layout must produce a different frame, not silently the active one
    other = client.post("/api/preview.png?layout=split", json=config)
    assert other.content != response.content


def test_candidate_preview_rejects_unknown_layout(client):
    config = client.get("/api/config").json()
    response = client.post("/api/preview.png?layout=nonexistent", json=config)
    assert response.status_code == 422


def test_playlist_roundtrip_and_status(client):
    config = client.get("/api/config").json()
    config["playlist"] = {
        "enabled": True,
        "items": [
            {"layout": "full", "minutes": 5, "enabled": True},
            {"layout": "grid", "minutes": 10, "enabled": True},
        ],
    }
    assert client.put("/api/config", json=config).status_code == 200

    status = client.get("/api/status").json()
    assert status["layout"] == "full"
    assert status["playlist"]["position"] == 1
    assert status["playlist"]["count"] == 2
    assert status["playlist"]["name"] == "Full screen"

    body = client.post("/api/playlist/next").json()
    assert body["playlist"]["position"] == 2
    assert client.get("/api/status").json()["layout"] == "grid"


def test_put_config_rejects_dangling_playlist_layout(client):
    config = client.get("/api/config").json()
    config["playlist"] = {"enabled": True, "items": [{"layout": "nope", "minutes": 5}]}
    response = client.put("/api/config", json=config)
    assert response.status_code == 422
    assert "playlist screen 1" in response.json()["detail"]


def test_playlist_next_without_playlist_is_a_plain_refresh(client):
    body = client.post("/api/playlist/next").json()
    assert body == {"ok": True, "playlist": None}


def test_refresh_wakes_render_loop(client):
    import time

    def last_render():
        return client.get("/api/status").json()["last_render"]

    for _ in range(50):  # wait for the startup render
        if last_render() is not None:
            break
        time.sleep(0.1)
    initial = last_render()
    assert initial is not None
    client.post("/api/refresh")
    for _ in range(50):
        time.sleep(0.1)
        if last_render() != initial:
            break
    assert last_render() != initial


def test_status(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["display"] == "FileDisplay"
    assert body["resolution"] == [800, 480]


def photo_jpeg(size=(120, 80)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, (200, 40, 40)).save(buf, format="JPEG")
    return buf.getvalue()


def test_photo_pool_round_trip(client):
    assert client.get("/api/photos").json() == {
        "photos": [],
        "bytes_used": 0,
        "max_photos": 200,
        "frame": [800, 480],
    }

    upload = client.post(
        "/api/photos/upload",
        files=[("files", ("one.jpg", photo_jpeg(), "image/jpeg"))],
    )
    assert upload.json() == {"added": 1, "duplicates": 0, "pruned": 0, "errors": []}

    # the same file again is recognised rather than stored twice
    again = client.post(
        "/api/photos/upload", files=[("files", ("one.jpg", photo_jpeg(), "image/jpeg"))]
    )
    assert again.json()["duplicates"] == 1

    pool = client.get("/api/photos").json()
    assert len(pool["photos"]) == 1 and pool["bytes_used"] > 0
    photo_id = pool["photos"][0]["id"]

    thumb = client.get(f"/api/photos/thumb/{photo_id}")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"

    assert client.delete(f"/api/photos/{photo_id}").status_code == 200
    assert client.get("/api/photos").json()["photos"] == []
    assert client.delete(f"/api/photos/{photo_id}").status_code == 404


def test_upload_reports_a_bad_file_without_failing(client):
    response = client.post(
        "/api/photos/upload",
        files=[("files", ("notes.txt", b"not an image", "text/plain"))],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["added"] == 0 and body["errors"]


def test_clear_removes_every_photo(client):
    client.post("/api/photos/upload", files=[("files", ("a.jpg", photo_jpeg(), "image/jpeg"))])
    assert client.delete("/api/photos").json() == {"removed": 1}
    assert client.get("/api/photos").json()["photos"] == []


def test_thumb_rejects_an_unknown_photo(client):
    assert client.get("/api/photos/thumb/deadbeef").status_code == 404
    assert client.get("/api/photos/thumb/not-hex").status_code == 404


def test_photo_frame_follows_the_layout_hosting_the_panel(client):
    config = client.get("/api/config").json()
    config["layout"]["active"] = "photo_clock"  # photos over 26 of 30 rows
    assert client.put("/api/config", json=config).status_code == 200
    assert client.get("/api/photos").json()["frame"] == [800, 416]


def test_photo_focus_round_trip(client):
    client.post("/api/photos/upload", files=[("files", ("a.jpg", photo_jpeg(), "image/jpeg"))])
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]

    # unpositioned photos carry no focus at all: absent means centred
    assert "focus" not in client.get("/api/photos").json()["photos"][0]

    saved = client.put(f"/api/photos/{photo_id}/focus", json={"focus": [0.25, 0.75]})
    assert saved.status_code == 200 and saved.json()["focus"] == [0.25, 0.75]
    assert client.get("/api/photos").json()["photos"][0]["focus"] == [0.25, 0.75]

    # out of range is clamped rather than refused — a stray drag can't wedge it
    clamped = client.put(f"/api/photos/{photo_id}/focus", json={"focus": [-2, 9]})
    assert clamped.json()["focus"] == [0.0, 1.0]

    # null restores the default
    assert client.put(f"/api/photos/{photo_id}/focus", json={"focus": None}).status_code == 200
    assert "focus" not in client.get("/api/photos").json()["photos"][0]

    assert client.put("/api/photos/deadbeef/focus", json={"focus": None}).status_code == 404


def test_photo_preview_renders_at_the_panel_size(client):
    from io import BytesIO

    from PIL import Image

    client.post("/api/photos/upload", files=[("files", ("a.jpg", photo_jpeg(), "image/jpeg"))])
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]

    response = client.get(f"/api/photos/preview/{photo_id}.png?fx=0&fy=1")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert Image.open(BytesIO(response.content)).size == (800, 480)

    simulated = client.get(f"/api/photos/preview/{photo_id}.png?simulate=1")
    assert simulated.status_code == 200
    assert client.get("/api/photos/preview/deadbeef.png").status_code == 404


def test_photo_full_serves_the_stored_image(client):
    client.post("/api/photos/upload", files=[("files", ("a.jpg", photo_jpeg(), "image/jpeg"))])
    photo_id = client.get("/api/photos").json()["photos"][0]["id"]
    full = client.get(f"/api/photos/full/{photo_id}")
    assert full.status_code == 200 and full.headers["content-type"] == "image/jpeg"
    assert client.get("/api/photos/full/deadbeef").status_code == 404


def test_google_status_when_nothing_is_connected(client):
    body = client.get("/api/google/status").json()
    assert body["credentials"] is False
    assert body["services"]["photos"] == {"connected": False}
    assert body["services"]["calendar"] == {"connected": False}


def test_google_connect_without_a_client_file_returns_a_hint(client):
    body = client.post("/api/google/connect", json={"service": "photos"}).json()
    assert "google_credentials.json" in body["error"]


def test_google_connect_rejects_an_unknown_service(client):
    assert client.post("/api/google/connect", json={"service": "gmail"}).status_code == 422
    assert client.delete("/api/google/gmail").status_code == 422


def test_google_complete_with_a_stale_state_is_reported_not_raised(client):
    body = client.post("/api/google/complete", json={"state": "gone", "response": "4/x"}).json()
    assert body["connected"] is False and body["error"]


def test_picker_session_without_a_connection_returns_a_hint(client):
    assert "not connected" in client.post("/api/photos/picker/session").json()["error"]


def test_simulated_preview_uses_only_panel_colours(client):
    from PIL import Image

    from inky_dash import palette

    response = client.get("/api/preview.png?simulate=1")
    assert response.status_code == 200
    frame = Image.open(io.BytesIO(response.content)).convert("RGB")
    assert set(frame.get_flattened_data()) <= set(palette.COLOURS)


def test_picking_imports_without_any_further_client_calls(client, monkeypatch):
    """The reason picking moved to the backend: after the one call that starts a
    session, the client does nothing at all and the photos still arrive."""
    import time

    from inky_dash import google_photos, photos_sync

    monkeypatch.setattr(photos_sync, "MIN_POLL", 0.01)
    monkeypatch.setattr(
        google_photos,
        "create_session",
        lambda: {
            "session_id": "sess-1",
            "picker_uri": "https://photos.google.com/pick/sess-1",
            "poll_seconds": 0.01,
            "timeout_seconds": 5,
            "ready": False,
        },
    )
    monkeypatch.setattr(google_photos, "get_session", lambda sid: {"ready": True})
    imported = {}
    monkeypatch.setattr(
        google_photos,
        "import_session",
        lambda sid, cap: (
            imported.setdefault("cap", cap)
            and None
            or {"imported": 2, "skipped": 0, "duplicates": 0, "total": 2}
        ),
    )

    started = client.post("/api/photos/picker/session").json()
    assert started["picker_uri"].endswith("sess-1")

    for _ in range(50):
        time.sleep(0.05)
        jobs = client.get("/api/photos/jobs").json()
        if jobs["picker"]["state"] == "done":
            break
    assert jobs["picker"] == {
        "state": "done",
        "at": jobs["picker"]["at"],
        "imported": 2,
        "skipped": 0,
        "duplicates": 0,
        "total": 2,
    }
    # the import used the configured pool cap, not a hard-coded one
    assert imported["cap"] == 200


def test_jobs_endpoint_is_safe_before_anything_has_run(client):
    assert client.get("/api/photos/jobs").json() == {
        "picker": {"state": "idle"},
        "drive": {"state": "idle"},
    }


def test_a_foreign_signin_is_flagged_for_handoff(client):
    """A code from another instance's sign-in must come back flagged, so the UI
    can offer the address for pasting rather than reporting a dead end."""
    body = client.post(
        "/api/google/complete",
        json={"state": "elsewhere", "response": "http://localhost:8000/?state=elsewhere&code=4/x"},
    ).json()
    assert body["connected"] is False
    assert body["unknown_state"] is True
    assert "not started here" in body["error"]


def test_a_bad_paste_is_not_flagged_for_handoff(client):
    """Only an unknown flow gets the handoff treatment; a malformed paste is just
    an error, or the UI would send people chasing the wrong fix."""
    body = client.post("/api/google/complete", json={"state": "", "response": "   "}).json()
    assert body["connected"] is False
    assert "unknown_state" not in body
    assert "Nothing pasted" in body["error"]
