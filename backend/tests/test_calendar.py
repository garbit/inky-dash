import asyncio
from datetime import UTC, date

from inky_dash.config import AppConfig
from inky_dash.modules.calendar import CalendarModule, bucket_events, merge_events


def test_unconfigured_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setenv("INKY_DASH_DATA", str(tmp_path))
    data = asyncio.run(CalendarModule().fetch(AppConfig()))
    assert data == {"configured": False}


def test_bucket_events_splits_today_and_upcoming():
    today = date(2026, 7, 25)
    items = [
        {
            "start": {"dateTime": "2026-07-25T09:30:00+00:00"},
            "summary": "Standup",
            "_cal_name": "Work",
        },
        {"start": {"date": "2026-07-25"}, "summary": "Holiday", "_cal_name": "Family"},
        {
            "start": {"dateTime": "2026-07-27T14:00:00+00:00"},
            "summary": "Dentist",
            "_cal_name": "Personal",
        },
        {"start": {"date": "2026-07-30"}, "summary": "Trip", "_cal_name": "Family"},
    ]
    events, upcoming = bucket_events(items, today, UTC, max_events=6)
    assert events == [
        {"when": "09:30", "summary": "Standup", "calendar": "Work"},
        {"when": "all day", "summary": "Holiday", "calendar": "Family"},
    ]
    assert upcoming == [
        {"when": "14:00", "summary": "Dentist", "calendar": "Personal", "day": "Mon 27"},
        {"when": "all day", "summary": "Trip", "calendar": "Family", "day": "Thu 30"},
    ]


def test_bucket_events_multiday_underway_counts_as_today():
    today = date(2026, 7, 25)
    items = [{"start": {"date": "2026-07-24"}, "summary": "Conference"}]
    events, upcoming = bucket_events(items, today, UTC, max_events=6)
    assert events == [{"when": "all day", "summary": "Conference", "calendar": ""}]
    assert upcoming == []


def test_merge_events_sorts_across_calendars():
    work = [{"start": {"dateTime": "2026-07-26T15:00:00+00:00"}, "summary": "Late", "iCalUID": "a"}]
    shared = [
        {"start": {"dateTime": "2026-07-26T09:00:00+00:00"}, "summary": "Early", "iCalUID": "b"},
        {"start": {"date": "2026-07-26"}, "summary": "All day", "iCalUID": "c"},
    ]
    merged = merge_events([work, shared], UTC)
    assert [e["summary"] for e in merged] == ["All day", "Early", "Late"]


def test_merge_events_dedupes_by_ical_uid():
    invite = {"start": {"dateTime": "2026-07-26T09:00:00+00:00"}, "summary": "Sync", "iCalUID": "x"}
    copy = {"start": {"dateTime": "2026-07-26T09:00:00+00:00"}, "summary": "Sync", "iCalUID": "x"}
    merged = merge_events([[invite], [copy]], UTC)
    assert len(merged) == 1


def test_bucket_events_caps_each_bucket():
    today = date(2026, 7, 25)
    items = [
        {"start": {"dateTime": f"2026-07-26T{hour:02d}:00:00+00:00"}, "summary": f"e{hour}"}
        for hour in range(10)
    ]
    _, upcoming = bucket_events(items, today, UTC, max_events=3)
    assert len(upcoming) == 3
