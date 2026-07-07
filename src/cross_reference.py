"""
Cross-Referencing & Verification module for the Indian Banned Medicines Data Pipeline.

Links unofficial/AYUSH/FSSAI records with primary CDSCO banned medicines (official PDFs)
using normalized notification numbers and ingredient matching.
Unlinked or missing notifications are flagged and routed to the manual review queue.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.database import DatabaseManager, BannedMedicine, UnofficialMedicine, AyushFssaiMedicine
from src.validators import normalize_notification_number

logger = logging.getLogger(__name__)


class CrossReferencer:
    """Verifies and links aggregator lists against the definitive CDSCO banned_medicines database."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or DatabaseManager()

    def cross_reference_all(self) -> dict[str, int]:
        """
        Cross-reference all records in unofficial_medicines and ayush_fssai_medicines tables.
        Returns a dict of run summary statistics.
        """
        logger.info("[cross_reference] Starting cross-referencing run...")
        
        stats = {
            "unofficial_total": 0,
            "unofficial_linked": 0,
            "unofficial_unlinked": 0,
            "ayush_fssai_total": 0,
            "ayush_fssai_linked": 0,
            "ayush_fssai_unlinked": 0,
        }

        with self.db.session_scope() as session:
            # 1. Fetch all CDSCO banned medicines for mapping cache
            banned_list = session.query(BannedMedicine).all()
            
            # Map notification_number -> list of BannedMedicine records
            banned_by_notif: dict[str, list[BannedMedicine]] = {}
            for bm in banned_list:
                if bm.notification_number:
                    norm = normalize_notification_number(bm.notification_number)
                    banned_by_notif.setdefault(norm, []).append(bm)

            # 2. Process Unofficial Banned Medicines
            unofficials = session.query(UnofficialMedicine).all()
            stats["unofficial_total"] = len(unofficials)
            
            for rec in unofficials:
                notif_raw = rec.notification_number
                if not notif_raw:
                    # Missing notification number
                    msg = f"Unofficial record '{rec.generic_name}' is missing a notification number."
                    self.db.add_to_review_queue(session, {
                        "notification_number": None,
                        "notification_date": rec.notification_date,
                        "issue_type": "missing_notification_number",
                        "description": msg
                    })
                    stats["unofficial_unlinked"] += 1
                    continue
                    
                norm_notif = normalize_notification_number(notif_raw)
                
                # Check if this notification exists in official CDSCO database
                if norm_notif not in banned_by_notif:
                    msg = f"Notification {norm_notif} for unofficial drug '{rec.generic_name}' is not in CDSCO banned_medicines."
                    self.db.add_to_review_queue(session, {
                        "notification_number": norm_notif,
                        "notification_date": rec.notification_date,
                        "issue_type": "unlinked_record",
                        "description": msg
                    })
                    stats["unofficial_unlinked"] += 1
                    continue
                    
                # Notification exists, let's verify if the drug matches
                matching_bms = banned_by_notif[norm_notif]
                match_found = False
                
                rec_name_clean = rec.generic_name.strip().lower()
                rec_ingredients = {i.strip().lower() for i in rec.generic_name.split('+') if i.strip()}
                
                for bm in matching_bms:
                    bm_name_clean = bm.generic_name.strip().lower()
                    bm_ingredients = {i.strip().lower() for i in bm.ingredients} if bm.ingredients else {bm_name_clean}
                    
                    # Check exact name match or ingredients set intersection match
                    if rec_name_clean == bm_name_clean or (rec_ingredients and rec_ingredients == bm_ingredients):
                        match_found = True
                        break
                        
                if match_found:
                    stats["unofficial_linked"] += 1
                else:
                    msg = (f"Unofficial drug '{rec.generic_name}' notification {norm_notif} found, "
                           f"but drug names or ingredients do not match parsed CDSCO entries.")
                    self.db.add_to_review_queue(session, {
                        "notification_number": norm_notif,
                        "notification_date": rec.notification_date,
                        "issue_type": "unlinked_record",
                        "description": msg
                    })
                    stats["unofficial_unlinked"] += 1

            # 3. Process AYUSH/FSSAI Banned Medicines
            ayush_fssais = session.query(AyushFssaiMedicine).all()
            stats["ayush_fssai_total"] = len(ayush_fssais)
            
            for rec in ayush_fssais:
                notif_raw = rec.notification_number
                if not notif_raw:
                    # Missing notification number
                    msg = f"AYUSH/FSSAI record '{rec.generic_name}' is missing a notification number."
                    self.db.add_to_review_queue(session, {
                        "notification_number": None,
                        "notification_date": rec.notification_date,
                        "issue_type": "missing_notification_number",
                        "description": msg
                    })
                    stats["ayush_fssai_unlinked"] += 1
                    continue
                    
                norm_notif = normalize_notification_number(notif_raw)
                
                # Check if notification exists
                if norm_notif not in banned_by_notif:
                    msg = f"Notification {norm_notif} for AYUSH/FSSAI drug '{rec.generic_name}' is not in CDSCO banned_medicines."
                    self.db.add_to_review_queue(session, {
                        "notification_number": norm_notif,
                        "notification_date": rec.notification_date,
                        "issue_type": "unlinked_record",
                        "description": msg
                    })
                    stats["ayush_fssai_unlinked"] += 1
                    continue
                    
                # Match drug name
                matching_bms = banned_by_notif[norm_notif]
                match_found = False
                
                rec_name_clean = rec.generic_name.strip().lower()
                rec_ingredients = {i.strip().lower() for i in rec.generic_name.split('+') if i.strip()}
                
                for bm in matching_bms:
                    bm_name_clean = bm.generic_name.strip().lower()
                    bm_ingredients = {i.strip().lower() for i in bm.ingredients} if bm.ingredients else {bm_name_clean}
                    
                    if rec_name_clean == bm_name_clean or (rec_ingredients and rec_ingredients == bm_ingredients):
                        match_found = True
                        break
                        
                if match_found:
                    stats["ayush_fssai_linked"] += 1
                else:
                    msg = (f"AYUSH/FSSAI drug '{rec.generic_name}' notification {norm_notif} found, "
                           f"but drug names or ingredients do not match parsed CDSCO entries.")
                    self.db.add_to_review_queue(session, {
                        "notification_number": norm_notif,
                        "notification_date": rec.notification_date,
                        "issue_type": "unlinked_record",
                        "description": msg
                    })
                    stats["ayush_fssai_unlinked"] += 1

        logger.info("[cross_reference] Cross-referencing run complete. Summary: %s", stats)
        return stats


if __name__ == "__main__":
    from src.utils import logging_setup
    logging_setup()
    referencer = CrossReferencer()
    referencer.cross_reference_all()
