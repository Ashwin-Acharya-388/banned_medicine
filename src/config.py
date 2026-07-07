"""
Configuration management for the Indian Banned Medicines Data Pipeline.

Loads all settings from environment variables with sensible defaults.
Uses python-dotenv to read from a .env file if present.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env file from the project root (two levels up from this file)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Directory paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = _PROJECT_ROOT

DOWNLOAD_DIR = Path(
    os.getenv("DOWNLOAD_DIR", str(PROJECT_ROOT / "downloads"))
)

LOG_DIR = Path(
    os.getenv("LOG_DIR", str(PROJECT_ROOT / "logs"))
)

DATA_DIR = Path(
    os.getenv("DATA_DIR", str(PROJECT_ROOT / "data"))
)

EXPORT_DIR = Path(
    os.getenv("EXPORT_DIR", str(PROJECT_ROOT / "exports"))
)

# Ensure directories exist at import time
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/banned_drugs",
)

# SQLAlchemy connection-pool tuning
DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))
DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))

# ---------------------------------------------------------------------------
# Scraper behaviour
# ---------------------------------------------------------------------------
SCRAPE_DELAY_MIN: float = float(os.getenv("SCRAPE_DELAY_MIN", "2"))
SCRAPE_DELAY_MAX: float = float(os.getenv("SCRAPE_DELAY_MAX", "5"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "30"))
FDA_MAX_WORKERS: int = int(os.getenv("FDA_MAX_WORKERS", "3"))

# ---------------------------------------------------------------------------
# Target URLs
# ---------------------------------------------------------------------------
CDSCO_NOTIFICATIONS_URL: str = os.getenv(
    "CDSCO_NOTIFICATIONS_URL",
    "https://cdsco.gov.in/opencms/opencms/en/Notifications/",
)
CDSCO_NSQ_ALERTS_URL: str = os.getenv(
    "CDSCO_NSQ_ALERTS_URL",
    "https://cdsco.gov.in/opencms/opencms/en/Notifications/NSQ-Alerts/",
)
CDSCO_BASE_URL: str = os.getenv(
    "CDSCO_BASE_URL",
    "https://cdsco.gov.in",
)

PIB_BASE_URL: str = os.getenv(
    "PIB_BASE_URL",
    "https://pib.gov.in",
)
PIB_SEARCH_URL: str = os.getenv(
    "PIB_SEARCH_URL",
    "https://pib.gov.in/allRel.aspx",
)

# ---------------------------------------------------------------------------
# Keywords used to identify ban-related PDF links
# A link/text must match at least one STRONG keyword to be considered.
# ---------------------------------------------------------------------------
BAN_KEYWORDS_STRONG: list[str] = [
    "banned drug",
    "ban drug",
    "prohibited drug",
    "prohibited medicine",
    "prohibition of drug",
    "prohibition of fixed",
    "fdc ban",
    "fdc prohibit",
    "fixed dose combination",
    "fixed-dose combination",
    "section 26a",
    "drugs banned",
    "drugs prohibited",
    "list of drugs prohibited",
    "manufacture and sale",
]

# Fallback keywords — used only if the document also does NOT match
# any exclusion keyword.
BAN_KEYWORDS_WEAK: list[str] = [
    "g.s.r",
    "gsr",
    "s.o.",
    "gazette notification",
]

# Documents matching any of these keywords are REJECTED even if they
# match a ban keyword.  Prevents downloading press releases, citizen
# charters, fact-check reports, RTI documents, etc.
SCRAPER_EXCLUDE_KEYWORDS: list[str] = [
    "fact check",
    "factcheck",
    "citizen charter",
    "citizens charter",
    "grievance",
    "press release summary",
    "annual report",
    "rti",
    "right to information",
    "fraudulent website",
    "social media",
    "twitter",
    "instagram",
    "vacancy",
    "recruitment",
    "tender",
    "budget",
    "parliament",
    "lok sabha",
    "rajya sabha",
]

# ---------------------------------------------------------------------------
# Blacklisted generic names — entries matching these are rejected by the
# validator as false-positives from administrative/non-drug documents.
# ---------------------------------------------------------------------------
BLACKLISTED_GENERIC_NAMES: set[str] = {
    # Months
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    # Administrative terms
    "total", "query", "queries", "total queries", "actionable queries",
    "fact check", "fact check unit", "thread link", "twitter link",
    "status", "target", "summary", "report", "grievance", "date",
    "page", "till", "result", "action", "actionable",
    "sr. no", "sr no", "sl. no", "sl no", "s.no", "sno",
    # Website/URL patterns
    "nchmr.com", "nrrms.com", "niyukti.org", "brs.inc",
    # Document structure terms
    "annexure", "appendix", "schedule", "table", "index",
    "contents", "preface", "foreword", "introduction",
    "[unreadable pdf]", "[no entries found]",
}

# ---------------------------------------------------------------------------
# User-Agent rotation pool
# (Real browser user-agents to avoid being flagged)
# ---------------------------------------------------------------------------
USER_AGENTS: list[str] = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
    "Gecko/20100101 Firefox/126.0",
    # Firefox on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) "
    "Gecko/20100101 Firefox/126.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]

# ---------------------------------------------------------------------------
# Known dosage forms (used for normalization & validation)
# ---------------------------------------------------------------------------
KNOWN_DOSAGE_FORMS: dict[str, str] = {
    "tablet": "Tablet",
    "tab": "Tablet",
    "tablets": "Tablet",
    "capsule": "Capsule",
    "cap": "Capsule",
    "capsules": "Capsule",
    "syrup": "Syrup",
    "syr": "Syrup",
    "suspension": "Suspension",
    "susp": "Suspension",
    "injection": "Injection",
    "inj": "Injection",
    "cream": "Cream",
    "ointment": "Ointment",
    "gel": "Gel",
    "drops": "Drops",
    "drop": "Drops",
    "eye drops": "Eye Drops",
    "ear drops": "Ear Drops",
    "nasal drops": "Nasal Drops",
    "lotion": "Lotion",
    "powder": "Powder",
    "solution": "Solution",
    "soln": "Solution",
    "inhaler": "Inhaler",
    "spray": "Spray",
    "patch": "Patch",
    "suppository": "Suppository",
    "respules": "Respules",
    "oral liquid": "Oral Liquid",
    "dry syrup": "Dry Syrup",
    "infusion": "Infusion",
    "emulsion": "Emulsion",
}

# ---------------------------------------------------------------------------
# Metadata log file (tracks downloaded PDFs)
# ---------------------------------------------------------------------------
METADATA_LOG_FILE = DOWNLOAD_DIR / "download_metadata.json"

# ---------------------------------------------------------------------------
# Unofficial Aggregator Sources URLs
# ---------------------------------------------------------------------------
VAAYATH_URL: str = "https://vaayath.com/complete-list-of-banned-drugs-in-india/"
HEALTHMASTER_URL: str = "https://thehealthmaster.com/banned-drugs-india/"

# ---------------------------------------------------------------------------
# Government Sources URLs
# ---------------------------------------------------------------------------
AYUSH_URL: str = "https://ayush.gov.in/notifications"
FSSAI_URL: str = "https://fssai.gov.in/prohibited-ingredients"
CDSCO_BANNED_URL: str = "https://cdsco.gov.in/opencms/opencms/en/consumer/List-Of-Banned-Drugs/"

# ---------------------------------------------------------------------------
# Notification number matching patterns
# ---------------------------------------------------------------------------
NOTIFICATION_PATTERNS: dict[str, str] = {
    "gsr": r"(?:G\.S\.R\.|GSR)\s*(\d+)\s*\(E\)",
    "so": r"(?:S\.O\.|SO)\s*(\d+)\s*\(E\)",
}
