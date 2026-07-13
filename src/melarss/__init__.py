"""mela-rss — generate recipe RSS feeds that import cleanly into the Mela app.

Mela subscribes to an RSS feed and, for each item, follows ``<link>``, fetches
that page, and extracts a recipe from its schema.org/Recipe JSON-LD (with an ML
fallback). Everything here exists to produce feeds + pages that satisfy that
"fetch-and-extract the linked page" contract. See the plan in the repo for the
full design rationale.
"""

__version__ = "0.1.0"
