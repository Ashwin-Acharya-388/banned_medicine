"""
Web scraping module for the Indian Banned Medicines Data Pipeline.

Scrapes official Indian government sources (CDSCO, PIB) to discover and
download PDF notifications containing lists of banned/prohibited medicines.

**Ethical compliance**:
- Respects ``robots.txt`` via ``urllib.robotparser``.
- Implements rate limiting with random delays between requests.
- Rotates user-agents to avoid overwhelming any single endpoint.
- All data scraped is from *public* government notification pages.
- This tool is intended for public-health research purposes only.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from src import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metadata log helpers
# ---------------------------------------------------------------------------


def _load_metadata_log() -> list[dict]:
    """Load the JSON metadata log of previously downloaded PDFs."""
    if config.METADATA_LOG_FILE.exists():
        try:
            with open(config.METADATA_LOG_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, IOError) as exc:
            logger.warning("Could not read metadata log: %s", exc)
    return []


def _save_metadata_log(entries: list[dict]) -> None:
    """Persist the metadata log to disk."""
    with open(config.METADATA_LOG_FILE, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, default=str)


def _already_downloaded(url: str, metadata: list[dict]) -> bool:
    """Check if a URL has already been downloaded."""
    return any(entry.get("url") == url for entry in metadata)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitise_filename(name: str) -> str:
    """Replace characters that are problematic in file names."""
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:200]  # cap length


def _random_delay() -> None:
    """Sleep for a random interval between configured bounds."""
    delay = random.uniform(config.SCRAPE_DELAY_MIN, config.SCRAPE_DELAY_MAX)
    logger.debug("Sleeping %.1f s …", delay)
    time.sleep(delay)


def _get_random_headers() -> dict[str, str]:
    """Return a dict of HTTP headers with a randomly chosen User-Agent."""
    return {
        "User-Agent": random.choice(config.USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }


# ---------------------------------------------------------------------------
# robots.txt checker
# ---------------------------------------------------------------------------


class RobotsChecker:
    """Cache-backed robots.txt checker for a given domain."""

    def __init__(self) -> None:
        self._parsers: dict[str, RobotFileParser] = {}

    def can_fetch(self, url: str, user_agent: str = "*") -> bool:
        """Return True if ``url`` is allowed by the site's robots.txt."""
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"
        if domain not in self._parsers:
            rp = RobotFileParser()
            robots_url = f"{domain}/robots.txt"
            rp.set_url(robots_url)
            try:
                rp.read()
            except Exception as exc:
                logger.warning(
                    "Could not read robots.txt at %s: %s — allowing access.",
                    robots_url,
                    exc,
                )
                # If robots.txt is unreachable, assume access is allowed
                return True
            self._parsers[domain] = rp
        return self._parsers[domain].can_fetch(user_agent, url)


# ---------------------------------------------------------------------------
# Base Scraper
# ---------------------------------------------------------------------------


