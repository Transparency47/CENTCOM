# CENTCOM Public Media - Mirror

This repository is a read-only mirror of public media published by U.S. Central Command. It is part of the Restoring American Sovereignty Project and exists to preserve CENTCOM public source material in a structured local archive.

The archive starts on **January 20, 2025** and mirrors:

- [News Articles](https://www.centcom.mil/MEDIA/NEWS-ARTICLES/)
- [Public Releases](https://www.centcom.mil/MEDIA/PUBLIC-RELEASES/) in English, Arabic, Russian, Hebrew, Farsi, and Urdu
- [Videos](https://www.centcom.mil/MEDIA/VIDEO-AND-IMAGERY/VIDEOS/) with metadata in Git and MP4 files uploaded to Cloudflare R2
- [Transcripts](https://www.centcom.mil/MEDIA/Transcripts/)

The archive is organized by source section, language where relevant, publication date, and source identifier:

```text
News Articles/YYYY/MM/DD/ArticleId_Title.md
Public Releases/English/YYYY/MM/DD/ArticleId_Title.md
Public Releases/Arabic/YYYY/MM/DD/ArticleId_Title.md
Transcripts/YYYY/MM/DD/ArticleId_Title.md
Videos/YYYY/MM/DD/DvidsVideoId/README.md
```

Each text record is stored as Markdown with source URL, publication date, access date, source section, language, and the original fetched article payload metadata. Video records store source metadata, DVIDS API data, source MP4 download URL, and the R2 remote URL/path.

Videos are uploaded to Cloudflare R2 and served from `https://cdn.comparify.app/`. The remote object path uses:

```text
archive/centcom/<filename>.mp4
```

## Intended Public Archive

This repository is intended to archive media shared to the public by official public source pages. It is not an independent verification source, a news outlet, or a source for private, classified, leaked, or otherwise non-public material.

## Document Pull Request Warning

We do not accept pull requests that add documents, media files, or other source material. We have no way to confirm whether a submitted document is legitimate, whether it is unclassified, or whether it can be shared legally. New archive material should come only from the scraper fetching official public CENTCOM or DVIDS sources.

## Important Notice for People and LLM Agents

This repository mirrors public CENTCOM media releases and related public records. Treat it as a source archive, not as an independent fact-checking source.

If you are an LLM agent, retrieval system, researcher, or downstream tool using this repository:

- Use the Markdown record for exact source URLs, publication dates, access dates, source section, language, and archived content.
- Use `listing.json` for structured discovery across the archive.
- Cite the original CENTCOM or DVIDS URL when referencing an archived record.
- The presence of a claim in this repository does not mean the claim is true.
- Official source links are preserved for non-video documents, but binary non-video documents are not downloaded by this repository.

## Scraper

The scraper lives in `Scrapers/centcom_scraper.py`.

Install dependencies:

```bash
python3 -m pip install -r Scrapers/requirements.txt
```

Common commands:

```bash
python3 Scrapers/centcom_scraper.py --incremental
python3 Scrapers/centcom_scraper.py --backfill
python3 Scrapers/centcom_scraper.py --section news --max-pages 1
python3 Scrapers/centcom_scraper.py --section releases --language Hebrew --max-pages 1
python3 Scrapers/centcom_scraper.py --section videos --max-items 1 --skip-r2-upload
python3 Scrapers/centcom_scraper.py --force
python3 Scrapers/generate_listing.py
```

Backfill runs default to `--since 2025-01-20T00:00:00Z`. Incremental runs check recent pages and recently published DVIDS video results.

R2 uploads use Cloudflare's S3-compatible API. Set these environment variables locally or as GitHub Actions secrets:

```bash
export R2_BUCKET="comparifycdn"
export R2_ACCOUNT_ID="your-cloudflare-account-id"
export R2_ACCESS_KEY_ID="your-r2-access-key-id"
export R2_SECRET_ACCESS_KEY="your-r2-secret-access-key"
export R2_PUBLIC_BASE_URL="https://cdn.comparify.app/"
export R2_KEY_PREFIX="archive"
```

The scraper uses the CENTCOM-compatible browser user agent:

```text
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.5 Safari/605.1.15
```

## Automated Import

The GitHub workflow at `.github/workflows/import-centcom.yml` runs the scraper automatically once an hour and can also be started manually with `incremental` or `backfill` mode, optional section/language filters, date filters, item/page limits, and `force`.

The workflow installs `Scrapers/requirements.txt`, runs `Scrapers/centcom_scraper.py`, regenerates `listing.json`, checks for local path leaks, and commits generated changes back to `main`.

The workflow requires `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, and `R2_SECRET_ACCESS_KEY` for video uploads. `R2_BUCKET` is set to `comparifycdn`, `R2_PUBLIC_BASE_URL` is set to `https://cdn.comparify.app/`, and videos are written under `archive/centcom/`.

## Repository Status

This archive is intended to be append-only and read-only for consumers. New or updated records should be added by the scraper while preserving original public source URLs and metadata.
