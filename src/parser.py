"""
Notification Parser module for the Indian Banned Medicines Data Pipeline.

Extends the base PDF/text parser to handle FDC (Fixed Dose Combination) ingredients splitting,
validates entries, and handles scanned/unstructured text by routing them to the database
manual review queue.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional

from src import config
from src.database import DatabaseManager
from src.pdf_parser import BanNotificationParser
from src.validators import BannedMedicineEntry, normalize_notification_number

logger = logging.getLogger(__name__)


class NotificationParser:
    """Orchestrates PDF parsing, FDC splitting, and manual review routing."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or DatabaseManager()
        self.base_parser = BanNotificationParser()

    def split_fdc_ingredients(self, generic_name: str) -> tuple[bool, List[str]]:
        """
        Split a generic name into separate ingredients if it is an FDC.
        Returns (is_fdc, list_of_ingredients).
        """
        if not generic_name:
            return False, []

        # Regex for splitting: +, &, and (case-insensitive)
        split_pattern = re.compile(r"\s*\+\s*|\s*&\s*|\s+and\s+", re.IGNORECASE)
        
        # Check if the name looks like an FDC
        if split_pattern.search(generic_name):
            # Split ingredients
            raw_ingredients = split_pattern.split(generic_name)
            ingredients = []
            for ing in raw_ingredients:
                cleaned = ing.strip().strip(",.-")
                if cleaned:
                    ingredients.append(cleaned)
            
            if len(ingredients) > 1:
                return True, ingredients
                
        return False, [generic_name.strip()]

    def parse_pdf_notification(self, filepath: Path, source_url: Optional[str] = None) -> List[BannedMedicineEntry]:
        """
        Parse a downloaded PDF notification.
        If it is scanned/unstructured or has parsing failures, route to the manual review queue.
        """
        logger.info("[parser] Parsing notification PDF: %s", filepath)
        
        try:
            # 1. Run base PDF parser
            base_entries = self.base_parser.parse_pdf(filepath)
            
            # Determine notification number and date from base parser results if possible
            notification_number = None
            notification_date = None
            for entry in base_entries:
                if entry.notification_number:
                    notification_number = normalize_notification_number(entry.notification_number)
                if entry.notification_date:
                    notification_date = entry.notification_date
                    
            if not notification_number:
                # Fallback to filename parsing
                match = re.search(r"^(GSR|SO)_(\d+[a-zA-Z]?)", filepath.stem)
                if match:
                    prefix = "G.S.R." if match.group(1) == "GSR" else "S.O."
                    notification_number = f"{prefix} {match.group(2)}(E)"

            # Check if pdf is completely unreadable or has no entries
            is_unreadable = any(
                "[UNREADABLE PDF]" in e.generic_name or "[NO ENTRIES FOUND]" in e.generic_name
                for e in base_entries
            )
            
            if is_unreadable or not base_entries:
                msg = f"PDF {filepath.name} is scanned, unreadable, or contains no entries."
                logger.warning("[parser] %s", msg)
                
                # Add to manual review queue
                with self.db.session_scope() as session:
                    # Update notifications_processed parsing status
                    if notification_number:
                        self.db.upsert_notification_processing(session, {
                            "notification_number": notification_number,
                            "notification_date": notification_date,
                            "source_url": source_url,
                            "pdf_path": str(filepath),
                            "parsing_status": "failed",
                            "error_message": msg
                        })
                    
                    self.db.add_to_review_queue(session, {
                        "notification_number": notification_number,
                        "notification_date": notification_date,
                        "source_url": source_url,
                        "issue_type": "scanned_pdf" if is_unreadable else "no_entries_found",
                        "description": msg,
                        "raw_text": base_entries[0].raw_text if base_entries else None
                    })
                return []

            # 2. Process and enrich parsed entries
            processed_entries = []
            for entry in base_entries:
                if entry.parsing_status == "needs_review":
                    # Add specific suspicious entries to manual review
                    msg = f"Suspicious entry '{entry.generic_name}' requires review."
                    with self.db.session_scope() as session:
                        self.db.add_to_review_queue(session, {
                            "notification_number": notification_number or entry.notification_number,
                            "notification_date": notification_date or entry.notification_date,
                            "source_url": source_url,
                            "issue_type": "validation_failed",
                            "description": msg,
                            "raw_text": entry.raw_text
                        })
                    continue

                # Clean and split ingredients if FDC
                is_fdc, ingredients = self.split_fdc_ingredients(entry.generic_name)
                
                # Update attributes
                entry.is_fdc = is_fdc
                entry.ingredients = ingredients
                entry.source_url = source_url
                if notification_number:
                    entry.notification_number = notification_number
                if notification_date:
                    entry.notification_date = notification_date
                    
                processed_entries.append(entry)

            # Update notifications_processed table with success
            if notification_number:
                with self.db.session_scope() as session:
                    self.db.upsert_notification_processing(session, {
                        "notification_number": notification_number,
                        "notification_date": notification_date,
                        "source_url": source_url,
                        "pdf_path": str(filepath),
                        "parsing_status": "parsed",
                        "error_message": None
                    })

            logger.info("[parser] Successfully parsed %d entries from %s", len(processed_entries), filepath.name)
            return processed_entries

        except Exception as exc:
            msg = f"Uncaught exception while parsing {filepath.name}: {exc}"
            logger.error("[parser] %s", msg, exc_info=True)
            
            # Extract basic fallback details from filename
            fallback_num = None
            match = re.search(r"^(GSR|SO)_(\d+[a-zA-Z]?)", filepath.stem)
            if match:
                prefix = "G.S.R." if match.group(1) == "GSR" else "S.O."
                fallback_num = f"{prefix} {match.group(2)}(E)"
                
            with self.db.session_scope() as session:
                if fallback_num:
                    self.db.upsert_notification_processing(session, {
                        "notification_number": fallback_num,
                        "source_url": source_url,
                        "pdf_path": str(filepath),
                        "parsing_status": "failed",
                        "error_message": msg
                    })
                self.db.add_to_review_queue(session, {
                    "notification_number": fallback_num,
                    "source_url": source_url,
                    "issue_type": "parsing_failed",
                    "description": msg
                })
            return []
