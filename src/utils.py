"""
Utility module for the Indian Banned Medicines Data Pipeline.

Provides shared helpers for logging setup, date parsing, PDF verification,
filename sanitization, JSON handling, and database backups.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from src import config

logger = logging.getLogger(__name__)


def logging_setup() -> None:
    """Sets up unified logging for the application, writing to console and pipeline.log."""
    log_file = config.LOG_DIR / "pipeline.log"
    
    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8")
        ]
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logger.info("Logging initialised. Log file at: %s", log_file)


def is_valid_pdf(filepath: Path) -> bool:
    """Check if the file at the given path has valid PDF magic bytes."""
    try:
        if not filepath.exists() or filepath.stat().st_size < 4:
            return False
        with open(filepath, "rb") as f:
            header = f.read(4)
            return header == b"%PDF"
    except Exception as exc:
        logger.error("Error checking PDF validity for %s: %s", filepath, exc)
        return False


def sanitize_filename(name: str) -> str:
    """Sanitize notification numbers to be safe for filenames."""
    # Replace slashes, dots, spaces, parens with underscores
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    # Deduplicate underscores
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_")


def parse_date_with_fallback(date_str: Optional[str]) -> Optional[date]:
    """Parse a date string using multiple candidate formats."""
    if not date_str:
        return None
        
    # Standardize string format
    clean_str = re.sub(r"\s+", " ", date_str).strip()
    
    formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%B %d, %Y",
        "%d %B %Y",
        "%b %d, %Y",
        "%d %b %Y",
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(clean_str, fmt).date()
        except ValueError:
            continue
            
    # Try regex match for DD-MM-YYYY or DD.MM.YYYY
    match = re.search(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", clean_str)
    if match:
        d, m, y = match.groups()
        for fmt in ["%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"]:
            try:
                candidate = f"{d.zfill(2)}-{m.zfill(2)}-{y}"
                return datetime.strptime(candidate, "%d-%m-%Y").date()
            except ValueError:
                continue
                
    return None


def find_pdf_links(html_content: str, base_url: str) -> List[Dict[str, str]]:
    """Extract all PDF links from an HTML document."""
    soup = BeautifulSoup(html_content, "html.parser")
    links = []
    
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        text = anchor.get_text(strip=True)
        
        # We check if the link targets a PDF
        if href.lower().endswith(".pdf") or "pdf" in href.lower():
            full_url = urljoin(base_url, href)
            links.append({
                "url": full_url,
                "text": text,
                "title": anchor.get("title", "").strip()
            })
            
    return links


def read_json_file(filepath: Path) -> Any:
    """Read and parse a JSON file."""
    if not filepath.exists():
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("Failed to read JSON from %s: %s", filepath, exc)
        return None


def write_json_file(filepath: Path, data: Any) -> bool:
    """Write data to a JSON file with pretty printing."""
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as exc:
        logger.error("Failed to write JSON to %s: %s", filepath, exc)
        return False


def backup_table_data(db_manager: Any, table_name: str, backup_dir: Path) -> Optional[Path]:
    """Create a JSON backup file for a specific table's current contents."""
    from sqlalchemy import text
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"{table_name}_backup_{timestamp}.json"
        
        with db_manager.session_scope() as session:
            result = session.execute(text(f"SELECT * FROM {table_name}"))
            rows = [dict(row._mapping) for row in result]
            
            # Serialize dates and datetimes
            for row in rows:
                for k, v in row.items():
                    if isinstance(v, (date, datetime)):
                        row[k] = v.isoformat()
                        
            write_json_file(backup_file, rows)
            logger.info("Backup for table '%s' written to %s", table_name, backup_file)
            return backup_file
    except Exception as exc:
        logger.error("Failed to backup table '%s': %s", table_name, exc)
        return None
