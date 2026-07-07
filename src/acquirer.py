"""
Acquisition module for the Indian Banned Medicines Data Pipeline.

Handles downloading PDF notifications from the CDSCO website based on the notification roadmap.
Implements robust retry logic, PDF magic-bytes verification, and populates tracking tables.
"""

from __future__ import annotations

import logging
import random
import re
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src import config
from src.database import DatabaseManager, NotificationProcessed
from src.html_scraper import parse_notification_field
from src.utils import is_valid_pdf, sanitize_filename, read_json_file, write_json_file, parse_date_with_fallback
from src.validators import normalize_notification_number

logger = logging.getLogger(__name__)


def retry_request(max_retries: int = 3, backoff_in_seconds: float = 2.0):
    """Decorator or helper for retrying requests with exponential backoff and jitter."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except (requests.RequestException, Exception) as exc:
                    retries += 1
                    if retries >= max_retries:
                        logger.error("Failed executing %s after %d retries: %s", func.__name__, max_retries, exc)
                        raise
                    sleep_time = (backoff_in_seconds * (2 ** (retries - 1))) + random.uniform(0, 1.0)
                    logger.warning("Error in %s. Retrying %d/%d in %.2f seconds... Error: %s", 
                                   func.__name__, retries, max_retries, sleep_time, exc)
                    time.sleep(sleep_time)
        return wrapper
    return decorator


class PDFAcquirer:
    """Acquires (discovers and downloads) gazette notification PDF files."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or DatabaseManager()
        self.session = requests.Session()
        self.url_cache: Dict[str, str] = {}
        self.crawled = False

    def _get_headers(self) -> dict[str, str]:
        return {
            "User-Agent": random.choice(config.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive"
        }

    @retry_request(max_retries=3)
    def _fetch_page(self, url: str) -> requests.Response:
        """Fetch a page with retries."""
        # Rate-limiting delay
        time.sleep(random.uniform(config.SCRAPE_DELAY_MIN, config.SCRAPE_DELAY_MAX))
        resp = self.session.get(url, headers=self._get_headers(), timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp

    def crawl_cdsco_sections(self) -> None:
        """Crawl major CDSCO sections to discover and build a mapping of notification_number -> PDF URL."""
        if self.crawled:
            return
            
        sub_pages = [
            config.CDSCO_NOTIFICATIONS_URL,
            urljoin(config.CDSCO_NOTIFICATIONS_URL, "Gazette-Notifications/"),
            urljoin(config.CDSCO_NOTIFICATIONS_URL, "Public-Notices/"),
            urljoin(config.CDSCO_NOTIFICATIONS_URL, "NSQ-Alerts/"),
            config.CDSCO_BANNED_URL
        ]
        
        logger.info("[acquirer] Crawling CDSCO pages to index PDF links...")
        
        for page_url in sub_pages:
            try:
                logger.info("[acquirer] Scanning: %s", page_url)
                resp = self._fetch_page(page_url)
                soup = BeautifulSoup(resp.text, "html.parser")
                
                for anchor in soup.find_all("a", href=True):
                    href = anchor["href"]
                    
                    # Match pdf/download links
                    href_lower = href.lower()
                    if href_lower.endswith(".pdf") or "download_file" in href_lower or "jsp?num_id=" in href_lower:
                        full_url = urljoin(config.CDSCO_BASE_URL, href)
                        
                        # Find context text from parent elements
                        tr = anchor.find_parent("tr")
                        li = anchor.find_parent("li")
                        if tr:
                            context_text = tr.get_text(" | ", strip=True)
                        elif li:
                            context_text = li.get_text(" | ", strip=True)
                        else:
                            context_text = f"{anchor.get_text(strip=True)} {anchor.get('title', '').strip()} {href}"
                        
                        # Try to parse notification details
                        notif_num, _ = parse_notification_field(context_text)
                        if notif_num:
                            norm_num = normalize_notification_number(notif_num)
                            if norm_num not in self.url_cache:
                                self.url_cache[norm_num] = full_url
                                logger.info("[acquirer] Cached URL for %s: %s", norm_num, full_url)
            except Exception as exc:
                logger.warning("[acquirer] Failed to scan page %s: %s", page_url, exc)
                
        self.crawled = True
        logger.info("[acquirer] Crawling complete. Indexed %d notification PDF links.", len(self.url_cache))

    @retry_request(max_retries=3)
    def _download_file(self, url: str, dest_path: Path) -> None:
        """Download file from URL to dest_path with retries."""
        time.sleep(random.uniform(config.SCRAPE_DELAY_MIN, config.SCRAPE_DELAY_MAX))
        with self.session.get(url, headers=self._get_headers(), stream=True, timeout=config.REQUEST_TIMEOUT) as r:
            r.raise_for_status()
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

    def acquire_notification(self, notif: dict, force: bool = False) -> str:
        """
        Download PDF for a single notification roadmap entry.
        Returns the path to the downloaded file, or raises an exception.
        """
        notif_num = normalize_notification_number(notif["notification_number"])
        date_str = notif.get("notification_date")
        
        # Determine target filename
        sanitized_num = sanitize_filename(notif_num)
        filename = f"{sanitized_num}_{date_str}.pdf" if date_str else f"{sanitized_num}.pdf"
        dest_path = config.DOWNLOAD_DIR / filename
        
        # Check DB status
        with self.db.session_scope() as session:
            existing = session.query(NotificationProcessed).filter_by(notification_number=notif_num).first()
            if existing and existing.download_status == "downloaded" and dest_path.exists() and is_valid_pdf(dest_path) and not force:
                logger.info("[acquirer] PDF already downloaded for: %s", notif_num)
                return str(dest_path)

        # Find URL
        url = notif.get("source_url")
        if not url:
            # Check crawl cache
            url = self.url_cache.get(notif_num)
            
        if not url:
            # If not in cache, log as missing and raise
            msg = f"No PDF URL found for notification {notif_num}"
            with self.db.session_scope() as session:
                self.db.upsert_notification_processing(session, {
                    "notification_number": notif_num,
                    "notification_date": parse_date_with_fallback(date_str),
                    "download_status": "missing",
                    "error_message": msg
                })
                self.db.add_to_review_queue(session, {
                    "notification_number": notif_num,
                    "notification_date": parse_date_with_fallback(date_str),
                    "issue_type": "missing_pdf",
                    "description": msg
                })
            raise FileNotFoundError(msg)

        logger.info("[acquirer] Downloading PDF for %s from %s", notif_num, url)
        
        try:
            self._download_file(url, dest_path)
            
            # Verify PDF magic bytes
            if not is_valid_pdf(dest_path):
                dest_path.unlink(missing_ok=True)
                raise ValueError("Downloaded file is not a valid PDF (invalid magic bytes)")
                
            # Success - update DB
            with self.db.session_scope() as session:
                self.db.upsert_notification_processing(session, {
                    "notification_number": notif_num,
                    "notification_date": parse_date_with_fallback(date_str),
                    "source_url": url,
                    "pdf_path": str(dest_path),
                    "download_status": "downloaded",
                    "error_message": None
                })
            logger.info("[acquirer] Downloaded and verified PDF: %s", dest_path)
            return str(dest_path)
            
        except Exception as exc:
            error_msg = f"Failed to download/verify PDF: {exc}"
            with self.db.session_scope() as session:
                self.db.upsert_notification_processing(session, {
                    "notification_number": notif_num,
                    "notification_date": parse_date_with_fallback(date_str),
                    "source_url": url,
                    "download_status": "failed",
                    "error_message": error_msg
                })
                self.db.add_to_review_queue(session, {
                    "notification_number": notif_num,
                    "notification_date": parse_date_with_fallback(date_str),
                    "source_url": url,
                    "issue_type": "corrupted_pdf" if "valid PDF" in str(exc) else "download_failed",
                    "description": error_msg
                })
            raise

    def acquire_all(self, limit: Optional[int] = None, force: bool = False) -> None:
        """Download PDFs for all notifications in the roadmap."""
        roadmap_file = config.DATA_DIR / "notification_roadmap.json"
        roadmap = read_json_file(roadmap_file)
        if not roadmap:
            logger.error("[acquirer] No notification roadmap found. Run discovery first.")
            return

        # Crawl to index URLs
        self.crawl_cdsco_sections()

        count = 0
        success_count = 0
        failed_count = 0
        missing_count = 0

        for notif in roadmap:
            if limit and success_count >= limit:
                break
                
            try:
                self.acquire_notification(notif, force=force)
                success_count += 1
            except FileNotFoundError as e:
                logger.warning("[acquirer] FileNotFoundError for %s: %s", notif.get("notification_number"), e)
                missing_count += 1
            except Exception as e:
                logger.error("[acquirer] Exception for %s: %s", notif.get("notification_number"), e, exc_info=True)
                failed_count += 1
                
            count += 1

        logger.info("[acquirer] PDF Acquisition summary: Total attempted: %d, Success: %d, Failed: %d, Missing URL: %d", 
                    count, success_count, failed_count, missing_count)


if __name__ == "__main__":
    from src.utils import logging_setup
    logging_setup()
    acquirer = PDFAcquirer()
    acquirer.acquire_all(limit=10)
