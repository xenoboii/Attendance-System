"""Static files are cached by the browser (no icon flicker) but versioned so edits still load."""
import re


def test_static_links_are_versioned_and_cached(client):
    page = client.get("/login").data.decode()
    css = re.search(r'href="(/static/css/style\.css\?v=\d+)"', page)
    assert css, "style.css link should carry ?v=<mtime>"
    r = client.get(css.group(1))
    assert r.status_code == 200
    assert "max-age=31536000" in r.headers.get("Cache-Control", "")


def test_font_preloads_match_css_urls(client):
    page = client.get("/login").data.decode()
    # icon font preload must be the exact URL the icon CSS requests
    assert '/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2?dd67030699838ea613ee6dbda90effa6"' in page
    # text font preloads have no version tag (style.css asks for them without one)
    assert '/static/vendor/fonts/inter-latin-400-normal.woff2"' in page