class BaseScraper:
    """
    Abstract base class providing common scraping primitives.

    Subclasses must implement :meth:`discover_pdf_links` and optionally
    override :meth:`run`.
    """

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name
        self.session = requests.Session()
        self.robots = RobotsChecker()
        self.metadata = _load_metadata_log()

    # ---- HTTP with retries ------------------------------------------------

    def _request_with_retries(
        self,
        url: str,
        *,
        stream: bool = False,
    ) -> Optional[requests.Response]:
        """
        Perform a GET request with exponential-backoff retry logic.

        Returns ``None`` if all retries are exhausted.
        """
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                _random_delay()
                headers = _get_random_headers()
                resp = self.session.get(
                    url,
                    headers=headers,
                    timeout=config.REQUEST_TIMEOUT,
                    stream=stream,
                    allow_redirects=True,
                )
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                wait = 2**attempt + random.uniform(0, 1)
                logger.warning(
                    "[%s] Request attempt %d/%d failed for %s: %s — "
                    "retrying in %.1f s",
                    self.source_name,
                    attempt,
                    config.MAX_RETRIES,
                    url,
                    exc,
                    wait,
                )
                time.sleep(wait)
        logger.error(
            "[%s] All %d retries exhausted for %s",
            self.source_name,
            config.MAX_RETRIES,
            url,
        )
        return None

    # ---- PDF download -----------------------------------------------------

    def download_pdf(self, url: str, dest_dir: Path) -> Optional[Path]:
        """
        Download a single PDF from *url* into *dest_dir*.

        Returns the local file path on success, ``None`` on failure.
        Respects robots.txt before downloading.
        """
        # Robots.txt check
        if not self.robots.can_fetch(url):
            logger.info(
                "[%s] Skipping %s — disallowed by robots.txt", self.source_name, url
            )
            return None

        # Already downloaded?
        if _already_downloaded(url, self.metadata):
            logger.debug("[%s] Already downloaded: %s", self.source_name, url)
            return None

        resp = self._request_with_retries(url, stream=True)
        if resp is None:
            return None

        # Determine filename
        url_path = urlparse(url).path
        raw_name = Path(url_path).name or "unknown.pdf"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        filename = _sanitise_filename(
            f"{self.source_name}_{timestamp}_{raw_name}"
        )
        if not filename.lower().endswith(".pdf"):
            filename += ".pdf"

        dest_path = dest_dir / filename
        try:
            with open(dest_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=8192):
                    fh.write(chunk)
        except IOError as exc:
            logger.error("[%s] Failed to write %s: %s", self.source_name, dest_path, exc)
            return None

        # Update metadata log
        self.metadata.append(
            {
                "url": url,
                "filename": filename,
                "filepath": str(dest_path),
                "source": self.source_name,
                "download_timestamp": datetime.now(timezone.utc).isoformat(),
                "http_status": resp.status_code,
            }
        )
        _save_metadata_log(self.metadata)
        logger.info("[%s] Downloaded: %s → %s", self.source_name, url, dest_path)
        return dest_path

    # ---- abstract ---------------------------------------------------------

    def discover_pdf_links(self) -> list[str]:
        """Return a list of PDF URLs discovered from the source. Override me."""
        raise NotImplementedError

    def run(self, dest_dir: Optional[Path] = None) -> list[Path]:
        """
        Execute the full scraping workflow: discover → filter → download.

        Returns a list of newly downloaded file paths.
        """
        dest = dest_dir or config.DOWNLOAD_DIR
        dest.mkdir(parents=True, exist_ok=True)

        logger.info("[%s] Starting scrape …", self.source_name)
        pdf_urls = self.discover_pdf_links()
        logger.info("[%s] Discovered %d PDF link(s).", self.source_name, len(pdf_urls))

        downloaded: list[Path] = []
        for url in pdf_urls:
            path = self.download_pdf(url, dest)
            if path:
                downloaded.append(path)

        logger.info(
            "[%s] Scrape complete. %d new PDF(s) downloaded.",
            self.source_name,
            len(downloaded),
        )
        return downloaded


# ---------------------------------------------------------------------------
# CDSCO Scraper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Keyword matching helpers (two-tier: strong + weak, with exclusions)
# ---------------------------------------------------------------------------

_STRONG_RE = re.compile(
    "|".join(re.escape(kw) for kw in config.BAN_KEYWORDS_STRONG),
    re.IGNORECASE,
)
_WEAK_RE = re.compile(
    "|".join(re.escape(kw) for kw in config.BAN_KEYWORDS_WEAK),
    re.IGNORECASE,
)
_EXCLUDE_RE = re.compile(
    "|".join(re.escape(kw) for kw in config.SCRAPER_EXCLUDE_KEYWORDS),
    re.IGNORECASE,
)


def _is_ban_related(text: str) -> bool:
    """
    Return True if *text* (link text + URL) looks related to a drug ban.

    Logic:
    1. If any EXCLUDE keyword matches → reject immediately.
    2. If any STRONG keyword matches → accept.
    3. If any WEAK keyword matches (and no exclusion) → accept.
    4. Otherwise → reject.
    """
    if _EXCLUDE_RE.search(text):
        return False
    if _STRONG_RE.search(text):
        return True
    if _WEAK_RE.search(text):
        return True
    return False


def is_drug_related_pdf(filepath: Path) -> bool:
    """
    Quick content-check on the first page of a downloaded PDF.

    Returns True if the document appears to be about drugs / medicines /
    pharmaceutical bans.  Used as a post-download filter to reject
    administrative documents that slipped through link-level filtering.
    """
    drug_context_re = re.compile(
        r"drug|medicine|pharmaceutical|pharmacopoeia|formulation|dosage|FDC"
        r"|fixed.dose|tablet|capsule|injection|syrup|suspension"
        r"|section\s*26|cosmetics?\s*act|gazette|prohibited|banned"
        r"|manufacture|therapeutic|irrational",
        re.IGNORECASE,
    )
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            # Check first 3 pages (or fewer)
            for page in pdf.pages[:3]:
                text = page.extract_text()
                if text and drug_context_re.search(text):
                    return True
    except Exception:
        pass
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        for page in reader.pages[:3]:
            text = page.extract_text()
            if text and drug_context_re.search(text):
                return True
    except Exception:
        pass
    return False


