from __future__ import annotations

import json
import re
import struct
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
BASE_URL = "https://someone006.github.io/Restore-Rehearsal/"
PAGES = {
    "index.html": BASE_URL,
    "restore-testing/index.html": f"{BASE_URL}restore-testing/",
    "project-brain/index.html": f"{BASE_URL}project-brain/",
    "agent-skills/index.html": f"{BASE_URL}agent-skills/",
}


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.start_tags: list[tuple[str, dict[str, str]]] = []
        self.h1_count = 0
        self.script_types: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        self.start_tags.append((tag, attributes))
        if tag == "h1":
            self.h1_count += 1
        if tag == "script":
            self.script_types.append(attributes.get("type", ""))


def _parse(page: Path) -> tuple[str, _DocumentParser]:
    source = page.read_text(encoding="utf-8")
    parser = _DocumentParser()
    parser.feed(source)
    return source, parser


def _matching(parser: _DocumentParser, tag: str, **attributes: str) -> list[dict[str, str]]:
    return [
        attrs
        for candidate, attrs in parser.start_tags
        if candidate == tag and all(attrs.get(key) == value for key, value in attributes.items())
    ]


def test_expected_static_site_files_exist_and_are_regular() -> None:
    expected = {
        ".nojekyll",
        "index.html",
        "restore-testing/index.html",
        "project-brain/index.html",
        "agent-skills/index.html",
        "assets/site.css",
        "assets/favicon.svg",
        "assets/restore-rehearsal-launch.jpg",
        "assets/restore-rehearsal-launch.mp4",
        "assets/social-card.png",
        "assets/social-card.svg",
        "robots.txt",
        "sitemap.xml",
        "llms.txt",
    }
    assert {
        path.relative_to(SITE).as_posix() for path in SITE.rglob("*") if path.is_file()
    } == expected
    assert not any(path.is_symlink() for path in SITE.rglob("*"))


def test_pages_have_unique_search_and_social_metadata_without_remote_runtime_assets() -> None:
    titles: set[str] = set()
    descriptions: set[str] = set()

    for relative, canonical in PAGES.items():
        page = SITE / relative
        source, parser = _parse(page)

        assert '<html lang="en">' in source
        assert parser.h1_count == 1
        assert "style=" not in source
        assert "google-analytics" not in source.lower()
        assert "googletagmanager" not in source.lower()
        assert "segment.com" not in source.lower()

        title_match = re.search(r"<title>([^<]+)</title>", source)
        assert title_match
        titles.add(title_match.group(1))

        description_tags = _matching(parser, "meta", name="description")
        assert len(description_tags) == 1
        description = description_tags[0]["content"]
        assert 80 <= len(description) <= 180
        descriptions.add(description)

        canonical_tags = _matching(parser, "link", rel="canonical")
        assert canonical_tags == [{"rel": "canonical", "href": canonical}]
        assert _matching(parser, "meta", property="og:url")[0]["content"] == canonical
        assert _matching(parser, "meta", property="og:image:width")[0]["content"] == "1200"
        assert _matching(parser, "meta", property="og:image:height")[0]["content"] == "630"
        assert _matching(parser, "meta", name="twitter:card")[0]["content"] == "summary_large_image"

        for tag, attrs in parser.start_tags:
            asset = ""
            if tag in {"img", "script"}:
                asset = attrs.get("src", "")
            elif tag == "link" and attrs.get("rel") in {"stylesheet", "icon"}:
                asset = attrs.get("href", "")
            if asset:
                assert not asset.startswith(("http://", "https://", "//"))

        assert all(script_type == "application/ld+json" for script_type in parser.script_types)
        structured_data = [
            json.loads(match)
            for match in re.findall(
                r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
                source,
                re.DOTALL,
            )
        ]
        assert len(structured_data) == 1
        assert all(data["@context"] == "https://schema.org" for data in structured_data)
        if relative != "index.html":
            entity_types = {
                entity["@type"]
                for entity in structured_data[0]["@graph"]
            }
            assert {"WebPage", "SoftwareApplication", "BreadcrumbList"} <= entity_types

    assert len(titles) == len(PAGES)
    assert len(descriptions) == len(PAGES)


