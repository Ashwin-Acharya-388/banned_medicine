"""
Data validation and normalization utilities for the Banned Medicines pipeline.

Provides functions to:
- Validate parsed medicine entries before database insertion
- Normalize drug names, dosage forms, and other fields
- Deduplicate entries within a batch
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from src import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BannedMedicineEntry:
    """Structured representation of a single banned-medicine record."""

    generic_name: str = ""
    brand_names: list[str] = field(default_factory=list)
    dosage_form: Optional[str] = None
    strength: Optional[str] = None
    notification_number: Optional[str] = None
    notification_date: Optional[date] = None
    ban_reason: Optional[str] = None
    source_pdf: Optional[str] = None
    parsing_status: str = "ok"  # ok | needs_review
    raw_text: Optional[str] = None  # preserved for manual review
    is_fdc: bool = False
    ingredients: list[str] = field(default_factory=list)
    source_url: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to a plain dictionary suitable for database insertion."""
        return {
            "generic_name": self.generic_name,
            "brand_names": self.brand_names,
            "dosage_form": self.dosage_form,
            "strength": self.strength,
            "notification_number": self.notification_number,
            "notification_date": self.notification_date,
            "ban_reason": self.ban_reason,
            "source_pdf": self.source_pdf,
            "is_fdc": self.is_fdc,
            "ingredients": self.ingredients,
            "source_url": self.source_url,
        }


