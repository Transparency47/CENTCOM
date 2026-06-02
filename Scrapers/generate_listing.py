#!/usr/bin/env python3
"""Generate listing.json for the CENTCOM public media archive."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
LISTING_PATH = ROOT_DIR / "listing.json"


def stable_id(source: str, path: str) -> str:
    digest = hashlib.sha1(f"{source}:{path}".encode("utf-8")).hexdigest()[:16]
    return f"{source}:{digest}"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def metadata_line(markdown: str, label: str) -> str | None:
    pattern = re.compile(rf"^-\s+{re.escape(label)}:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
    match = pattern.search(markdown)
    return match.group(1).strip() if match else None


def html_comment(markdown: str, label: str) -> str | None:
    match = re.search(rf"<!--\s*{re.escape(label)}:\s*(.*?)\s*-->", markdown, re.IGNORECASE)
    return match.group(1).strip() if match else None


def first_heading(markdown: str) -> str | None:
    match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else None


def summary_from(markdown: str) -> str | None:
    body = re.sub(r"```json[\s\S]*?```", "", markdown)
    body = re.sub(r"<!--[\s\S]*?-->", "", body)
    body = re.sub(r"^#\s+.+?$", "", body, count=1, flags=re.MULTILINE)
    body = re.sub(r"^##\s+Source Metadata\s*$[\s\S]*?(?=^##\s+|\Z)", "", body, flags=re.MULTILINE)
    body = re.sub(r"^##\s+DVIDS API Data\s*$[\s\S]*", "", body, flags=re.MULTILINE)
    paragraphs = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n\s*\n", body)]
    for paragraph in paragraphs:
        if paragraph and not paragraph.startswith("#") and not paragraph.startswith("- "):
            return paragraph[:280]
    return None


def kind_from_path(relative_path: str) -> str:
    if relative_path.startswith("News Articles/"):
        return "centcom_news_article"
    if relative_path.startswith("Public Releases/"):
        return "centcom_public_release"
    if relative_path.startswith("Transcripts/"):
        return "centcom_transcript"
    if relative_path.startswith("Videos/"):
        return "centcom_video"
    return "centcom_record"


def category_from_path(relative_path: str) -> str:
    if relative_path.startswith("Public Releases/"):
        return "Public Releases"
    return relative_path.split("/", 1)[0]


def language_from_path(relative_path: str) -> str | None:
    if relative_path.startswith("Public Releases/"):
        parts = relative_path.split("/")
        return parts[1] if len(parts) > 1 else None
    return html_language_default(relative_path)


def html_language_default(relative_path: str) -> str | None:
    if relative_path.startswith(("News Articles/", "Transcripts/")):
        return "English"
    return None


def build_record(path: Path) -> dict:
    relative_path = path.relative_to(ROOT_DIR).as_posix()
    markdown = read_text(path)
    title = first_heading(markdown) or path.stem.replace("_", " ")
    date = html_comment(markdown, "date_published") or metadata_line(markdown, "Date published")
    if date == "Unknown":
        date = None
    date_short = date[:10] if date and len(date) >= 10 else date
    kind = kind_from_path(relative_path)
    metadata = {
        "dateAccessed": html_comment(markdown, "date_accessed") or metadata_line(markdown, "Date accessed"),
        "section": html_comment(markdown, "section") or metadata_line(markdown, "Section") or category_from_path(relative_path),
        "language": html_comment(markdown, "language") or metadata_line(markdown, "Language") or language_from_path(relative_path),
        "articleId": html_comment(markdown, "article_id") or metadata_line(markdown, "Article ID"),
        "dvidsVideoId": html_comment(markdown, "dvids_video_id") or metadata_line(markdown, "DVIDS video ID"),
        "remoteUrl": metadata_line(markdown, "Remote URL"),
        "remotePath": metadata_line(markdown, "Remote path"),
        "downloadUrl": metadata_line(markdown, "Download URL"),
        "thumbnail": metadata_line(markdown, "Thumbnail"),
    }
    return {
        "id": stable_id("centcom", relative_path),
        "title": title,
        "path": relative_path,
        "category": metadata["section"],
        "kind": kind,
        "date": date_short,
        "sourceUrl": html_comment(markdown, "source") or metadata_line(markdown, "Source URL"),
        "summary": summary_from(markdown),
        "metadata": {key: value for key, value in metadata.items() if value},
    }


def discover_records() -> list[Path]:
    records: list[Path] = []
    for path in ROOT_DIR.rglob("*.md"):
        relative = path.relative_to(ROOT_DIR).as_posix()
        if relative == "README.md" or relative.startswith("Scrapers/") or relative.startswith(".github/"):
            continue
        if relative.startswith(("News Articles/", "Public Releases/", "Transcripts/", "Videos/")):
            records.append(path)
    return sorted(records, key=lambda item: item.relative_to(ROOT_DIR).as_posix())


def build_listing() -> dict:
    records = [build_record(path) for path in discover_records()]
    records.sort(key=lambda row: (row.get("date") or "", row.get("title") or ""), reverse=True)
    return {
        "version": 1,
        "source": "centcom",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "records": records,
    }


def write_listing(path: Path = LISTING_PATH) -> None:
    listing = build_listing()
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(listing, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)
    print(f"Wrote {path.relative_to(ROOT_DIR)} with {len(listing['records'])} records.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate CENTCOM listing.json.")
    parser.parse_args()
    write_listing()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
