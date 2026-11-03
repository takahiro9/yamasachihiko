from world_events.collect import gdelt
from world_events.collect.rss import parse_feed
from world_events.collect.sitemap import parse_sitemap, sitemap_index_children
from world_events.llm.verify import ground_l1, numbers_in, ungrounded_numbers
from world_events.llm.schemas import Entity, L1Out, NumberFact
from world_events.normalize.hashing import content_hash, hamming, simhash
from world_events.normalize.lang import detect_language
from world_events.normalize.url import normalize_url

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>地震が発生</title><link>https://ex.com/a?utm_source=x</link><pubDate>Sat, 24 Oct 2026 01:00:00 GMT</pubDate>
<description>&lt;p&gt;震度5&lt;/p&gt;</description></item>
<item><title></title><link>https://ex.com/b</link></item></channel></rss>"""


def test_normalize_url_strips_tracking_and_trailing_slash():
    assert normalize_url("HTTPS://Ex.com/a/?utm_source=x&id=1#frag") == normalize_url("https://ex.com/a?id=1")


def test_hash_and_simhash():
    assert content_hash("A  b", "c") == content_hash("a b", "C")
    a = simhash("The quick brown fox jumps over the lazy dog near the river bank today")
    b = simhash("The quick brown fox jumps over the lazy dog near the river bank today!")
    c = simhash("完全に別の話題について書かれた全く違う文章です。株価が下落しました")
    assert hamming(a, b) < hamming(a, c)


def test_detect_language():
    assert detect_language("こんにちは", "en") == "ja"
    assert detect_language("Привет", "en") == "ru"
    assert detect_language("Hello", "es") == "es"


def test_parse_feed_skips_items_without_title():
    items = parse_feed(RSS)
    assert len(items) == 1 and items[0].title == "地震が発生" and items[0].excerpt == "震度5"
    assert items[0].published_at == "2026-10-24T01:00:00Z"


def test_parse_sitemap_news_and_since():
    xml = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
    <url><loc>https://e.com/1</loc><news:news><news:publication_date>2026-10-24T01:00:00Z</news:publication_date><news:title>New</news:title></news:news></url>
    <url><loc>https://e.com/2</loc><lastmod>2026-10-01</lastmod></url></urlset>"""
    all_ = parse_sitemap(xml)
    assert [r.title for r in all_][0] == "New" and len(all_) == 2
    assert [r.url for r in parse_sitemap(xml, since="2026-10-10T00:00:00Z")] == ["https://e.com/1"]
    idx = '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://e.com/s1.xml</loc></sitemap></sitemapindex>'
    assert sitemap_index_children(idx) == ["https://e.com/s1.xml"]


def test_gdelt_parse():
    out = gdelt.parse_artlist({"articles": [{"url": "https://a/1", "title": "T", "seendate": "20261024T010000Z",
                                              "domain": "a.jp", "language": "Japanese", "sourcecountry": "Japan"}]})
    assert (out[0].language, out[0].country, out[0].media, out[0].published_at) == ("ja", "JP", "a.jp", "2026-10-24T01:00:00Z")


def test_number_grounding():
    assert numbers_in("死者 1,200 人と 3.5%") == {"1200", "3.5"}
    assert ungrounded_numbers("死者は1200人、負傷者は50人", "死者 1,200 人") == {"50"}


def test_ground_l1_drops_unsupported():
    out = L1Out(title_ja="t", points_ja=[], entities=[Entity(name="Tokyo", type="place"), Entity(name="Mars", type="place")],
                numbers=[NumberFact(value="12"), NumberFact(value="99")])
    g, dropped = ground_l1(out, "12 people in Tokyo")
    assert [e.name for e in g.entities] == ["Tokyo"] and [n.value for n in g.numbers] == ["12"] and len(dropped) == 2