@dataclass
class ValidationResult:
    """Result of validating a single entry."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def normalize_generic_name(name: str) -> str:
    """
    Standardise a generic/INN drug name.

    - Strip leading/trailing whitespace
    - Collapse internal whitespace
    - Title-case each word
    - Remove trailing punctuation (periods, commas)
    """
    if not name:
        return ""
    name = re.sub(r"\s+", " ", name.strip())
    name = name.rstrip(".,;:")
    # Title-case but keep roman numerals uppercase
    words = []
    for word in name.split():
        if re.fullmatch(r"[IVXLCDM]+", word.upper()) and len(word) <= 5:
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    return " ".join(words)


def normalize_dosage_form(form: Optional[str]) -> Optional[str]:
    """
    Map common dosage-form abbreviations / variations to a canonical name.

    Returns ``None`` if the input is empty or unrecognised.
    """
    if not form:
        return None
    key = form.strip().lower().rstrip("s.")
    # Try exact match first, then with trailing 's'
    canonical = config.KNOWN_DOSAGE_FORMS.get(key)
    if canonical:
        return canonical
    canonical = config.KNOWN_DOSAGE_FORMS.get(key + "s")
    if canonical:
        return canonical
    # If still not found, return cleaned title-case version
    return form.strip().title()


def normalize_strength(strength: Optional[str]) -> Optional[str]:
    """
    Clean up strength/concentration strings.

    - Normalise whitespace
    - Ensure consistent formatting of units (mg, ml, g, mcg, IU)
    """
    if not strength:
        return None
    s = re.sub(r"\s+", " ", strength.strip())
    # Normalise unit spacing: "500mg" → "500 mg"
    s = re.sub(r"(\d)(mg|ml|g|mcg|iu|%)", r"\1 \2", s, flags=re.IGNORECASE)
    return s


def normalize_notification_number(number: Optional[str]) -> Optional[str]:
    """
    Standardise G.S.R. / S.O. notification numbers.

    E.g. "G.S.R.  123 (E)" → "G.S.R. 123(E)"
    """
    if not number:
        return None
    n = number.strip()
    # Collapse extra spaces
    n = re.sub(r"\s+", " ", n)
    # Remove space before (E)
    n = re.sub(r"\s*\(\s*E\s*\)", "(E)", n)
    return n


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_GSR_PATTERN = re.compile(r"G\.?S\.?R\.?\s*\d+", re.IGNORECASE)
_SO_PATTERN = re.compile(r"S\.?O\.?\s*\d+", re.IGNORECASE)
_ADMIN_NAVIGATION_KEYWORDS: tuple[str, ...] = (
    # Government structure
    "department",
    "departments",
    "ministry",
    "directorate",
    "committee",
    "board",
    # Personnel / titles
    "governor",
    "officer",
    "officers",
    "secretary",
    "minister",
    "staff strength",
    "seniority",
    "promotion",
    "promotions",
    "training",
    # Navigation / website chrome
    "about us",
    "contact",
    "contact us",
    "feedback",
    "sitemap",
    "gallery",
    "photo gallery",
    "login",
    "search",
    "screen reader",
    "skip to",
    "accessibility",
    "disclaimer",
    "terms",
    "increase text",
    "decrease text",
    "high contrast",
    "grayscale",
    "negative contrast",
    "light background",
    # Administrative actions
    "tender",
    "tenders",
    "career",
    "careers",
    "recruitment",
    "jobs",
    "advertisement",
    "policy",
    "scheme",
    "schemes",
    "service",
    "services",
    # Document/portal sections
    "directory",
    "forms",
    "orders",
    "circulars",
    "statistics",
    "legislation",
    "acts",
    "rules",
    "notices",
    "archives",
    "certificates",
    "who is who",
    "organization chart",
    "citizen charter",
    "government order",
    "standard treatment",
    # Licence / administrative operations (not drug names)
    "licence",
    "know your",
)

# Archive labels like "January (72)", "February (8)"
_ARCHIVE_LABEL_PATTERN = re.compile(
    r"^(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s*\(\d+\)$",
    re.IGNORECASE,
)


def validate_medicine_entry(entry: BannedMedicineEntry) -> ValidationResult:
    """
    Validate a single :class:`BannedMedicineEntry`.

    Checks performed:
    1. ``generic_name`` must not be empty.
    2. ``generic_name`` must not be in the blacklist.
    3. ``generic_name`` must look like a plausible drug name (not a URL, etc.).
    4. ``notification_date`` (if present) must not be in the future.
    5. ``dosage_form`` should be a recognised form (warning if not).
    6. ``notification_number`` should match G.S.R. or S.O. patterns (warning if not).

    Returns a :class:`ValidationResult`.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Generic name is mandatory
    if not entry.generic_name or not entry.generic_name.strip():
        errors.append("generic_name is empty")
    else:
        name_lower = entry.generic_name.strip().lower()

        # 2. Blacklist check
        if name_lower in config.BLACKLISTED_GENERIC_NAMES:
            errors.append(
                f"generic_name is blacklisted: {entry.generic_name!r}"
            )

        # 3. Heuristic: must look like a plausible drug name
        #    - Reject URLs / domain names
        if re.search(r"\.\w{2,4}$", name_lower) and re.search(r"[/:]|www\.", name_lower):
            errors.append(
                f"generic_name looks like a URL: {entry.generic_name!r}"
            )
        #    - Reject domain-like patterns (e.g. "nchmr.com")
        if re.fullmatch(r"[\w\-]+\.\w{2,6}", name_lower):
            errors.append(
                f"generic_name looks like a domain: {entry.generic_name!r}"
            )
        #    - Reject very short names (< 3 chars, excluding known abbreviations)
        if len(name_lower) < 3:
            errors.append(
                f"generic_name too short: {entry.generic_name!r}"
            )
        #    - Reject all-numeric strings
        if re.fullmatch(r"[\d\s.,%]+", name_lower):
            errors.append(
                f"generic_name is numeric: {entry.generic_name!r}"
            )
        #    - Reject entries containing common junk substrings
        junk_substrings = [
            "http", "www.", ".com", ".in", ".org", ".net",
            "link", "click here", "download",
        ]
        for junk in junk_substrings:
            if junk in name_lower:
                errors.append(
                    f"generic_name contains junk substring '{junk}': "
                    f"{entry.generic_name!r}"
                )
                break

        #    - Reject administrative, directory, or navigation labels from
        #      government homepages and menus.
        for admin_keyword in _ADMIN_NAVIGATION_KEYWORDS:
            if re.search(rf"\b{re.escape(admin_keyword)}\b", name_lower):
                errors.append(
                    "generic_name contains administrative/navigation "
                    f"keyword '{admin_keyword}': {entry.generic_name!r}"
                )
                break

        #    - Reject archive / calendar labels like "January (72)"
        if _ARCHIVE_LABEL_PATTERN.match(name_lower):
            errors.append(
                f"generic_name matches archive label pattern: "
                f"{entry.generic_name!r}"
            )

    # 4. Notification date must not be in the future
    if entry.notification_date is not None:
        if isinstance(entry.notification_date, datetime):
            check_date = entry.notification_date.date()
        else:
            check_date = entry.notification_date
        if check_date > date.today():
            errors.append(
                f"notification_date is in the future: {entry.notification_date}"
            )

    # 5. Dosage form should be recognised
    if entry.dosage_form:
        key = entry.dosage_form.strip().lower()
        if key not in config.KNOWN_DOSAGE_FORMS and key.rstrip("s") not in config.KNOWN_DOSAGE_FORMS:
            warnings.append(
                f"Unrecognised dosage_form: {entry.dosage_form!r}"
            )

    # 6. Notification number should look valid
    if entry.notification_number:
        if not (
            _GSR_PATTERN.search(entry.notification_number)
            or _SO_PATTERN.search(entry.notification_number)
        ):
            warnings.append(
                f"notification_number does not match G.S.R./S.O. pattern: "
                f"{entry.notification_number!r}"
            )

    is_valid = len(errors) == 0
    if not is_valid:
        logger.warning("Validation FAILED for entry: %s — %s", entry.generic_name, errors)
    elif warnings:
        logger.info("Validation OK with warnings for: %s — %s", entry.generic_name, warnings)

    return ValidationResult(is_valid=is_valid, errors=errors, warnings=warnings)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _dedup_key(entry: BannedMedicineEntry) -> tuple:
    """Generate a deduplication key from the cleaned generic name."""
    return ((entry.generic_name or "").strip().lower(),)


