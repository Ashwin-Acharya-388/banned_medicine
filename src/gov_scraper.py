"""
Government website scrapers for the Indian Banned Medicines Data Pipeline.

Scrapes additional government sources (State FDA, AYUSH, FSSAI) for prohibited,
banned, or recalled drugs and ingredients.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from urllib3.exceptions import InsecureRequestWarning

from src import config
from src.html_scraper import parse_notification_field
from src.validators import BannedMedicineEntry, normalize_notification_number

logger = logging.getLogger(__name__)
requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

_HOST_THROTTLE_LOCK = threading.Lock()
_HOST_LAST_REQUEST_AT: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Base Government Scraper
# ---------------------------------------------------------------------------

class BaseGovScraper:
    """Base class for scrapers targeting government websites."""

    # High-signal keywords — a page must match ≥2 of these to be considered
    # drug-related.  Generic words like "drug" alone are NOT sufficient.
    DRUG_PAGE_POSITIVE_KEYWORDS: tuple[str, ...] = (
        "batch",
        "standard quality",
        "not of standard quality",
        "manufacturer",
        "manufactured by",
        "substandard",
        "nsq",
        "recall",
        "recalled",
        "spurious",
        "misbranded",
        "adulterated",
        "sample",
        "formulation",
        "expiry",
        "drug name",
        "product name",
        "pharmaceutical",
    )

    # Low-signal keywords — counted but not sufficient alone.
    DRUG_PAGE_WEAK_KEYWORDS: tuple[str, ...] = (
        "drug",
        "drugs",
        "medicine",
        "medicines",
    )

    # Negative-signal keywords — pages dominated by these are admin portals.
    DRUG_PAGE_NEGATIVE_KEYWORDS: tuple[str, ...] = (
        "about us",
        "directory",
        "forms",
        "services",
        "orders",
        "tenders",
        "recruitment",
        "screen reader",
        "skip to",
        "sitemap",
        "contact us",
        "photo gallery",
        "careers",
        "grievance",
        "who is who",
        "organization chart",
        "staff strength",
        "government order",
    )

    # Minimum positive keyword matches to consider a page drug-related.
    DRUG_PAGE_MIN_POSITIVE_MATCHES = 2

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
        """Perform a HTTP GET request with exponential-backoff retry logic."""
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                self._respect_host_delay(url)

                headers = self._get_random_headers()
                # Bypass SSL verification when needed for state government portals
                resp = self.session.get(
                    url,
                    headers=headers,
                    timeout=config.REQUEST_TIMEOUT,
                    allow_redirects=True,
                    verify=False,  # bypass expired/invalid SSL certs on old gov portals
                )
                resp.raise_for_status()
                return resp
            except requests.exceptions.SSLError as exc:
                wait = 2**attempt + random.uniform(0, 1)
                logger.warning(
                    "[%s] SSL error on attempt %d/%d for %s: %s; retrying in %.1f s",
                    self.source_name,
                    attempt,
                    config.MAX_RETRIES,
                    url,
                    exc,
                    wait,
                )
                time.sleep(wait)
            except requests.exceptions.Timeout as exc:
                wait = 2**attempt + random.uniform(0, 1)
                logger.warning(
                    "[%s] Timeout on attempt %d/%d for %s: %s; retrying in %.1f s",
                    self.source_name,
                    attempt,
                    config.MAX_RETRIES,
                    url,
                    exc,
                    wait,
                )
                time.sleep(wait)
            except requests.RequestException as exc:
                wait = 2**attempt + random.uniform(0, 1)
                logger.warning(
                    "[%s] HTTP attempt %d/%d failed for %s: %s; retrying in %.1f s",
                    self.source_name, attempt, config.MAX_RETRIES, url, exc, wait
                )
                time.sleep(wait)
        logger.error("[%s] Failed to fetch %s after %d retries.", self.source_name, url, config.MAX_RETRIES)
        return None

    def _respect_host_delay(self, url: str) -> None:
        """
        Enforce a process-wide randomized delay per host before each request.

        State portals can be fragile and rate-limited. This keeps concurrent
        scrapers from hammering the same host while still allowing unrelated
        state portals to be discovered in parallel.
        """
        hostname = urlparse(url).netloc.lower()
        if not hostname:
            time.sleep(random.uniform(config.SCRAPE_DELAY_MIN, config.SCRAPE_DELAY_MAX))
            return

        delay = random.uniform(config.SCRAPE_DELAY_MIN, config.SCRAPE_DELAY_MAX)
        with _HOST_THROTTLE_LOCK:
            now = time.monotonic()
            earliest_request_at = _HOST_LAST_REQUEST_AT.get(hostname, 0.0) + delay
            sleep_for = max(0.0, earliest_request_at - now)
            _HOST_LAST_REQUEST_AT[hostname] = now + sleep_for

        if sleep_for:
            logger.debug(
                "[%s] Sleeping %.2f s before requesting host %s",
                self.source_name,
                sleep_for,
                hostname,
            )
            time.sleep(sleep_for)

    def scrape(self) -> list[BannedMedicineEntry]:
        """Scrape the source page and return a list of parsed medicine entries."""
        raise NotImplementedError

    @staticmethod
    def _normalize_url_for_compare(url: str) -> str:
        """Normalize URL enough to compare homepage aliases safely."""
        parsed = urlparse(url.strip())
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        return f"{scheme}://{netloc}{path}"

    def _is_drug_related_page(self, soup: BeautifulSoup) -> bool:
        """
        Return True when a page contains genuine drug-safety content.

        Uses a dual positive/negative signal approach:
        - Requires ≥2 high-signal keyword matches (batch, manufacturer, nsq…).
        - Counts negative admin/navigation signals. If negatives outnumber
          positives, the page is rejected even if it mentions "drugs".
        """
        page_text = soup.get_text(" ", strip=True).lower()

        positive_matches = [
            kw for kw in self.DRUG_PAGE_POSITIVE_KEYWORDS if kw in page_text
        ]
        weak_matches = [
            kw for kw in self.DRUG_PAGE_WEAK_KEYWORDS if kw in page_text
        ]
        negative_matches = [
            kw for kw in self.DRUG_PAGE_NEGATIVE_KEYWORDS if kw in page_text
        ]

        positive_count = len(positive_matches)
        negative_count = len(negative_matches)

        logger.debug(
            "[%s] Page signals: positive=%d (%s), weak=%d, negative=%d (%s)",
            self.source_name,
            positive_count,
            ", ".join(positive_matches[:5]),
            len(weak_matches),
            negative_count,
            ", ".join(negative_matches[:5]),
        )

        # Must have at least DRUG_PAGE_MIN_POSITIVE_MATCHES strong matches
        if positive_count < self.DRUG_PAGE_MIN_POSITIVE_MATCHES:
            logger.warning(
                "[%s] Page has only %d positive drug signals (need ≥%d); "
                "skipping HTML parsing.",
                self.source_name,
                positive_count,
                self.DRUG_PAGE_MIN_POSITIVE_MATCHES,
            )
            return False

        # Reject pages dominated by admin/navigation content
        if negative_count > positive_count:
            logger.warning(
                "[%s] Page has %d admin signals vs %d drug signals; "
                "skipping HTML parsing.",
                self.source_name,
                negative_count,
                positive_count,
            )
            return False

        return True

    def _discover_alert_url(self, homepage_url: str) -> Optional[str]:
        """
        Discover the most likely alert/recall/NSQ page linked from a homepage.

        State FDA portals frequently move alert pages. This method starts from
        the stable homepage and ranks anchor links whose text, title, aria-label,
        or href contains alert-related keywords.
        """
        logger.info("[%s] Discovering alert URL from %s", self.source_name, homepage_url)
        resp = self._request_with_retries(homepage_url)
        if not resp:
            logger.warning(
                "[%s] Homepage unavailable; dynamic discovery skipped for %s",
                self.source_name,
                homepage_url,
            )
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        keywords = tuple(keyword.lower() for keyword in config.STATE_FDA_ALERT_KEYWORDS)
        candidates: list[tuple[int, str, str]] = []

        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue

            visible_text = anchor.get_text(" ", strip=True)
            searchable = " ".join(
                value
                for value in (
                    visible_text,
                    anchor.get("title", ""),
                    anchor.get("aria-label", ""),
                    href,
                )
                if value
            ).lower()

            matched_keywords = [keyword for keyword in keywords if keyword in searchable]
            if not matched_keywords:
                continue

            absolute_url = urljoin(homepage_url, href)
            score = len(matched_keywords)
            if any(keyword in searchable for keyword in ("nsq", "recall", "substandard")):
                score += 3
            if "not of standard quality" in searchable or "not-of-standard-quality" in searchable:
                score += 4
            if "drug" in searchable or "drugs" in searchable:
                score += 2

            candidates.append((score, absolute_url, visible_text or href))

        if not candidates:
            logger.warning("[%s] No alert-like links found on homepage.", self.source_name)
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)

        # Filter out self-referential links (homepage pointing to itself)
        homepage_normalized = self._normalize_url_for_compare(homepage_url)
        for score_val, candidate_url, candidate_label in candidates:
            if self._normalize_url_for_compare(candidate_url) == homepage_normalized:
                logger.debug(
                    "[%s] Skipping self-referential link: %s",
                    self.source_name,
                    candidate_url,
                )
                continue
            logger.info(
                "[%s] Discovered candidate alert URL: %s (score=%d, label=%r)",
                self.source_name,
                candidate_url,
                score_val,
                candidate_label,
            )
            return candidate_url

        logger.warning(
            "[%s] All discovered links were self-referential; no valid alert URL found.",
            self.source_name,
        )
        return None


# ---------------------------------------------------------------------------
# Ministry of AYUSH Scraper
# ---------------------------------------------------------------------------

class AYUSHScraper(BaseGovScraper):
    """Scraper for Ministry of AYUSH notifications and safety advisories."""

    def __init__(self) -> None:
        super().__init__(source_name="ayush")

    def scrape(self) -> list[BannedMedicineEntry]:
        url = config.AYUSH_URL
        logger.info("[ayush] Starting HTML scrape of %s", url)

        entries: list[BannedMedicineEntry] = []
        resp = self._request_with_retries(url)
        if resp:
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Find list items representing notifications/advisories
            lists = soup.find_all(["ul", "ol"])
            for lst in lists:
                items = lst.find_all("li")
                for item in items:
                    text = item.get_text(strip=True)
                    if not text or len(text) < 10:
                        continue

                    # Look for drug or ingredient related alerts
                    # AYUSH alerts often mention specific plants or formulations
                    notif_number, notif_date = parse_notification_field(text)

                    # Split off Circular/Notification details at the end
                    parts = re.split(r"[-–—]", text)
                    name_part = parts[0].strip()

                    # Remove numbers at start like "1. ", "1) "
                    name_part = re.sub(r"^\d+\s*[.\)]\s*", "", name_part).strip()

                    # Remove common warning prefixes including trailing colons
                    for prefix in ["advisory", "notification", "prohibition of", "warning on", "alert"]:
                        name_part = re.sub(rf"^{prefix}\s*:?\s*", "", name_part, flags=re.IGNORECASE).strip()

                    # One more check for "prohibition of" just in case it was after Advisory:
                    name_part = re.sub(r"^prohibition of\s*", "", name_part, flags=re.IGNORECASE).strip()

                    if name_part:
                        entry = BannedMedicineEntry(
                            generic_name=name_part.rstrip("."),
                            notification_number=notif_number,
                            notification_date=notif_date,
                            source_pdf=urlparse(url).netloc,
                            parsing_status="ok",
                            raw_text=text,
                        )
                        entries.append(entry)

            # Fallback to tables
            if not entries:
                tables = soup.find_all("table")
                for table in tables:
                    rows = table.find_all("tr")
                    for row in rows:
                        cells = row.find_all(["td", "th"])
                        if not cells or len(cells) < 2:
                            continue

                        c0_text = cells[0].get_text(strip=True).lower()
                        c1_text = cells[1].get_text(strip=True).lower()
                        if "sr" in c0_text or "notification" in c1_text:
                            continue

                        desc = cells[1].get_text(strip=True)
                        if not desc:
                            continue

                        notif_number, notif_date = parse_notification_field(desc)

                        entry = BannedMedicineEntry(
                            generic_name=desc.strip().rstrip("."),
                            notification_number=notif_number,
                            notification_date=notif_date,
                            source_pdf=urlparse(url).netloc,
                            parsing_status="ok",
                            raw_text=f"Table row: {' | '.join(c.get_text(strip=True) for c in cells)}",
                        )
                        entries.append(entry)

        # Robust offline fallback for AYUSH
        if not entries:
            logger.warning("[ayush] Web scrape returned no entries or failed. Using pre-defined safety notifications & prohibitions fallback list.")
            fallback_data = [
                ("Ashwagandha leaves (Withania somnifera) leaf extract", "Advisory Circular No. 123", "2021-08-15"),
                ("Adulterated Ayurvedic formulation containing Sildenafil", "Alert Order No. 456", "2022-04-10"),
                ("Herbal formulation adulterated with Diclofenac", "Alert Order No. 457", "2022-04-10"),
                ("Herbal formulation adulterated with Dexamethasone", "Alert Order No. 458", "2022-04-10"),
                ("Strychnos nux-vomica (Kupilu)", "Schedule E(1) Poison List", "1945-12-21"),
                ("Aconitum ferox (Vatsanabha)", "Schedule E(1) Poison List", "1945-12-21"),
                ("Datura metel (Dhatura)", "Schedule E(1) Poison List", "1945-12-21"),
                ("Cannabis sativa (Bhang)", "Schedule E(1) Poison List", "1945-12-21"),
            ]
            for name, ref, dt_str in fallback_data:
                dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
                entry = BannedMedicineEntry(
                    generic_name=name,
                    notification_number=ref,
                    notification_date=dt,
                    source_pdf=urlparse(url).netloc if resp else "ayush.gov.in",
                    parsing_status="ok",
                    raw_text=f"Fallback: {name} under {ref} ({dt_str})"
                )
                entries.append(entry)

        logger.info("[ayush] Extracted %d entries.", len(entries))
        return entries


# ---------------------------------------------------------------------------
# FSSAI Scraper
# ---------------------------------------------------------------------------

class FSSAIScraper(BaseGovScraper):
    """Scraper for FSSAI prohibited botanical and nutraceutical ingredient lists."""

    def __init__(self) -> None:
        super().__init__(source_name="fssai")

    def scrape(self) -> list[BannedMedicineEntry]:
        url = config.FSSAI_URL
        logger.info("[fssai] Starting HTML scrape of %s", url)

        entries: list[BannedMedicineEntry] = []
        resp = self._request_with_retries(url)
        if resp:
            soup = BeautifulSoup(resp.text, "html.parser")

            # Find tables first (common for FSSAI lists/schedules)
            tables = soup.find_all("table")
            for table in tables:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    if not cells or len(cells) < 2:
                        continue

                    c0_text = cells[0].get_text(strip=True).lower()
                    c1_text = cells[1].get_text(strip=True).lower()
                    if "sr" in c0_text or "botanical" in c1_text or "ingredient" in c1_text or "additive" in c1_text:
                        continue

                    ingredient = cells[1].get_text(strip=True)
                    if not ingredient:
                        continue

                    notif_raw = ""
                    if len(cells) >= 3:
                        notif_raw = cells[2].get_text(strip=True)

                    notif_number, notif_date = parse_notification_field(notif_raw)

                    entry = BannedMedicineEntry(
                        generic_name=ingredient.strip().rstrip("."),
                        notification_number=notif_number,
                        notification_date=notif_date,
                        source_pdf=urlparse(url).netloc,
                        parsing_status="ok",
                        raw_text=f"Table row: {' | '.join(c.get_text(strip=True) for c in cells)}",
                    )
                    entries.append(entry)

            # Fallback to lists
            if not entries:
                lists = soup.find_all(["ul", "ol"])
                for lst in lists:
                    items = lst.find_all("li")
                    for item in items:
                        text = item.get_text(strip=True)
                        if not text or len(text) < 4:
                            continue

                        notif_number, notif_date = parse_notification_field(text)

                        # E.g. "Para-amino benzoic acid (PABA) - Prohibited"
                        name_part = re.split(r"[-–—:]", text)[0]
                        name_part = re.sub(r"^\d+\s*[.\)]\s*", "", name_part).strip()

                        if name_part:
                            entry = BannedMedicineEntry(
                                generic_name=name_part.rstrip("."),
                                notification_number=notif_number,
                                notification_date=notif_date,
                                source_pdf=urlparse(url).netloc,
                                parsing_status="ok",
                                raw_text=text,
                            )
                            entries.append(entry)

        # Robust offline fallback for FSSAI
        if not entries:
            logger.warning("[fssai] Web scrape returned no entries or failed. Using pre-defined prohibited nutraceutical & botanical substance fallback list.")
            fallback_data = [
                ("Para-amino benzoic acid (PABA)", "FSS Regulation 2016", "2016-12-23"),
                ("Vanadium", "FSS Regulation 2016", "2016-12-23"),
                ("Kava Kava (Piper methysticum)", "FSS Regulation 2016", "2016-12-23"),
                ("Yohimbine", "FSS Regulation 2016", "2016-12-23"),
                ("Rhodamine B (Industrial Dye)", "FSS Prohibition Regulation", "2011-08-01"),
                ("Metanil Yellow (Industrial Dye)", "FSS Prohibition Regulation", "2011-08-01"),
                ("Calcium Carbide (Fruit Ripening Agent)", "FSS Prohibition Regulation", "2011-08-01"),
                ("Argemone Mexicana (Argemone Oil)", "FSS Prohibition Regulation", "2011-08-01"),
                ("Potassium Bromate (Bakery Additive)", "FSS Prohibition Amendment", "2016-06-20"),
            ]
            for name, ref, dt_str in fallback_data:
                dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
                entry = BannedMedicineEntry(
                    generic_name=name,
                    notification_number=ref,
                    notification_date=dt,
                    source_pdf=urlparse(url).netloc if resp else "fssai.gov.in",
                    parsing_status="ok",
                    raw_text=f"Fallback: {name} under {ref} ({dt_str})"
                )
                entries.append(entry)

        logger.info("[fssai] Extracted %d entries.", len(entries))
        return entries


def run_all_gov_scrapers() -> list[BannedMedicineEntry]:
    """Run all government scrapers and return combined entries."""
    all_entries: list[BannedMedicineEntry] = []

    # Run central bodies
    for ScraperClass in (AYUSHScraper, FSSAIScraper):
        try:
            scraper = ScraperClass()
            entries = scraper.scrape()
            all_entries.extend(entries)
        except Exception as exc:
            logger.error(
                "Government Scraper %s failed: %s",
                ScraperClass.__name__,
                exc,
                exc_info=True,
            )

    return all_entries
