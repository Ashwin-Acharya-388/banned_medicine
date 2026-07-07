"""
Orchestration script for the Indian Banned Medicines Data Pipeline.

Coordinates the complete workflow:
1. Discover (Scrape portals and aggregator lists to build a roadmap)
2. Acquire (Download PDF notifications listed in the roadmap)
3. Process Notifications (Parse PDF content, split FDC ingredients, validate, and store in database)
4. Cross-Reference (Verify/link unofficial aggregator entries to official CDSCO entries)
5. Backup (Dump database tables to JSON files)

Supports dry runs, verbose logging, and modular execution.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from src import config
from src.database import DatabaseManager, NotificationProcessed
from src.discovery import NotificationDiscoverer
from src.acquirer import PDFAcquirer
from src.parser import NotificationParser
from src.cross_reference import CrossReferencer
from src.utils import backup_table_data
from src.validators import (
    BannedMedicineEntry,
    DataValidator,
)
from src.scraper import is_drug_related_pdf, run_all_scrapers
from src.html_scraper import run_all_html_scrapers
from src.gov_scraper import run_all_gov_scrapers

logger = logging.getLogger("banned_drugs_pipeline")


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> None:
    """Configure dual-handler logging: file (rotating) + console."""
    log_level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Root logger
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # capture everything; handlers filter

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler (rotating: 10 MB, 5 backups)
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = config.LOG_DIR / f"pipeline_{datetime.now():%Y%m%d}.log"
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logger.info("Logging initialised. Log file: %s", log_file)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="banned-drugs-pipeline",
        description=(
            "Scrape, parse, and store Indian government banned-medicine "
            "notifications into a PostgreSQL database."
        ),
    )

    # Main workflow execution controls
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Discover notifications to compile data/notification_roadmap.json.",
    )
    parser.add_argument(
        "--acquire",
        action="store_true",
        help="Download notification PDF files based on the roadmap.",
    )
    parser.add_argument(
        "--process-notifications",
        action="store_true",
        help="Parse downloaded PDFs, split FDC ingredients, validate, and store in database.",
    )
    parser.add_argument(
        "--cross-reference",
        action="store_true",
        help="Link unofficial aggregator entries to official CDSCO entries and flag mismatches.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Export all database table contents to JSON backup files.",
    )
    parser.add_argument(
        "--html-only",
        action="store_true",
        help="Scrape and store compiled lists from unofficial HTML aggregators (no PDFs).",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Run full sequence sequentially: Discover -> Acquire -> Process -> Cross-Reference.",
    )

    # General configuration
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be processed without making changes.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable debug-level logging output.",
    )
    parser.add_argument(
        "--download-dir",
        type=Path,
        default=None,
        help=f"Override download directory (default: {config.DOWNLOAD_DIR}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of PDFs processed/downloaded during this run.",
    )

    return parser


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def stage_discover(dry_run: bool) -> None:
    """Stage: Discover notifications and generate roadmap."""
    logger.info("=" * 60)
    logger.info("STAGE: DISCOVERING NOTIFICATIONS & ROADMAP")
    logger.info("=" * 60)
    
    if dry_run:
        logger.info("[DRY RUN] Would discover notifications from CDSCO & aggregators to build roadmap.")
        return
        
    discoverer = NotificationDiscoverer()
    discoverer.discover()


def stage_acquire(dry_run: bool, limit: Optional[int]) -> None:
    """Stage: Download PDF notifications."""
    logger.info("=" * 60)
    logger.info("STAGE: ACQUIRING PDF NOTIFICATIONS")
    logger.info("=" * 60)
    
    if dry_run:
        logger.info("[DRY RUN] Would download PDF notifications from CDSCO based on the roadmap.")
        return
        
    acquirer = PDFAcquirer()
    acquirer.acquire_all(limit=limit)


def stage_process_notifications(dry_run: bool, db: DatabaseManager, limit: Optional[int]) -> None:
    """Stage: Parse downloaded PDFs, extract ingredients, validate, and store in database."""
    logger.info("=" * 60)
    logger.info("STAGE: PROCESSING & PARSING PDF NOTIFICATIONS")
    logger.info("=" * 60)
    
    # 1. Discover existing downloaded PDFs in processing table
    with db.session_scope() as session:
        downloaded = session.query(NotificationProcessed).filter_by(download_status="downloaded").all()
        
    if not downloaded:
        logger.warning("No downloaded PDFs found in database notifications_processed table. Run --acquire first.")
        return
        
    logger.info("Found %d downloaded notification(s) to process.", len(downloaded))
    
    parser = NotificationParser(db)
    count = 0
    
    for item in downloaded:
        if limit and count >= limit:
            break
            
        pdf_path = Path(item.pdf_path) if item.pdf_path else None
        if not pdf_path or not pdf_path.exists():
            logger.warning("PDF path does not exist for notification %s: %s", item.notification_number, pdf_path)
            continue
            
        if dry_run:
            logger.info("[DRY RUN] Would parse PDF: %s (source URL: %s)", pdf_path.name, item.source_url)
            count += 1
            continue
            
        # Parse PDF and extract medicine entries
        raw_entries = parser.parse_pdf_notification(pdf_path, source_url=item.source_url)
        if not raw_entries:
            count += 1
            continue
            
        # Normalize and validate entries
        valid_entries = []
        for entry in raw_entries:
            norm_entry = DataValidator.normalize_data(entry)
            result = DataValidator.validate_notification_entry(norm_entry)
            if result.is_valid:
                valid_entries.append(norm_entry)
            else:
                msg = f"Validation failed for parsed drug '{entry.generic_name}': {result.errors}"
                logger.warning(msg)
                with db.session_scope() as session:
                    db.add_to_review_queue(session, {
                        "notification_number": item.notification_number,
                        "notification_date": item.notification_date,
                        "source_url": item.source_url,
                        "issue_type": "validation_failed",
                        "description": msg,
                        "raw_text": entry.raw_text
                    })
                    
        # Deduplicate
        unique_entries = DataValidator.deduplicate_entries(valid_entries)
        
        # Save to primary database table
        with db.session_scope() as session:
            for entry in unique_entries:
                db.upsert_medicine(session, entry.to_dict())
                
        logger.info("Successfully processed and saved %d entries for notification %s", len(unique_entries), item.notification_number)
        count += 1


def stage_cross_reference(dry_run: bool) -> None:
    """Stage: Link unofficial aggregate databases with official CDSCO tables."""
    logger.info("=" * 60)
    logger.info("STAGE: CROSS-REFERENCING & VERIFICATION")
    logger.info("=" * 60)
    
    if dry_run:
        logger.info("[DRY RUN] Would cross-reference unofficial/AYUSH/FSSAI records with CDSCO banned_medicines.")
        return
        
    referencer = CrossReferencer()
    referencer.cross_reference_all()


def stage_backup(dry_run: bool, db: DatabaseManager) -> None:
    """Stage: Export database tables to JSON backups."""
    logger.info("=" * 60)
    logger.info("STAGE: DATABASE BACKUP")
    logger.info("=" * 60)
    
    tables = [
        "banned_medicines",
        "unofficial_medicines",
        "ayush_fssai_medicines",
        "notifications_processed",
        "manual_review_queue"
    ]
    
    if dry_run:
        logger.info("[DRY RUN] Would backup tables: %s", ", ".join(tables))
        return
        
    for table in tables:
        backup_table_data(db, table, config.EXPORT_DIR)


def stage_scrape_html(dry_run: bool) -> list[BannedMedicineEntry]:
    """Stage: Scrape unofficial HTML aggregate lists (legacy support)."""
    logger.info("=" * 60)
    logger.info("STAGE: SCRAPING HTML AGGREGATORS (LEGACY)")
    logger.info("=" * 60)

    if dry_run:
        logger.info("[DRY RUN] Would scrape Vaayath and TheHealthMaster compiled tables.")
        return []

    entries = run_all_html_scrapers()
    logger.info("HTML scraping complete. Extracted %d entries.", len(entries))
    return entries


def stage_scrape_gov(dry_run: bool) -> list[BannedMedicineEntry]:
    """Stage: Scrape government bodies (AYUSH, FSSAI) (legacy support)."""
    logger.info("=" * 60)
    logger.info("STAGE: SCRAPING GOVT BODIES (AYUSH, FSSAI) (LEGACY)")
    logger.info("=" * 60)

    if dry_run:
        logger.info("[DRY RUN] Would scrape AYUSH and FSSAI portals.")
        return []

    entries = run_all_gov_scrapers()
    logger.info("Government scraping complete. Extracted %d entries.", len(entries))
    return entries


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Main entry point for the pipeline CLI."""
    parser = build_arg_parser()
    args = parser.parse_args()

    # Verify at least one action is specified
    has_action = (
        args.discover or args.acquire or args.process_notifications or
        args.cross_reference or args.backup or args.html_only or args.update
    )
    if not has_action:
        parser.print_help()
        sys.exit(1)

    # Setup logging
    setup_logging(verbose=args.verbose)

    logger.info("=" * 60)
    logger.info("INDIAN BANNED MEDICINES DATA PIPELINE")
    logger.info("=" * 60)
    logger.info("Dry run: %s", args.dry_run)
    logger.info("Database: %s", config.DATABASE_URL.rsplit("@", 1)[-1])  # hide credentials
    
    # Initialize DB manager if database operations are involved
    db: DatabaseManager | None = None
    if not args.dry_run and (args.acquire or args.process_notifications or args.cross_reference or args.backup or args.html_only or args.update):
        db = DatabaseManager()
        if not db.check_connection():
            logger.error("Cannot connect to database. Check DATABASE_URL in .env")
            sys.exit(1)
        db.create_tables()

    # 1. Discover/Roadmap
    if args.discover or args.update:
        stage_discover(args.dry_run)

    # 2. Acquire PDFs
    if args.acquire or args.update:
        stage_acquire(args.dry_run, limit=args.limit)

    # 3. Process/Parse PDFs
    if args.process_notifications or args.update:
        if args.dry_run:
            stage_process_notifications(dry_run=True, db=DatabaseManager(), limit=args.limit)
        elif db:
            stage_process_notifications(dry_run=False, db=db, limit=args.limit)

    # 4. Cross-Reference
    if args.cross_reference or args.update:
        stage_cross_reference(args.dry_run)

    # 5. Backup database
    if args.backup:
        if args.dry_run:
            stage_backup(dry_run=True, db=DatabaseManager())
        elif db:
            stage_backup(dry_run=False, db=db)

    # Legacy: HTML aggregator scraping only (if explicitly asked)
    if args.html_only:
        html_entries = stage_scrape_html(args.dry_run)
        gov_entries = stage_scrape_gov(args.dry_run)
        
        if not args.dry_run and db:
            # Normalize, validate, deduplicate
            all_entries = html_entries + gov_entries
            valid_entries = []
            for entry in all_entries:
                norm_entry = DataValidator.normalize_data(entry)
                result = DataValidator.validate_notification_entry(norm_entry)
                if result.is_valid:
                    valid_entries.append(norm_entry)
            unique_entries = DataValidator.deduplicate_entries(valid_entries)
            
            # Store in DB
            inserted_unofficial = 0
            updated_unofficial = 0
            inserted_ayush_fssai = 0
            updated_ayush_fssai = 0
            
            with db.session_scope() as session:
                for entry in unique_entries:
                    source = (entry.source_pdf or "").lower()
                    if "vaayath" in source or "thehealthmaster" in source or "healthmaster" in source:
                        medicine = db.upsert_unofficial_medicine(session, entry.to_dict())
                        if medicine and medicine.id:
                            inserted_unofficial += 1
                        else:
                            updated_unofficial += 1
                    elif "ayush" in source or "fssai" in source:
                        medicine = db.upsert_ayush_fssai_medicine(session, entry.to_dict())
                        if medicine and medicine.id:
                            inserted_ayush_fssai += 1
                        else:
                            updated_ayush_fssai += 1
                            
            logger.info("Saved HTML aggregation entries. Unofficial=%d/%d, AYUSH_FSSAI=%d/%d",
                        inserted_unofficial, updated_unofficial, inserted_ayush_fssai, updated_ayush_fssai)

    logger.info("=" * 60)
    logger.info("RUN COMPLETE")
    logger.info("=" * 60)

    if not args.dry_run and db:
        with db.session_scope() as session:
            logger.info(
                "Database status summary: %d CDSCO, %d Unofficial, %d AYUSH/FSSAI records.",
                db.get_medicine_count(session),
                db.get_unofficial_medicine_count(session),
                db.get_ayush_fssai_medicine_count(session)
            )


if __name__ == "__main__":
    main()
