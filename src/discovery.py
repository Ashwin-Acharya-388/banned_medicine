"""
Discovery module for the Indian Banned Medicines Data Pipeline.

Scrapes compiled lists and official portals to build a comprehensive notification roadmap,
saving the compiled results to data/notification_roadmap.json.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from src import config
from src.html_scraper import parse_notification_field, run_all_html_scrapers
from src.utils import parse_date_with_fallback, write_json_file, find_pdf_links
from src.validators import normalize_notification_number

logger = logging.getLogger(__name__)


class NotificationDiscoverer:
    """Discovers ban notifications from unofficial aggregates and official CDSCO list page."""

    def __init__(self) -> None:
        self.session = requests.Session()

    def _get_headers(self) -> dict[str, str]:
        import random
        return {
            "User-Agent": random.choice(config.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def scrape_cdsco_banned_page(self) -> list[dict]:
        """Scrape the CDSCO consumer List-Of-Banned-Drugs page for direct PDF links."""
        url = config.CDSCO_BANNED_URL
        logger.info("[discovery] Scraping CDSCO banned page: %s", url)
        
        discovered_notifications = []
        try:
            resp = self.session.get(url, headers=self._get_headers(), timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("[discovery] Failed to fetch CDSCO banned list page: %s", exc)
            return discovered_notifications

        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Look for PDF links in lists or tables
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            
            # Check if this is a PDF
            if href.lower().endswith(".pdf") or "download_file" in href.lower() or "jsp?num_id=" in href.lower():
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
                
                notif_num, notif_date = parse_notification_field(context_text)
                
                if notif_num:
                    discovered_notifications.append({
                        "notification_number": notif_num,
                        "notification_date": notif_date.isoformat() if notif_date else None,
                        "source_url": full_url,
                        "description": text or title,
                        "source": "cdsco_official"
                    })
                    logger.info("[discovery] Found official notification link: %s -> %s", notif_num, full_url)

        return discovered_notifications

    def discover(self) -> list[dict]:
        """Run discovery end-to-end and build the notification roadmap."""
        roadmap: dict[str, dict] = {}

        # 1. Scrape CDSCO Official Page PDF Links
        cdsco_officials = self.scrape_cdsco_banned_page()
        for item in cdsco_officials:
            notif_num = item["notification_number"]
            roadmap[notif_num] = {
                "notification_number": notif_num,
                "notification_date": item["notification_date"],
                "source_url": item["source_url"],
                "drugs": [],
                "sources": [item["source"]]
            }

        # 2. Scrape Aggregators (Vaayath & TheHealthMaster)
        logger.info("[discovery] Scraping HTML aggregators...")
        aggregator_entries = run_all_html_scrapers()
        
        for entry in aggregator_entries:
            notif_num = entry.notification_number
            if not notif_num:
                continue
                
            notif_num = normalize_notification_number(notif_num)
            notif_date_str = entry.notification_date.isoformat() if entry.notification_date else None
            
            if notif_num not in roadmap:
                roadmap[notif_num] = {
                    "notification_number": notif_num,
                    "notification_date": notif_date_str,
                    "source_url": None,
                    "drugs": [],
                    "sources": []
                }
            
            # Merge dates if missing
            if not roadmap[notif_num]["notification_date"] and notif_date_str:
                roadmap[notif_num]["notification_date"] = notif_date_str
                
            # Merge source
            source = entry.source_pdf
            if source not in roadmap[notif_num]["sources"]:
                roadmap[notif_num]["sources"].append(source)
                
            # Clean and add drug if not duplicate
            if entry.generic_name:
                cleaned_drug = entry.generic_name.strip()
                if cleaned_drug and cleaned_drug not in roadmap[notif_num]["drugs"]:
                    roadmap[notif_num]["drugs"].append(cleaned_drug)

        # Convert roadmap dict to sorted list of notifications
        sorted_roadmap = []
        for key in sorted(roadmap.keys()):
            sorted_roadmap.append(roadmap[key])

        # Write roadmap to config.DATA_DIR / "notification_roadmap.json"
        roadmap_file = config.DATA_DIR / "notification_roadmap.json"
        write_json_file(roadmap_file, sorted_roadmap)
        logger.info("[discovery] Roadmap written with %d notifications to %s", len(sorted_roadmap), roadmap_file)
        
        return sorted_roadmap


if __name__ == "__main__":
    from src.utils import logging_setup
    logging_setup()
    discoverer = NotificationDiscoverer()
    discoverer.discover()
