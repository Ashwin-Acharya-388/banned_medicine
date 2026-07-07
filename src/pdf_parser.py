"""
PDF parsing module for the Indian Banned Medicines Data Pipeline.

Extracts structured medicine-ban data from downloaded government
notification PDFs. Handles the variable formatting found in official
Indian gazette notifications (tables, bulleted lists, paragraph text).

Uses ``pdfplumber`` as the primary extraction engine (best for tables)
with ``PyPDF2`` as a fallback for simpler text extraction.

**Note on scanned/image-only PDFs**: These cannot be parsed with text-
extraction libraries alone. Such files are flagged with
``parsing_status='needs_review'`` for manual processing.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from src.validators import BannedMedicineEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for extraction
# ---------------------------------------------------------------------------

# G.S.R. notification numbers — e.g. "G.S.R. 578(E)" or "G.S.R.578 (E)"
GSR_PATTERN = re.compile(
    r"G\.?\s*S\.?\s*R\.?\s*(\d+)\s*\(?\s*E\s*\)?",
    re.IGNORECASE,
)

# S.O. notification numbers — e.g. "S.O. 1432(E)"
SO_PATTERN = re.compile(
    r"S\.?\s*O\.?\s*(\d+)\s*\(?\s*E\s*\)?",
    re.IGNORECASE,
)

# Date patterns commonly found in Indian government notifications
DATE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # dd/mm/yyyy or dd-mm-yyyy
    (re.compile(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})"), "%d/%m/%Y"),
    # dd.mm.yyyy
    (re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})"), "%d.%m.%Y"),
    # Month dd, yyyy  (e.g. "January 10, 2018")
    (
        re.compile(
            r"(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})",
            re.IGNORECASE,
        ),
        "%B %d %Y",
    ),
    # dd Month yyyy  (e.g. "10 January 2018")
    (
        re.compile(
            r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|"
            r"September|October|November|December),?\s+(\d{4})",
            re.IGNORECASE,
        ),
        "%d %B %Y",
    ),
    # ddth Month, yyyy (e.g. "10th January, 2018")
    (
        re.compile(
            r"(\d{1,2})(?:st|nd|rd|th)\s+(January|February|March|April|May|June|July|"
            r"August|September|October|November|December),?\s+(\d{4})",
            re.IGNORECASE,
        ),
        "%d %B %Y",
    ),
]

# Dosage form keywords (used to extract dosage_form from text)
DOSAGE_FORM_PATTERN = re.compile(
    r"\b(tablet|capsule|syrup|suspension|injection|cream|ointment|gel|"
    r"drops?|lotion|powder|solution|inhaler|spray|patch|suppository|"
    r"respules|oral\s+liquid|dry\s+syrup|infusion|emulsion|"
    r"eye\s+drops?|ear\s+drops?|nasal\s+drops?)\b",
    re.IGNORECASE,
)

# Strength patterns (e.g. "500 mg", "250mg/5ml", "10%", "0.05%")
STRENGTH_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?\s*(?:mg|g|mcg|ml|iu)\b"
    r"(?:\s*/\s*\d+(?:\.\d+)?\s*(?:mg|g|mcg|ml|iu)\b)?)"
    r"|(\d+(?:\.\d+)?\s*%)",
    re.IGNORECASE,
)

# FDC separator patterns — used to split compound drug names
FDC_SEPARATORS = re.compile(r"\s*[+&]\s*|\s+and\s+|\s*\+\s*", re.IGNORECASE)

# Table row-like pattern: serial number followed by text
TABLE_ROW_PATTERN = re.compile(
    r"^\s*(\d{1,4})\s*[.\)]\s*(.+)", re.MULTILINE
)


# ---------------------------------------------------------------------------
# Text extraction backends
# ---------------------------------------------------------------------------


def _extract_text_pdfplumber(filepath: Path) -> Optional[str]:
    """Extract text using pdfplumber (better for tabular data)."""
    try:
        import pdfplumber

        text_parts: list[str] = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)

                # Also try extracting tables
                tables = page.extract_tables()
                for table in tables:
                    for row in table:
                        if row:
                            cleaned = [
                                str(cell).strip() if cell else ""
                                for cell in row
                            ]
                            text_parts.append(" | ".join(cleaned))

        full_text = "\n".join(text_parts)
        if full_text.strip():
            return full_text
        return None

    except Exception as exc:
        logger.warning("pdfplumber failed for %s: %s", filepath.name, exc)
        return None


def _extract_text_pypdf2(filepath: Path) -> Optional[str]:
    """Extract text using PyPDF2 (fallback)."""
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(filepath)
        text_parts: list[str] = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)

        full_text = "\n".join(text_parts)
        if full_text.strip():
            return full_text
        return None

    except Exception as exc:
        logger.warning("PyPDF2 failed for %s: %s", filepath.name, exc)
        return None


def extract_text(filepath: Path) -> Optional[str]:
    """
    Extract text from a PDF file using the best available backend.

    Tries ``pdfplumber`` first (better for tables), falls back to ``PyPDF2``.
    Returns ``None`` if no text can be extracted (likely a scanned PDF).
    """
    text = _extract_text_pdfplumber(filepath)
    if text:
        logger.debug("Text extracted via pdfplumber from %s", filepath.name)
        return text

    text = _extract_text_pypdf2(filepath)
    if text:
        logger.debug("Text extracted via PyPDF2 from %s", filepath.name)
        return text

    logger.warning(
        "No text extracted from %s — may be a scanned/image PDF.", filepath.name
    )
    return None


# ---------------------------------------------------------------------------
# Metadata extraction helpers
# ---------------------------------------------------------------------------


def extract_notification_number(text: str) -> Optional[str]:
    """
    Extract the G.S.R. or S.O. notification number from the text.

    Returns the first match found.
    """
    match = GSR_PATTERN.search(text)
    if match:
        return f"G.S.R. {match.group(1)}(E)"

    match = SO_PATTERN.search(text)
    if match:
        return f"S.O. {match.group(1)}(E)"

    return None


def extract_notification_date(text: str) -> Optional[date]:
    """
    Extract the notification date from the text.

    Tries multiple date formats commonly used in Indian government documents.
    Returns the first valid date found.
    """
    for pattern, fmt in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            date_str = match.group(0)
            # Clean up ordinal suffixes
            date_str = re.sub(r"(\d)(st|nd|rd|th)", r"\1", date_str)
            # Remove extra commas
            date_str = date_str.replace(",", "")
            # Normalise separators for the first format
            date_str = date_str.replace("-", "/")
            try:
                parsed = datetime.strptime(date_str.strip(), fmt)
                return parsed.date()
            except ValueError:
                continue
    return None


def extract_dosage_form(text: str) -> Optional[str]:
    """Extract the dosage form from a text snippet."""
    match = DOSAGE_FORM_PATTERN.search(text)
    return match.group(0).strip().title() if match else None


def extract_strength(text: str) -> Optional[str]:
    """Extract the drug strength/concentration from a text snippet."""
    match = STRENGTH_PATTERN.search(text)
    return match.group(0).strip() if match else None


def extract_ban_reason(text: str) -> Optional[str]:
    """
    Attempt to extract the grounds for prohibition from the text.

    Looks for common preamble phrases used in Indian gazette notifications.
    """
    reason_patterns = [
        re.compile(
            r"(?:reason|ground|basis|because|whereas|in\s+the\s+interest\s+of\s+public\s+health)"
            r"[:\s]+(.{20,500}?)(?:\.\s|$)",
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            r"(?:no\s+therapeutic\s+justification|risk\s+to\s+human|"
            r"irrational|unsafe|hazardous|likely\s+to\s+involve\s+risk)"
            r".{0,300}?(?:\.\s|$)",
            re.IGNORECASE | re.DOTALL,
        ),
    ]
    for pat in reason_patterns:
        match = pat.search(text)
        if match:
            reason = match.group(0).strip()
            # Clean up
            reason = re.sub(r"\s+", " ", reason)
            return reason[:500]  # cap at 500 chars
    return None


# ---------------------------------------------------------------------------
# Entry-level parsers
# ---------------------------------------------------------------------------


def _parse_table_rows(text: str, notification_number: Optional[str],
                      notification_date: Optional[date],
                      source_pdf: str) -> list[BannedMedicineEntry]:
    """
    Parse entries from text that appears to be in a numbered-table format.

    Each row typically looks like:
        1. Drug Name (dosage form) strength
        2. Drug A + Drug B (tablet) 500mg/250mg
    """
    entries: list[BannedMedicineEntry] = []

    for match in TABLE_ROW_PATTERN.finditer(text):
        row_text = match.group(2).strip()
        if len(row_text) < 3:
            continue

        entry = BannedMedicineEntry(
            notification_number=notification_number,
            notification_date=notification_date,
            source_pdf=source_pdf,
        )

        # Extract dosage form and strength from the row
        entry.dosage_form = extract_dosage_form(row_text)
        entry.strength = extract_strength(row_text)

        # Remove dosage form and strength from the name
        name_text = row_text
        if entry.dosage_form:
            name_text = re.sub(
                re.escape(entry.dosage_form), "", name_text, flags=re.IGNORECASE
            ).strip()
        if entry.strength:
            name_text = re.sub(
                re.escape(entry.strength), "", name_text, flags=re.IGNORECASE
            ).strip()

        # Clean up parentheses, slashes, extra punctuation from the name
        name_text = re.sub(r"[()[\]{}]", "", name_text).strip()
        name_text = re.sub(r"\s+", " ", name_text).strip()
        name_text = name_text.rstrip(".,;:-")

        if name_text:
            entry.generic_name = name_text
            entry.raw_text = row_text
            entries.append(entry)

    return entries


def _parse_fdc_entries(text: str, notification_number: Optional[str],
                       notification_date: Optional[date],
                       source_pdf: str) -> list[BannedMedicineEntry]:
    """
    Parse Fixed-Dose Combination (FDC) entries.

    FDC entries typically list multiple drug components separated by '+' or '&'.
    The entire combination is treated as a single ``generic_name``.
    """
    entries: list[BannedMedicineEntry] = []

    # Look for lines that contain FDC-like patterns (drug + drug)
    fdc_line_pattern = re.compile(
        r"^.*?\b\w+\s*\+\s*\w+.*$", re.MULTILINE
    )

    for match in fdc_line_pattern.finditer(text):
        line = match.group(0).strip()
        if len(line) < 5:
            continue

        entry = BannedMedicineEntry(
            notification_number=notification_number,
            notification_date=notification_date,
            source_pdf=source_pdf,
        )

        entry.dosage_form = extract_dosage_form(line)
        entry.strength = extract_strength(line)

        # The generic name is the full FDC combination
        name_text = line
        if entry.dosage_form:
            name_text = re.sub(
                re.escape(entry.dosage_form), "", name_text, flags=re.IGNORECASE
            ).strip()
        if entry.strength:
            name_text = re.sub(
                re.escape(entry.strength), "", name_text, flags=re.IGNORECASE
            ).strip()

        name_text = re.sub(r"[()[\]{}]", "", name_text).strip()
        name_text = re.sub(r"^\d+\s*[.\)]\s*", "", name_text)  # remove serial no.
        name_text = re.sub(r"\s+", " ", name_text).strip()
        name_text = name_text.rstrip(".,;:-")

        if name_text and "+" in name_text:
            entry.generic_name = name_text
            entry.raw_text = line
            entries.append(entry)

    return entries


def _parse_paragraph_text(text: str, notification_number: Optional[str],
                          notification_date: Optional[date],
                          source_pdf: str) -> list[BannedMedicineEntry]:
    """
    Fallback parser for unstructured paragraph text.

    Splits on sentence/line boundaries and tries to identify drug names.
    This is less accurate and entries are flagged as ``needs_review``.
    """
    entries: list[BannedMedicineEntry] = []

    # Split into sentences
    sentences = re.split(r"[.;\n]", text)

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 10:
            continue

        # Look for drug-name-like patterns (capitalised words)
        # This is heuristic and intentionally conservative
        drug_pattern = re.compile(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b"
        )
        matches = drug_pattern.findall(sentence)

        for drug_name in matches:
            if len(drug_name) < 4:
                continue
            # Skip common non-drug words
            skip_words = {
                "The", "This", "That", "These", "Those", "India",
                "Central", "Government", "Ministry", "Health",
                "Whereas", "Therefore", "Section", "Schedule",
                "Gazette", "Notification", "New Delhi", "Date",
            }
            if drug_name in skip_words:
                continue

            entry = BannedMedicineEntry(
                generic_name=drug_name,
                notification_number=notification_number,
                notification_date=notification_date,
                source_pdf=source_pdf,
                dosage_form=extract_dosage_form(sentence),
                strength=extract_strength(sentence),
                parsing_status="needs_review",
                raw_text=sentence[:500],
            )
            entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Main parser class
# ---------------------------------------------------------------------------


class BanNotificationParser:
    """
    Parses ban-notification PDFs and returns structured medicine entries.

    Tries multiple parsing strategies in order of reliability:
    1. Table-row parsing (most reliable for numbered lists)
    2. FDC-specific parsing (for fixed-dose combination entries)
    3. Paragraph text parsing (fallback, flagged for review)
    """

    def parse_pdf(self, filepath: Path) -> list[BannedMedicineEntry]:
        """
        Parse a single PDF file and return a list of extracted entries.

        Parameters
        ----------
        filepath : Path
            Path to the PDF file.

        Returns
        -------
        list[BannedMedicineEntry]
            Extracted and partially normalised entries.
            Entries that could not be parsed reliably have
            ``parsing_status='needs_review'``.
        """
        logger.info("Parsing PDF: %s", filepath.name)

        text = extract_text(filepath)
        if text is None:
            logger.warning(
                "No text could be extracted from %s — flagging for review.",
                filepath.name,
            )
            return [
                BannedMedicineEntry(
                    generic_name="[UNREADABLE PDF]",
                    source_pdf=filepath.name,
                    parsing_status="needs_review",
                    raw_text=f"No text extracted from {filepath.name}",
                )
            ]

        return self.parse_text(text, source_pdf=filepath.name)

    def parse_text(
        self,
        text: str,
        source_pdf: str = "unknown",
    ) -> list[BannedMedicineEntry]:
        """
        Parse raw text (already extracted from a PDF) into medicine entries.

        This method is also useful for testing with sample text.
        """
        # Extract global metadata
        notification_number = extract_notification_number(text)
        notification_date = extract_notification_date(text)
        ban_reason = extract_ban_reason(text)

        logger.debug(
            "Metadata — notification: %s, date: %s",
            notification_number,
            notification_date,
        )

        entries: list[BannedMedicineEntry] = []

        # Strategy 1: Table rows (numbered list)
        table_entries = _parse_table_rows(
            text, notification_number, notification_date, source_pdf
        )
        if table_entries:
            logger.info(
                "Table parser found %d entries in %s",
                len(table_entries),
                source_pdf,
            )
            entries.extend(table_entries)

        # Strategy 2: FDC entries
        fdc_entries = _parse_fdc_entries(
            text, notification_number, notification_date, source_pdf
        )
        if fdc_entries:
            logger.info(
                "FDC parser found %d entries in %s",
                len(fdc_entries),
                source_pdf,
            )
            # Only add FDC entries not already captured by table parser
            existing_names = {e.generic_name.lower() for e in entries}
            for fdc in fdc_entries:
                if fdc.generic_name.lower() not in existing_names:
                    entries.append(fdc)

        # Strategy 3: Paragraph fallback (only if no structured entries found)
        if not entries:
            logger.info(
                "No structured entries found in %s — trying paragraph parser.",
                source_pdf,
            )
            para_entries = _parse_paragraph_text(
                text, notification_number, notification_date, source_pdf
            )
            entries.extend(para_entries)

        # Apply ban reason to all entries if found globally
        if ban_reason:
            for entry in entries:
                if not entry.ban_reason:
                    entry.ban_reason = ban_reason

        if not entries:
            logger.warning(
                "No entries extracted from %s — creating placeholder for review.",
                source_pdf,
            )
            entries.append(
                BannedMedicineEntry(
                    generic_name="[NO ENTRIES FOUND]",
                    source_pdf=source_pdf,
                    parsing_status="needs_review",
                    raw_text=text[:2000],
                )
            )

        logger.info(
            "Total entries extracted from %s: %d (review needed: %d)",
            source_pdf,
            len(entries),
            sum(1 for e in entries if e.parsing_status == "needs_review"),
        )
        return entries
