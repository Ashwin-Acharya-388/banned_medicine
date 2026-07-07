"""
HTML scraping module for the Indian Banned Medicines Data Pipeline.

Scrapes compiled/aggregator lists of banned medicines from unofficial sources
(e.g., Vaayath, TheHealthMaster) and extracts structured entries.
"""

from __future__ import annotations

import logging
import re
import time
import random
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src import config
from src.pdf_parser import DATE_PATTERNS
from src.validators import (
    BannedMedicineEntry,
    normalize_notification_number,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper function for parsing composite notification fields
# ---------------------------------------------------------------------------

def parse_notification_field(field_text: str) -> tuple[Optional[str], Optional[date]]:
    """
    Extract GSR/SO number and date from a combined notification string.

    E.g., "GSR NO. 578(E) Dated 23.07.1983" -> ("G.S.R. 578(E)", date(1983, 7, 23))
    """
    if not field_text:
        return None, None

    # Replace non-breaking spaces and standardise whitespace
    text = re.sub(r"\s+", " ", field_text.replace("\xa0", " ")).strip()

    # Extract GSR / SO number using simpler regex group matching
    gsr_match = re.search(
        r"G\.?\s*S\.?\s*R\.?\s*(?:NO\.?)?\s*(\d+)\s*\(?\s*E\s*\)?",
        text,
        re.IGNORECASE,
    )
    notif_number = None
    if gsr_match:
        notif_number = normalize_notification_number(f"G.S.R. {gsr_match.group(1)}(E)")
    else:
        so_match = re.search(
            r"S\.?\s*O\.?\s*(?:NO\.?)?\s*(\d+)\s*\(?\s*E\s*\)?",
            text,
            re.IGNORECASE,
        )
        if so_match:
            notif_number = normalize_notification_number(f"S.O. {so_match.group(1)}(E)")

    # Extract Date
    notif_date = None
    for pattern, date_format in DATE_PATTERNS:
        d_match = pattern.search(text)
        if d_match:
            try:
                if date_format == "%d/%m/%Y":
                    day, month, year = int(d_match.group(1)), int(d_match.group(2)), int(d_match.group(3))
                    notif_date = date(year, month, day)
                elif date_format == "%d.%m.%Y":
                    day, month, year = int(d_match.group(1)), int(d_match.group(2)), int(d_match.group(3))
                    notif_date = date(year, month, day)
                elif date_format == "%B %d %Y":
                    m_str, d_str, y_str = d_match.group(1), d_match.group(2), d_match.group(3)
                    parsed_dt = datetime.strptime(f"{m_str} {d_str} {y_str}", "%B %d %Y")
                    notif_date = parsed_dt.date()
                elif date_format == "%d %B %Y":
                    d_str, m_str, y_str = d_match.group(1), d_match.group(2), d_match.group(3)
                    parsed_dt = datetime.strptime(f"{d_str} {m_str} {y_str}", "%d %B %Y")
                    notif_date = parsed_dt.date()
                break
            except (ValueError, IndexError):
                continue

    return notif_number, notif_date


# ---------------------------------------------------------------------------
# Base HTML Scraper
# ---------------------------------------------------------------------------

class BaseHTMLScraper:
    """Base class for HTML-based web scrapers."""

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name
        self.session = requests.Session()

    def _get_random_headers(self) -> dict[str, str]:
        return {
            "User-Agent": random.choice(config.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        }

    def _request_with_retries(self, url: str) -> Optional[requests.Response]:
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                # Random delay to respect robots/ethical scraping limits
                delay = random.uniform(config.SCRAPE_DELAY_MIN, config.SCRAPE_DELAY_MAX)
                time.sleep(delay)

                headers = self._get_random_headers()
                resp = self.session.get(
                    url,
                    headers=headers,
                    timeout=config.REQUEST_TIMEOUT,
                    allow_redirects=True,
                )
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                wait = 2**attempt + random.uniform(0, 1)
                logger.warning(
                    "[%s] HTTP attempt %d/%d failed for %s: %s — retrying in %.1f s",
                    self.source_name, attempt, config.MAX_RETRIES, url, exc, wait
                )
                time.sleep(wait)
        logger.error("[%s] Failed to fetch %s after %d retries.", self.source_name, url, config.MAX_RETRIES)
        return None

    def scrape(self) -> list[BannedMedicineEntry]:
        """Scrape the source page and return a list of parsed medicine entries."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Vaayath.com Scraper
# ---------------------------------------------------------------------------

class VaayathScraper(BaseHTMLScraper):
    """Scraper for Vaayath.com compiled banned drugs list."""

    def __init__(self) -> None:
        super().__init__(source_name="vaayath")

    def scrape(self) -> list[BannedMedicineEntry]:
        url = config.VAAYATH_URL
        logger.info("[vaayath] Starting HTML scrape of %s", url)

        resp = self._request_with_retries(url)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            logger.error("[vaayath] No table found on the page.")
            return []

        entries: list[BannedMedicineEntry] = []
        rows = table.find_all("tr")
        logger.info("[vaayath] Found %d rows in the main table.", len(rows))

        for row in rows:
            cells = row.find_all(["td", "th"])
            if not cells or len(cells) < 3:
                continue

            # Skip header row
            if "sr" in cells[0].get_text(strip=True).lower() or "drugs name" in cells[1].get_text(strip=True).lower():
                continue

            sr_no = cells[0].get_text(strip=True)
            drug_name = cells[1].get_text(strip=True)
            notif_raw = cells[2].get_text(strip=True)

            if not drug_name:
                continue

            # Clean name (remove trailing dots, etc.)
            clean_name = drug_name.strip().rstrip(".")

            notif_number, notif_date = parse_notification_field(notif_raw)

            entry = BannedMedicineEntry(
                generic_name=clean_name,
                notification_number=notif_number,
                notification_date=notif_date,
                source_pdf=urlparse(url).netloc,  # source identifier
                parsing_status="ok",
                raw_text=f"Sr: {sr_no} | Drug: {drug_name} | Notification: {notif_raw}",
            )
            entries.append(entry)

        logger.info("[vaayath] Extracted %d entries from HTML table.", len(entries))
        return entries


# ---------------------------------------------------------------------------
# TheHealthMaster.com Scraper
# ---------------------------------------------------------------------------

class TheHealthMasterScraper(BaseHTMLScraper):
    """Scraper for TheHealthMaster.com compiled banned drugs list."""

    def __init__(self) -> None:
        super().__init__(source_name="thehealthmaster")

    def scrape(self) -> list[BannedMedicineEntry]:
        url = config.HEALTHMASTER_URL
        logger.info("[thehealthmaster] Starting HTML scrape of %s", url)

        resp = self._request_with_retries(url)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        tables = soup.find_all("table")
        if not tables:
            logger.error("[thehealthmaster] No tables found on the page.")
            return []

        logger.info("[thehealthmaster] Found %d tables to parse.", len(tables))
        entries: list[BannedMedicineEntry] = []

        for t_idx, table in enumerate(tables):
            rows = table.find_all("tr")
            for r_idx, row in enumerate(rows):
                cells = row.find_all(["td", "th"])
                if not cells or len(cells) < 3:
                    continue

                # Skip header row
                c0_text = cells[0].get_text(strip=True).lower()
                c1_text = cells[1].get_text(strip=True).lower()
                if "sr" in c0_text or "drugs name" in c1_text or "drug name" in c1_text:
                    continue

                sr_no = cells[0].get_text(strip=True)
                drug_name = cells[1].get_text(strip=True)
                notif_raw = cells[2].get_text(strip=True)

                if not drug_name:
                    continue

                # Clean name
                clean_name = drug_name.strip().rstrip(".")

                notif_number, notif_date = parse_notification_field(notif_raw)

                entry = BannedMedicineEntry(
                    generic_name=clean_name,
                    notification_number=notif_number,
                    notification_date=notif_date,
                    source_pdf=urlparse(url).netloc,  # source identifier
                    parsing_status="ok",
                    raw_text=f"Table: {t_idx} Row: {r_idx} | Sr: {sr_no} | Drug: {drug_name} | Notification: {notif_raw}",
                )
                entries.append(entry)

        logger.info("[thehealthmaster] Extracted %d entries across all tables.", len(entries))
        return entries


# ---------------------------------------------------------------------------
# Aggregator Orchestration
# ---------------------------------------------------------------------------

def run_all_html_scrapers() -> list[BannedMedicineEntry]:
    """Run all HTML aggregators and return combined entries."""
    all_entries: list[BannedMedicineEntry] = []

    for ScraperClass in (VaayathScraper, TheHealthMasterScraper):
        try:
            scraper = ScraperClass()
            entries = scraper.scrape()
            all_entries.extend(entries)
        except Exception as exc:
            logger.error(
                "HTML Scraper %s failed: %s",
                ScraperClass.__name__,
                exc,
                exc_info=True,
            )

    return all_entries