def test_all_internal_page_and_asset_links_resolve() -> None:
    for relative in PAGES:
        page = SITE / relative
        _, parser = _parse(page)
        for tag, attrs in parser.start_tags:
            if tag not in {"a", "link"}:
                continue
            href = attrs.get("href", "")
            if not href or href.startswith("#") or urlparse(href).scheme:
                continue
            local = href.split("#", 1)[0].split("?", 1)[0]
            target = (page.parent / local).resolve()
            if local.endswith("/") or target.is_dir():
                target /= "index.html"
            assert target.is_relative_to(SITE.resolve())
            assert target.is_file(), f"broken local link in {relative}: {href}"


def test_home_structured_data_is_valid_and_truthful() -> None:
    source = (SITE / "index.html").read_text(encoding="utf-8")
    match = re.search(
        r'<script type="application/ld\+json">\s*(.*?)\s*</script>', source, re.DOTALL
    )
    assert match
    data = json.loads(match.group(1))
    assert data["@context"] == "https://schema.org"
    entities = {item["@type"]: item for item in data["@graph"]}
    assert set(entities) == {
        "SoftwareSourceCode",
        "SoftwareApplication",
        "WebSite",
        "FAQPage",
    }
    assert entities["SoftwareSourceCode"]["codeRepository"] == (
        "https://github.com/Someone006/Restore-Rehearsal"
    )
    assert entities["SoftwareSourceCode"]["license"].endswith("LICENSE-2.0")
    assert entities["SoftwareApplication"]["offers"]["price"] == "0"
    assert entities["SoftwareApplication"]["softwareVersion"] == "0.3.0"


def test_sitemap_robots_and_llms_reference_the_public_pages() -> None:
    root = ET.parse(SITE / "sitemap.xml").getroot()
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locations = {element.text for element in root.findall("s:url/s:loc", namespace)}
    assert locations == set(PAGES.values())
    assert all(
        element.text == "2026-11-11" for element in root.findall("s:url/s:lastmod", namespace)
    )

    robots = (SITE / "robots.txt").read_text(encoding="utf-8")
    assert "User-agent: *" in robots
    assert "Allow: /" in robots
    assert f"Sitemap: {BASE_URL}sitemap.xml" in robots

    llms = (SITE / "llms.txt").read_text(encoding="utf-8")
    assert all(url in llms for url in PAGES.values())
    assert "must not be the sole production release gate" in llms
    assert "must never be uploaded" in llms


def test_social_card_and_styles_have_expected_accessibility_features() -> None:
    card = ET.parse(SITE / "assets/social-card.svg").getroot()
    assert card.attrib["width"] == "1200"
    assert card.attrib["height"] == "630"
    assert card.find("{http://www.w3.org/2000/svg}title") is not None
    assert card.find("{http://www.w3.org/2000/svg}desc") is not None

    png = (SITE / "assets/social-card.png").read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", png[16:24]) == (1200, 630)

    styles = (SITE / "assets/site.css").read_text(encoding="utf-8")
    assert ":focus-visible" in styles
    assert "prefers-reduced-motion" in styles
    assert "url(http" not in styles.lower()


def test_launch_video_is_local_accessible_and_web_compatible() -> None:
    _, parser = _parse(SITE / "index.html")
    videos = _matching(parser, "video", poster="assets/restore-rehearsal-launch.jpg")
    assert len(videos) == 1
    assert videos[0]["controls"] == ""
    assert videos[0]["preload"] == "metadata"
    assert videos[0]["aria-describedby"] == ("launch-film-description launch-film-credit")
    assert _matching(
        parser,
        "source",
        src="assets/restore-rehearsal-launch.mp4",
        type="video/mp4",
    )

    poster = (SITE / "assets/restore-rehearsal-launch.jpg").read_bytes()
    video = (SITE / "assets/restore-rehearsal-launch.mp4").read_bytes()
    assert poster.startswith(b"\xff\xd8\xff")
    assert video[4:8] == b"ftyp"
    assert len(video) < 2_000_000


def test_pages_workflow_is_pinned_and_minimally_scoped() -> None:
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    actions = re.findall(r"uses:\s*([^\s#]+)", workflow)
    assert len(actions) == 4
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in actions)
    assert "contents: read" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    assert "enablement: true" in workflow
    assert "path: site" in workflow
    assert "pull_request:" not in workflow
