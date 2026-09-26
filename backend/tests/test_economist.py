from inky_dash.modules.economist import interleave, parse_feed

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Leaders</title>
  <item><title>First headline</title><link>https://example.com/1</link></item>
  <item><title>Second headline</title><link>https://example.com/2</link></item>
  <item><link>https://example.com/no-title</link></item>
</channel></rss>
"""


def test_parse_feed():
    items = parse_feed(RSS, "leaders")
    assert [i["title"] for i in items] == ["First headline", "Second headline"]
    assert all(i["section"] == "leaders" for i in items)


def test_interleave_round_robin():
    a = [{"title": f"a{i}"} for i in range(3)]
    b = [{"title": f"b{i}"} for i in range(1)]
    assert [i["title"] for i in interleave([a, b], 4)] == ["a0", "b0", "a1", "a2"]


def test_interleave_respects_limit():
    a = [{"title": f"a{i}"} for i in range(10)]
    assert len(interleave([a], 5)) == 5