class CDSCOScraper(BaseScraper):
    """
    Scraper for the Central Drugs Standard Control Organisation (CDSCO)
    notifications page.

    Target: https://cdsco.gov.in/opencms/opencms/en/Notifications/
    """

    def __init__(self) -> None:
        super().__init__(source_name="cdsco")

    def discover_pdf_links(self) -> list[str]:
        """
        Parse the CDSCO notifications page and return URLs of ban-related PDFs.

        Handles both absolute and relative URLs, static .pdf, and JSP download wrapper URLs.
        """
        # Scrape multiple sub-sections of CDSCO notifications for better coverage
        sub_pages = [
            config.CDSCO_NOTIFICATIONS_URL,
            urljoin(config.CDSCO_NOTIFICATIONS_URL, "Gazette-Notifications/"),
            urljoin(config.CDSCO_NOTIFICATIONS_URL, "Public-Notices/"),
        ]

        all_links: list[str] = []

        for page_url in sub_pages:
            logger.info("[cdsco] Fetching page: %s", page_url)
            resp = self._request_with_retries(page_url)
            if resp is None:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href: str = anchor["href"]
                text: str = anchor.get_text(strip=True)

                # Consider static PDF links or dynamic JSP download wrapper links
                href_lower = href.lower()
                is_pdf_link = (
                    href_lower.endswith(".pdf")
                    or "download_file" in href_lower
                    or "jsp?num_id=" in href_lower
                )
                if not is_pdf_link:
                    continue

                # Check if the link text + URL looks ban-related
                combined = f"{text} {href}"
                if _is_ban_related(combined):
                    full_url = urljoin(config.CDSCO_BASE_URL, href)
                    all_links.append(full_url)

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for link in all_links:
            if link not in seen:
                seen.add(link)
                unique.append(link)

        logger.info(
            "[cdsco] Found %d ban-related PDF link(s) across all sub-pages.",
            len(unique),
        )
        return unique


# ---------------------------------------------------------------------------
# PIB Scraper
# ---------------------------------------------------------------------------


class PIBScraper(BaseScraper):
    """
    Scraper for the Press Information Bureau (PIB) archive.

    Searches for press releases related to drug bans and extracts any
    linked PDF notifications.
    """

    def __init__(self) -> None:
        super().__init__(source_name="pib")

    def discover_pdf_links(self) -> list[str]:
        """
        Search PIB for drug-ban related press releases and extract PDF links.

        Uses tighter search terms and validates link context against
        exclusion keywords to prevent downloading unrelated documents.
        """
        search_terms = [
            "banned drugs",
            "prohibited drugs",
            "FDC ban",
            "Section 26A",
        ]

        all_links: list[str] = []

        for term in search_terms:
            url = f"{config.PIB_SEARCH_URL}?field1={term.replace(' ', '+')}"
            resp = self._request_with_retries(url)
            if resp is None:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href: str = anchor["href"]
                if not href.lower().endswith(".pdf"):
                    continue
                # Get surrounding context text
                text = anchor.get_text(strip=True)
                parent_text = ""
                if anchor.parent:
                    parent_text = anchor.parent.get_text(strip=True)
                combined = f"{text} {parent_text} {href}"
                # Apply two-tier keyword check
                if _is_ban_related(combined):
                    full_url = urljoin(config.PIB_BASE_URL, href)
                    all_links.append(full_url)

        # Deduplicate
        seen: set[str] = set()
        unique: list[str] = []
        for link in all_links:
            if link not in seen:
                seen.add(link)
                unique.append(link)

        logger.info(
            "[pib] Found %d PDF link(s) across search results.", len(unique)
        )
        return unique


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def run_all_scrapers(dest_dir: Optional[Path] = None) -> list[Path]:
    """Run all configured scrapers and return combined list of new downloads."""
    downloaded: list[Path] = []

    for ScraperClass in (CDSCOScraper, PIBScraper):
        try:
            scraper = ScraperClass()
            downloaded.extend(scraper.run(dest_dir))
        except Exception as exc:
            logger.error(
                "Scraper %s failed: %s",
                ScraperClass.__name__,
                exc,
                exc_info=True,
            )

    return downloaded
