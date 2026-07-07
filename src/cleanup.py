"""
Database cleanup script for the Indian Banned Medicines Data Pipeline.

Removes false-positive records (administrative terms, months, URLs, etc.)
that were incorrectly extracted from non-drug PDFs and inserted into the
``banned_medicines`` table.

Usage:
    python3 -m src.cleanup              # Dry-run (show what would be deleted)
    python3 -m src.cleanup --apply      # Actually delete the records
    python3 -m src.cleanup --nuke-all   # Delete ALL records and re-run pipeline
"""

from __future__ import annotations

import argparse
import logging
import re
import sys

from sqlalchemy import text

from src import config
from src.database import BannedMedicine, UnofficialMedicine, AyushFssaiMedicine, DatabaseManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _build_blacklist_pattern() -> re.Pattern:
    """Build a compiled regex from all blacklisted generic names."""
    escaped = [re.escape(name) for name in config.BLACKLISTED_GENERIC_NAMES]
    return re.compile(
        r"^(?:" + "|".join(escaped) + r")$",
        re.IGNORECASE,
    )


def _is_junk_entry(generic_name: str) -> bool:
    """
    Return True if a generic_name looks like a false-positive.

    Checks against:
    1. Exact blacklist match
    2. Domain-like patterns (e.g. nchmr.com)
    3. URL patterns
    4. Very short names (< 3 chars)
    5. All-numeric strings
    6. Contains junk substrings (http, www, .com, etc.)
    """
    name = generic_name.strip().lower()

    # Exact blacklist
    if name in config.BLACKLISTED_GENERIC_NAMES:
        return True

    # Domain pattern
    if re.fullmatch(r"[\w\-]+\.\w{2,6}", name):
        return True

    # URL-like
    if any(s in name for s in ["http", "www.", ".com", ".in", ".org", ".net"]):
        return True

    # Too short
    if len(name) < 3:
        return True

    # All-numeric
    if re.fullmatch(r"[\d\s.,%]+", name):
        return True

    # Contains junk substrings
    junk = [
        "click here", "download", "link", "thread link",
        "twitter", "instagram", "facebook",
    ]
    for j in junk:
        if j in name:
            return True

    # Administrative / navigation keywords
    admin_keywords = (
        "department", "departments", "ministry", "directorate", "committee",
        "board", "governor", "officer", "officers", "secretary", "minister",
        "staff strength", "seniority", "promotion", "promotions", "training",
        "about us", "contact", "contact us", "feedback", "sitemap", "gallery",
        "photo gallery", "login", "search", "screen reader", "skip to",
        "accessibility", "disclaimer", "terms", "increase text", "decrease text",
        "high contrast", "grayscale", "negative contrast", "light background",
        "tender", "tenders", "career", "careers", "recruitment", "jobs",
        "advertisement", "policy", "scheme", "schemes", "service", "services",
        "directory", "forms", "orders", "circulars", "statistics", "legislation",
        "acts", "rules", "notices", "archives", "certificates", "who is who",
        "organization chart", "citizen charter", "government order",
        "standard treatment", "licence", "know your",
    )
    for kw in admin_keywords:
        if re.search(rf"\b{re.escape(kw)}\b", name):
            return True

    # Archive labels like "January (72)"
    if re.fullmatch(
        r"(?:january|february|march|april|may|june|july|august|"
        r"september|october|november|december)\s*\(\d+\)",
        name,
        re.IGNORECASE,
    ):
        return True

    return False


def cleanup_database(apply: bool = False) -> int:
    """
    Scan the database for false-positive entries and optionally delete them.

    Parameters
    ----------
    apply : bool
        If True, actually delete the records. If False, just report.

    Returns
    -------
    int
        Number of junk records found (or deleted).
    """
    db = DatabaseManager()
    total_junk = 0

    with db.session_scope() as session:
        for model in (BannedMedicine, UnofficialMedicine, AyushFssaiMedicine):
            entries = session.query(model).all()
            junk_ids: list[int] = []

            for med in entries:
                if _is_junk_entry(med.generic_name):
                    junk_ids.append(med.id)
                    logger.info(
                        "  [%s] JUNK: id=%d  generic_name=%r  dosage_form=%r  source=%r",
                        model.__name__, med.id, med.generic_name, med.dosage_form, med.source_pdf,
                    )

            junk_count = len(junk_ids)
            total = len(entries)
            total_junk += junk_count
            logger.info(
                "[%s] Found %d junk entries out of %d total (%d clean).",
                model.__name__, junk_count, total, total - junk_count,
            )

            if apply and junk_ids:
                deleted = (
                    session.query(model)
                    .filter(model.id.in_(junk_ids))
                    .delete(synchronize_session="fetch")
                )
                logger.info("[%s] Deleted %d junk records.", model.__name__, deleted)
            elif junk_ids:
                logger.info(
                    "[%s] Dry run — no records deleted. Use --apply to delete.",
                    model.__name__
                )

    return total_junk


def nuke_all() -> None:
    """Delete ALL records from all tables and source_documents."""
    db = DatabaseManager()
    with db.session_scope() as session:
        med_count = session.execute(
            text("DELETE FROM banned_medicines")
        ).rowcount
        unof_count = session.execute(
            text("DELETE FROM unofficial_medicines")
        ).rowcount
        ayush_count = session.execute(
            text("DELETE FROM ayush_fssai_medicines")
        ).rowcount
        doc_count = session.execute(
            text("DELETE FROM source_documents")
        ).rowcount
        session.commit()
        logger.info(
            "Nuked ALL data: %d medicines, %d unofficial, %d AYUSH/FSSAI, %d source documents deleted.",
            med_count, unof_count, ayush_count, doc_count,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean up false-positive entries from the banned_medicines database.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete junk records (default is dry-run).",
    )
    group.add_argument(
        "--nuke-all",
        action="store_true",
        help="Delete ALL records and start fresh.",
    )
    args = parser.parse_args()

    if args.nuke_all:
        logger.warning("NUKING all data from banned_medicines and source_documents!")
        nuke_all()
    else:
        cleanup_database(apply=args.apply)


if __name__ == "__main__":
    main()
