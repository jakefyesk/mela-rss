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
    assert "https://www.example.com/sitemap-recipes-1.xml" in nested


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
