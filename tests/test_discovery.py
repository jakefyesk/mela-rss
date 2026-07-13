from melarss.discovery import native_feed, sitemap

from conftest import load_fixture


class RoutingHttp:
    def __init__(self, routes):
        self.routes = routes

    def get(self, url):
        return self.routes[url]


def test_parse_sitemap_index_and_urlset():
    entries, nested = sitemap.parse_sitemap(load_fixture("sitemap_index.xml"))
    assert entries == []
    nested_locs = [loc for loc, _ in nested]
    assert "https://www.example.com/sitemap-recipes-1.xml" in nested_locs
    # lastmod carried through for recency ordering
    assert ("https://www.example.com/sitemap-recipes-1.xml", "2026-06-01") in nested


def test_sitemap_urls_recurses_and_filters_and_orders():
    http = RoutingHttp(
        {
            "https://www.example.com/sitemap.xml": load_fixture("sitemap_index.xml"),
            "https://www.example.com/sitemap-recipes-1.xml": load_fixture("sitemap-recipes-1.xml"),
            "https://www.example.com/sitemap-pages.xml": "<urlset></urlset>",
        }
    )
    urls = sitemap.sitemap_urls(
        "https://www.example.com/sitemap.xml", r"/recipes/", http
    )
    # /about filtered out; newest lastmod first
    assert urls == [
        "https://www.example.com/recipes/newest-dish",
        "https://www.example.com/recipes/older-dish",
    ]


def test_native_feed_rss_links():
    links = native_feed.parse_feed_links(load_fixture("rss_feed.xml"))
    assert links == [
        "https://www.example.com/recipes/newest-dish",
        "https://www.example.com/news/we-moved",
    ]


def test_native_feed_atom_uses_alternate_link():
    links = native_feed.parse_feed_links(load_fixture("atom_feed.xml"))
    assert links == ["https://www.example.com/recipes/atom-dish"]


def test_sitemap_with_xml_comments_does_not_crash():
    xml = (
        '<?xml version="1.0"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<!-- a comment node that lxml yields -->"
        "<url><!-- inner comment --><loc>https://x.com/recipes/a</loc></url>"
        "</urlset>"
    )
    entries, nested = sitemap.parse_sitemap(xml)
    assert entries == [("https://x.com/recipes/a", None)]
    assert nested == []


def test_native_feed_with_xml_comments_does_not_crash():
    xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        "<!-- comment --><item><!-- c --><link>https://x.com/recipes/a</link></item>"
        "</channel></rss>"
    )
    assert native_feed.parse_feed_links(xml) == ["https://x.com/recipes/a"]