def _split_sources(source_pdf: Optional[str]) -> list[str]:
    """Split a comma-separated source string into cleaned source values."""
    if not source_pdf:
        return []
    return [source.strip() for source in source_pdf.split(",") if source.strip()]


def _merge_sources(existing: Optional[str], incoming: Optional[str]) -> str:
    """Merge source strings while preserving first-seen order."""
    merged: list[str] = []
    seen: set[str] = set()
    for source in _split_sources(existing) + _split_sources(incoming):
        source_key = source.lower()
        if source_key in seen:
            continue
        seen.add(source_key)
        merged.append(source)
    return ", ".join(merged)


def deduplicate_entries(
    entries: list[BannedMedicineEntry],
) -> list[BannedMedicineEntry]:
    """
    Remove duplicate entries based on cleaned ``generic_name``.

    The first occurrence is kept as the canonical record. Subsequent duplicate
    source domains are merged into ``source_pdf`` so multi-state recall/NSQ
    reports stay visible without creating duplicate database rows.
    """
    seen: dict[tuple, BannedMedicineEntry] = {}
    unique: list[BannedMedicineEntry] = []
    duplicates = 0

    for entry in entries:
        key = _dedup_key(entry)
        existing = seen.get(key)
        if existing:
            duplicates += 1
            existing.source_pdf = _merge_sources(existing.source_pdf, entry.source_pdf)
            logger.debug(
                "Duplicate merged: %s | sources=%s",
                entry.generic_name,
                existing.source_pdf,
            )
            continue
        seen[key] = entry
        unique.append(entry)

    if duplicates:
        logger.info(
            "Deduplication: %d duplicates merged, %d unique entries remain.",
            duplicates,
            len(unique),
        )
    return unique


def normalize_entry(entry: BannedMedicineEntry) -> BannedMedicineEntry:
    """Apply all normalisation functions to an entry (mutates in place)."""
    entry.generic_name = normalize_generic_name(entry.generic_name)
    entry.dosage_form = normalize_dosage_form(entry.dosage_form)
    entry.strength = normalize_strength(entry.strength)
    entry.notification_number = normalize_notification_number(
        entry.notification_number
    )
    # Normalise each brand name
    entry.brand_names = [
        normalize_generic_name(b) for b in entry.brand_names if b.strip()
    ]
    return entry


class DataValidator:
    """Class wrapper for validation, normalization, and deduplication logic."""

    @staticmethod
    def validate_notification_entry(entry: BannedMedicineEntry) -> ValidationResult:
        """Validate a single parsed notification entry."""
        return validate_medicine_entry(entry)

    @staticmethod
    def normalize_data(entry: BannedMedicineEntry) -> BannedMedicineEntry:
        """Normalize an entry's fields in-place."""
        return normalize_entry(entry)

    @staticmethod
    def deduplicate_entries(entries: list[BannedMedicineEntry]) -> list[BannedMedicineEntry]:
        """Deduplicate a list of entries by generic name."""
        return deduplicate_entries(entries)

