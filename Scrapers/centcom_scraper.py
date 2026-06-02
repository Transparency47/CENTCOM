#!/usr/bin/env python3
"""Mirror public CENTCOM media records."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import mimetypes
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from dateutil import parser as date_parser

from r2_media import R2Config, media_public_url, upload_file, video_object_key


ROOT_DIR = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT_DIR / "Scrapers" / "state.json"
LISTING_GENERATOR_PATH = ROOT_DIR / "Scrapers" / "generate_listing.py"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.5 Safari/605.1.15"
ARCHIVE_START = "2025-01-20T00:00:00Z"
CENTCOM_VIDEO_URL = "https://www.centcom.mil/MEDIA/VIDEO-AND-IMAGERY/VIDEOS/"
CENTCOM_ORIGIN = "https://www.centcom.mil"
REQUEST_TIMEOUT = 45
DEFAULT_INCREMENTAL_PAGES = 3
DEFAULT_INCREMENTAL_VIDEO_ITEMS = 50


@dataclass(frozen=True)
class TextSource:
    key: str
    section: str
    language: str
    base_url: str
    output_root: Path
    kind: str
    body_selector: str


TEXT_SOURCES: list[TextSource] = [
    TextSource(
        key="news",
        section="News Articles",
        language="English",
        base_url="https://www.centcom.mil/MEDIA/NEWS-ARTICLES/",
        output_root=ROOT_DIR / "News Articles",
        kind="centcom_news_article",
        body_selector="#news-content",
    ),
    TextSource(
        key="releases:English",
        section="Public Releases",
        language="English",
        base_url="https://www.centcom.mil/MEDIA/PUBLIC-RELEASES/",
        output_root=ROOT_DIR / "Public Releases" / "English",
        kind="centcom_public_release",
        body_selector="#news-content",
    ),
    TextSource(
        key="releases:Arabic",
        section="Public Releases",
        language="Arabic",
        base_url="https://www.centcom.mil/MEDIA/PUBLIC-RELEASES/ARABIC-PUBLIC-RELEASES/",
        output_root=ROOT_DIR / "Public Releases" / "Arabic",
        kind="centcom_public_release",
        body_selector="#news-content",
    ),
    TextSource(
        key="releases:Russian",
        section="Public Releases",
        language="Russian",
        base_url="https://www.centcom.mil/MEDIA/PUBLIC-RELEASES/RUSSIAN-PUBLIC-RELEASES/",
        output_root=ROOT_DIR / "Public Releases" / "Russian",
        kind="centcom_public_release",
        body_selector="#news-content",
    ),
    TextSource(
        key="releases:Hebrew",
        section="Public Releases",
        language="Hebrew",
        base_url="https://www.centcom.mil/MEDIA/PUBLIC-RELEASES/HEBREW-PUBLIC-RELEASES/",
        output_root=ROOT_DIR / "Public Releases" / "Hebrew",
        kind="centcom_public_release",
        body_selector="#news-content",
    ),
    TextSource(
        key="releases:Farsi",
        section="Public Releases",
        language="Farsi",
        base_url="https://www.centcom.mil/MEDIA/PUBLIC-RELEASES/FARSI-PUBLIC-RELEASES/",
        output_root=ROOT_DIR / "Public Releases" / "Farsi",
        kind="centcom_public_release",
        body_selector="#news-content",
    ),
    TextSource(
        key="releases:Urdu",
        section="Public Releases",
        language="Urdu",
        base_url="https://www.centcom.mil/MEDIA/PUBLIC-RELEASES/URDU-PUBLIC-RELEASES/",
        output_root=ROOT_DIR / "Public Releases" / "Urdu",
        kind="centcom_public_release",
        body_selector="#news-content",
    ),
    TextSource(
        key="transcripts",
        section="Transcripts",
        language="English",
        base_url="https://www.centcom.mil/MEDIA/Transcripts/",
        output_root=ROOT_DIR / "Transcripts",
        kind="centcom_transcript",
        body_selector="#transcript-content",
    ),
]


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = date_parser.parse(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def isoformat(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def slugify(value: str | None, max_length: int = 96) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    text = re.sub(r"_+", "_", text)
    return (text[:max_length].strip("_") or "untitled")


def article_id_from_url(url: str) -> str:
    match = re.search(r"/Article/(\d+)/", url)
    if match:
        return match.group(1)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"url_{digest}"


def video_id_from_asset_id(value: str) -> str:
    return value.split(":", 1)[-1].strip()


def write_if_changed(path: Path, body: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8", errors="replace") == body:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return True


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"text": {}, "videos": {}}
    with STATE_PATH.open("r", encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("text", {})
    state.setdefault("videos", {})
    return state


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(STATE_PATH)


def session_for_centcom() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def dvids_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": CENTCOM_VIDEO_URL,
        "Origin": CENTCOM_ORIGIN,
    }


def fetch(session: requests.Session, url: str) -> requests.Response:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    if "Access Denied" in response.text[:1000]:
        raise RuntimeError(f"Access denied while fetching {url}")
    return response


def page_url(base_url: str, page: int) -> str:
    if page <= 1:
        return base_url
    return f"{base_url.rstrip('/')}/?Page={page}"


def discover_max_page(soup: BeautifulSoup, base_url: str) -> int | None:
    max_page: int | None = None
    base_path = urlparse(base_url).path.rstrip("/").lower()
    for anchor in soup.select("a[href]"):
        href = urljoin(base_url, anchor["href"])
        parsed = urlparse(href)
        if parsed.path.rstrip("/").lower() != base_path:
            continue
        value = parse_qs(parsed.query).get("Page") or parse_qs(parsed.query).get("page")
        if not value:
            continue
        try:
            page = int(value[0])
        except (TypeError, ValueError):
            continue
        max_page = page if max_page is None else max(max_page, page)
    return max_page


def article_links_from_listing(soup: BeautifulSoup, base_url: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = urljoin(base_url, anchor["href"]).split("#", 1)[0]
        if "/Article/" not in urlparse(href).path:
            continue
        if href in seen:
            continue
        seen.add(href)
        urls.append(href)
    return urls


def article_container(soup: BeautifulSoup, source: TextSource) -> Tag:
    container = soup.select_one(source.body_selector)
    if container is not None:
        return container
    fallback = soup.select_one(".article-body") or soup.select_one(".article-view")
    if fallback is not None:
        return fallback
    raise RuntimeError(f"Could not find article container for {source.base_url}")


def markdown_from_body(body: Tag, source_url: str) -> str:
    lines: list[str] = []
    for child in body.children:
        append_markdown_node(child, lines, source_url)
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        cleaned = line.rstrip()
        blank = cleaned == ""
        if blank and previous_blank:
            continue
        compact.append(cleaned)
        previous_blank = blank
    return "\n".join(compact).strip()


def append_markdown_node(node: Any, lines: list[str], source_url: str) -> None:
    if isinstance(node, str):
        text = clean_text(node)
        if text:
            lines.append(text)
        return
    if not isinstance(node, Tag):
        return
    name = node.name.lower()
    if name in {"script", "style", "noscript"}:
        return
    if name in {"h1", "h2", "h3", "h4"}:
        text = inline_markdown(node, source_url)
        if text:
            level = {"h1": "#", "h2": "##", "h3": "###", "h4": "####"}[name]
            lines.extend(["", f"{level} {text}", ""])
        return
    if name == "p":
        text = inline_markdown(node, source_url)
        if text:
            lines.extend([text, ""])
        return
    if name in {"ul", "ol"}:
        ordered = name == "ol"
        for index, item in enumerate(node.find_all("li", recursive=False), 1):
            text = inline_markdown(item, source_url)
            if text:
                marker = f"{index}." if ordered else "-"
                lines.append(f"{marker} {text}")
        lines.append("")
        return
    if name == "blockquote":
        text = inline_markdown(node, source_url)
        if text:
            for line in text.splitlines():
                lines.append(f"> {line}")
            lines.append("")
        return
    if name == "br":
        lines.append("")
        return
    if name == "table":
        text = clean_text(node.get_text(" ", strip=True))
        if text:
            lines.extend([text, ""])
        return
    for child in node.children:
        append_markdown_node(child, lines, source_url)


def inline_markdown(node: Tag, source_url: str) -> str:
    pieces: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            pieces.append(child)
        elif isinstance(child, Tag):
            name = child.name.lower()
            if name in {"script", "style", "noscript"}:
                continue
            if name == "a" and child.get("href"):
                text = clean_text(child.get_text(" ", strip=True))
                href = urljoin(source_url, child["href"])
                if text:
                    pieces.append(f"[{text}]({href})")
            elif name == "br":
                pieces.append("\n")
            else:
                pieces.append(inline_markdown(child, source_url))
    text = "".join(pieces)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def parse_article(session: requests.Session, source: TextSource, url: str) -> dict[str, Any]:
    response = fetch(session, url)
    soup = BeautifulSoup(response.text, "html.parser")
    container = article_container(soup, source)
    title = clean_text((container.select_one(".title") or soup.select_one("h1.title") or soup.select_one("title")).get_text(" ", strip=True))
    category_date = clean_text((container.select_one(".category-date") or container.select_one(".header")).get_text(" ", strip=True) if (container.select_one(".category-date") or container.select_one(".header")) else "")
    date_value = parse_date(category_date.split("|", 1)[-1].strip() if "|" in category_date else category_date)
    if not date_value:
        date_value = parse_date(soup.select_one("meta[property='article:published_time']")["content"] if soup.select_one("meta[property='article:published_time']") else None)
    body = container.select_one(".body") or container
    markdown_body = markdown_from_body(body, url)
    if not title:
        title = first_heading_from_markdown(markdown_body) or "Untitled CENTCOM record"
    return {
        "articleId": article_id_from_url(url),
        "title": title,
        "sourceUrl": url,
        "datePublished": isoformat(date_value),
        "dateAccessed": isoformat(utc_now()),
        "section": source.section,
        "language": source.language,
        "kind": source.kind,
        "categoryDate": category_date,
        "body": markdown_body,
    }


def first_heading_from_markdown(markdown: str) -> str | None:
    match = re.search(r"^#+\s+(.+?)\s*$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else None


def record_path_for_article(source: TextSource, record: dict[str, Any]) -> Path:
    published = parse_date(record.get("datePublished")) or utc_now()
    filename = f"{record['articleId']}_{slugify(record['title'])}.md"
    return source.output_root / f"{published.year:04d}" / f"{published.month:02d}" / f"{published.day:02d}" / filename


def article_markdown(record: dict[str, Any]) -> str:
    metadata = [
        f"<!-- source: {record['sourceUrl']} -->",
        f"<!-- date_published: {record.get('datePublished') or 'Unknown'} -->",
        f"<!-- date_accessed: {record.get('dateAccessed') or 'Unknown'} -->",
        f"<!-- section: {record['section']} -->",
        f"<!-- language: {record['language']} -->",
        f"<!-- article_id: {record['articleId']} -->",
        "",
        f"# {record['title']}",
        "",
        "## Source Metadata",
        "",
        f"- Source URL: {record['sourceUrl']}",
        f"- Article ID: {record['articleId']}",
        f"- Section: {record['section']}",
        f"- Language: {record['language']}",
        f"- Date published: {record.get('datePublished') or 'Unknown'}",
        f"- Date accessed: {record.get('dateAccessed') or 'Unknown'}",
        "",
    ]
    body = record.get("body") or "_No article body text was extracted._"
    if body.startswith("# "):
        body = re.sub(r"^#\s+.+?\n+", "", body, count=1)
    return "\n".join(metadata).rstrip() + "\n\n" + body.strip() + "\n"


def select_text_sources(section: str | None, language: str | None) -> list[TextSource]:
    requested = (section or "all").lower()
    selected: list[TextSource] = []
    for source in TEXT_SOURCES:
        if requested not in {"all", "text"}:
            if requested in {"news", "articles"} and source.key != "news":
                continue
            if requested in {"releases", "public-releases", "public releases"} and not source.key.startswith("releases:"):
                continue
            if requested in {"transcripts", "transcript"} and source.key != "transcripts":
                continue
            if requested == "videos":
                continue
        if language and source.language.lower() != language.lower():
            continue
        selected.append(source)
    return selected


def scrape_text_source(
    args: argparse.Namespace,
    session: requests.Session,
    state: dict[str, Any],
    source: TextSource,
    since: dt.datetime,
    until: dt.datetime | None,
) -> tuple[int, int]:
    changed = 0
    seen = 0
    page_limit = args.max_pages
    if page_limit is None and args.mode == "incremental":
        page_limit = DEFAULT_INCREMENTAL_PAGES

    first_page = fetch(session, source.base_url)
    first_soup = BeautifulSoup(first_page.text, "html.parser")
    max_page = discover_max_page(first_soup, source.base_url)
    page = 1
    while True:
        if page_limit and page > page_limit:
            break
        if max_page and page > max_page:
            break
        soup = first_soup if page == 1 else BeautifulSoup(fetch(session, page_url(source.base_url, page)).text, "html.parser")
        links = article_links_from_listing(soup, source.base_url)
        if not links:
            break
        print(f"{source.section}/{source.language}: page {page} has {len(links)} article links", flush=True)

        page_dates: list[dt.datetime] = []
        for url in links:
            if args.max_items and seen >= args.max_items:
                return changed, seen
            state_key = f"{source.key}:{url}"
            if not args.force and state["text"].get(state_key, {}).get("path"):
                existing_date = parse_date(state["text"][state_key].get("datePublished"))
                if existing_date:
                    page_dates.append(existing_date)
                if args.mode == "incremental":
                    seen += 1
                    continue
            try:
                record = parse_article(session, source, url)
            except Exception as exc:
                print(f"WARNING {source.key}: could not fetch {url}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                continue
            published = parse_date(record.get("datePublished"))
            if published:
                page_dates.append(published)
                if published < since:
                    continue
                if until and published > until:
                    continue
            path = record_path_for_article(source, record)
            if write_if_changed(path, article_markdown(record)):
                changed += 1
            state["text"][state_key] = {
                "datePublished": record.get("datePublished"),
                "dateAccessed": record.get("dateAccessed"),
                "path": path.relative_to(ROOT_DIR).as_posix(),
                "title": record["title"],
            }
            seen += 1
            time.sleep(args.delay)

        if args.mode == "backfill" and page_dates and max(page_dates) < since:
            break
        page += 1
    return changed, seen


def dvids_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    params = {**params, "api_key": args_api_key()}
    response = requests.get(f"https://api.dvidshub.net{path}", params=params, headers=dvids_headers(), timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError("; ".join(str(item) for item in payload["errors"]))
    return payload


def args_api_key() -> str:
    value = getattr(args_api_key, "value", None)
    if not value:
        raise RuntimeError("DVIDS API key is unavailable. Could not discover it from the CENTCOM video page.")
    return value


def set_dvids_api_key(value: str | None) -> None:
    setattr(args_api_key, "value", value)


def discover_dvids_api_key(session: requests.Session) -> str:
    html = fetch(session, CENTCOM_VIDEO_URL).text
    match = re.search(r"api_key=(key-[A-Za-z0-9]+)", html)
    if not match:
        raise RuntimeError("Could not discover a DVIDS API key from the CENTCOM video player.")
    return match.group(1)


def dvids_search_videos(since: dt.datetime, until: dt.datetime | None, max_items: int | None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    page = 1
    while True:
        params: dict[str, Any] = {
            "type": "video",
            "unit_name": "U.S. Central Command Public Affairs",
            "from_date": isoformat(since),
            "max_results": 50,
            "page": page,
            "sort": "date",
            "sortdir": "desc",
        }
        if until:
            params["to_date"] = isoformat(until)
        payload = dvids_get("/search", params)
        batch = payload.get("results") or []
        if not batch:
            break
        for item in batch:
            results.append(item)
            if max_items and len(results) >= max_items:
                return results
        info = payload.get("page_info") or {}
        total = int(info.get("total_results") or 0)
        per_page = int(info.get("results_per_page") or len(batch) or 50)
        if page * per_page >= total:
            break
        page += 1
    return results


def discovered_video_ids_from_centcom(session: requests.Session) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    html = fetch(session, CENTCOM_VIDEO_URL).text
    playlist_hashes = re.findall(r"dvpplaylist=([a-f0-9]+)", html)
    for page_html in [html, *[fetch(session, f"{CENTCOM_VIDEO_URL}?dvpmoduleid=37619&dvpplaylist={playlist}").text for playlist in dict.fromkeys(playlist_hashes)]]:
        for video_id in re.findall(r"videoid=(\d+)&dvpmoduleid=37619", page_html):
            if video_id not in seen:
                seen.add(video_id)
                ids.append(video_id)
    return ids


def dvids_asset(asset_id: str) -> dict[str, Any]:
    payload = dvids_get("/asset", {"id": asset_id})
    result = payload.get("results")
    if not isinstance(result, dict):
        raise RuntimeError(f"DVIDS asset response did not include results for {asset_id}")
    return result


def choose_video_file(asset: dict[str, Any]) -> dict[str, Any] | None:
    files = [item for item in asset.get("files") or [] if item.get("src") and str(item.get("type", "")).startswith("video/")]
    if not files:
        return None
    for item in files:
        src = str(item.get("src") or "")
        if item.get("height") == 1080 and "6000k" in src:
            return item
    return max(files, key=lambda item: (int(item.get("height") or 0), int(item.get("width") or 0), int(item.get("size") or 0)))


def download_video(session: requests.Session, url: str, target: Path, max_mb: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    limit = max_mb * 1024 * 1024
    with session.get(url, stream=True, timeout=REQUEST_TIMEOUT) as response:
        response.raise_for_status()
        total = 0
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > limit:
                    raise RuntimeError(f"Video exceeded {max_mb} MB while downloading")
                handle.write(chunk)


def video_readme(asset: dict[str, Any], selected_file: dict[str, Any] | None, remote_url: str | None, remote_path: str | None, local_file: str | None) -> str:
    video_id = video_id_from_asset_id(asset["id"])
    date_published = parse_date(asset.get("date_published") or asset.get("publishdate") or asset.get("date"))
    date_accessed = isoformat(utc_now())
    lines = [
        f"<!-- source: {asset.get('url') or ''} -->",
        f"<!-- date_published: {isoformat(date_published) or 'Unknown'} -->",
        f"<!-- date_accessed: {date_accessed} -->",
        f"<!-- section: Videos -->",
        f"<!-- dvids_video_id: {video_id} -->",
        "",
        f"# {asset.get('title') or f'DVIDS Video {video_id}'}",
        "",
        "## Source Metadata",
        "",
        f"- Source URL: {asset.get('url') or 'Unknown'}",
        f"- DVIDS video ID: {video_id}",
        f"- Date published: {isoformat(date_published) or 'Unknown'}",
        f"- Date recorded: {asset.get('date') or 'Unknown'}",
        f"- Date accessed: {date_accessed}",
        f"- Unit: {asset.get('unit_name') or 'Unknown'}",
        f"- Credit: {credit_text(asset.get('credit')) or asset.get('credit') or 'Unknown'}",
        f"- Duration seconds: {asset.get('duration') or 'Unknown'}",
        f"- Thumbnail: {thumbnail_url(asset) or 'Unknown'}",
        f"- HLS URL: {redact_api_key(asset.get('hls_url')) or 'Unknown'}",
    ]
    if selected_file:
        lines.extend(
            [
                f"- Download URL: {selected_file.get('src')}",
                f"- Download content type: {selected_file.get('type') or 'video/mp4'}",
                f"- Download width: {selected_file.get('width') or 'Unknown'}",
                f"- Download height: {selected_file.get('height') or 'Unknown'}",
                f"- Download size: {selected_file.get('size') or 'Unknown'}",
            ]
        )
    if local_file:
        lines.append(f"- Local file: {local_file}")
    if remote_url:
        lines.append(f"- Remote URL: {remote_url}")
    if remote_path:
        lines.append(f"- Remote path: {remote_path}")
    description = clean_text(asset.get("description") or asset.get("short_description"))
    if description:
        lines.extend(["", "## Description", "", description])
    lines.extend(["", "## DVIDS API Data", "", "```json", json.dumps(redact_api_keys(asset), indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)


def redact_api_key(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return re.sub(r"([?&]api_key=)[^&\s\"']+", r"\1REDACTED", value)


def redact_api_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_api_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_api_keys(item) for item in value]
    if isinstance(value, str):
        return redact_api_key(value)
    return value


def credit_text(value: Any) -> str | None:
    if isinstance(value, list):
        names = []
        for item in value:
            if isinstance(item, dict):
                rank = item.get("rank") or ""
                name = item.get("name") or ""
                text = clean_text(f"{rank} {name}")
                if text:
                    names.append(text)
        return ", ".join(names) or None
    if isinstance(value, str):
        return value
    return None


def thumbnail_url(asset: dict[str, Any]) -> str | None:
    thumbnail = asset.get("thumbnail")
    if isinstance(thumbnail, dict):
        return thumbnail.get("url")
    if isinstance(thumbnail, str):
        return thumbnail
    return asset.get("image")


def video_record_dir(asset: dict[str, Any]) -> Path:
    date_value = parse_date(asset.get("date_published") or asset.get("publishdate") or asset.get("date")) or utc_now()
    video_id = video_id_from_asset_id(asset["id"])
    return ROOT_DIR / "Videos" / f"{date_value.year:04d}" / f"{date_value.month:02d}" / f"{date_value.day:02d}" / video_id


def scrape_videos(
    args: argparse.Namespace,
    session: requests.Session,
    state: dict[str, Any],
    since: dt.datetime,
    until: dt.datetime | None,
) -> tuple[int, int]:
    if args.require_r2_upload and args.skip_r2_upload:
        raise RuntimeError("--require-r2-upload cannot be combined with --skip-r2-upload")
    config = R2Config.from_env()
    if args.require_r2_upload and not config.can_upload:
        raise RuntimeError(f"R2 upload is required but not configured; missing {', '.join(config.missing_settings())}.")

    max_items = args.max_items
    if max_items is None and args.mode == "incremental":
        max_items = DEFAULT_INCREMENTAL_VIDEO_ITEMS
    search_items = dvids_search_videos(since, until, max_items)
    asset_ids = [item["id"] for item in search_items if item.get("id")]
    for video_id in discovered_video_ids_from_centcom(session):
        asset_id = f"video:{video_id}"
        if asset_id not in asset_ids:
            asset_ids.append(asset_id)
    if args.max_items:
        asset_ids = asset_ids[: args.max_items]

    changed = 0
    seen = 0
    for asset_id in asset_ids:
        video_id = video_id_from_asset_id(asset_id)
        state_entry = state["videos"].get(video_id, {})
        has_remote_video = bool(state_entry.get("remotePath") and state_entry.get("remoteUrl"))
        if (
            state_entry.get("path")
            and not args.force
            and args.mode == "incremental"
            and (args.skip_r2_upload or has_remote_video)
        ):
            seen += 1
            continue
        try:
            asset = dvids_asset(asset_id)
        except Exception as exc:
            print(f"WARNING videos: could not fetch DVIDS asset {asset_id}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            continue
        published = parse_date(asset.get("date_published") or asset.get("publishdate") or asset.get("date"))
        if published and published < since:
            continue
        if published and until and published > until:
            continue
        selected_file = choose_video_file(asset)
        if not selected_file:
            print(f"WARNING videos: {asset_id} has no downloadable MP4", file=sys.stderr, flush=True)
            continue

        record_dir = video_record_dir(asset)
        local_media_name = Path(urlparse(selected_file["src"]).path).name or f"{video_id}.mp4"
        local_media_rel = f"media/{local_media_name}"
        remote_path = video_object_key(source_url=selected_file.get("src"), video_id=video_id, key_prefix=config.key_prefix)
        remote_url = media_public_url(remote_path, config.public_base_url)
        uploaded_to_r2 = False

        if not args.skip_r2_upload:
            tmp_dir = Path(tempfile.mkdtemp(prefix="centcom-video-"))
            try:
                if not config.can_upload:
                    raise RuntimeError(f"R2 upload is not configured; missing {', '.join(config.missing_settings())}.")
                tmp_file = tmp_dir / local_media_name
                download_video(session, selected_file["src"], tmp_file, args.max_video_mb)
                upload_file(tmp_file, remote_path, config, selected_file.get("type") or "video/mp4")
                uploaded_to_r2 = True
            except Exception as exc:
                if args.require_r2_upload:
                    raise RuntimeError(f"Could not upload {asset_id} to R2: {exc}") from exc
                print(f"WARNING videos: could not upload {asset_id} to R2: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        readme = video_readme(
            asset,
            selected_file,
            remote_url if uploaded_to_r2 else None,
            remote_path if uploaded_to_r2 else None,
            local_media_rel if uploaded_to_r2 else None,
        )
        readme_path = record_dir / "README.md"
        if write_if_changed(readme_path, readme):
            changed += 1
        state["videos"][video_id] = {
            "datePublished": isoformat(published),
            "path": readme_path.relative_to(ROOT_DIR).as_posix(),
            "remotePath": remote_path if uploaded_to_r2 else None,
            "remoteUrl": remote_url if uploaded_to_r2 else None,
            "title": asset.get("title"),
        }
        seen += 1
        time.sleep(args.delay)
    return changed, seen


def run(args: argparse.Namespace) -> int:
    set_dvids_api_key(args.dvids_api_key)
    since = parse_date(args.since or ARCHIVE_START)
    if since is None:
        raise RuntimeError(f"Invalid --since value: {args.since}")
    until = parse_date(args.until) if args.until and args.until.lower() != "now" else (utc_now() if args.until else None)

    session = session_for_centcom()
    state = load_state()
    total_changed = 0
    total_seen = 0
    includes_videos = args.section.lower() in {"all", "videos", "video"}
    if includes_videos and not args.dvids_api_key:
        set_dvids_api_key(discover_dvids_api_key(session))

    selected_text_sources = select_text_sources(args.section, args.language)
    if args.section.lower() in {"all", "text", "news", "articles", "releases", "public-releases", "public releases", "transcripts", "transcript"}:
        for source in selected_text_sources:
            changed, seen = scrape_text_source(args, session, state, source, since, until)
            total_changed += changed
            total_seen += seen
            save_state(state)

    if includes_videos:
        changed, seen = scrape_videos(args, session, state, since, until)
        total_changed += changed
        total_seen += seen
        save_state(state)

    print(f"Done. changed={total_changed} seen={total_seen}", flush=True)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mirror public CENTCOM media.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--incremental", dest="mode", action="store_const", const="incremental", help="Fetch recent records.")
    mode.add_argument("--backfill", dest="mode", action="store_const", const="backfill", help="Backfill to the archive start date.")
    parser.set_defaults(mode="incremental")
    parser.add_argument("--section", default="all", help="all, text, news, releases, transcripts, or videos.")
    parser.add_argument("--language", help="Language filter for public releases, such as English, Arabic, Russian, Hebrew, Farsi, or Urdu.")
    parser.add_argument("--since", default=ARCHIVE_START, help="Archive records published at or after this date/time.")
    parser.add_argument("--until", help="Archive records published at or before this date/time, or 'now'.")
    parser.add_argument("--max-pages", type=int, help="Maximum listing pages per text source.")
    parser.add_argument("--max-items", type=int, help="Maximum records per source/run.")
    parser.add_argument("--max-video-mb", type=int, default=2048, help="Maximum video download size before aborting.")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between fetched records.")
    parser.add_argument("--force", action="store_true", help="Re-fetch and overwrite known records.")
    parser.add_argument("--skip-r2-upload", action="store_true", help="Write video metadata without uploading MP4 files to R2.")
    parser.add_argument("--require-r2-upload", action="store_true", help="Fail if a video cannot be uploaded to R2.")
    parser.add_argument("--dvids-api-key", help="Override the public DVIDS API key exposed by CENTCOM's video player.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
