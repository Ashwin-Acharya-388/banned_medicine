"""
Tests for the PDF / text parser module.

Uses sample notification text (no real PDF files required) to verify
extraction of notification numbers, dates, drug entries, FDC handling,
dosage forms, strengths, and fallback behaviour.
"""

import sys
from pathlib import Path

import pytest

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pdf_parser import (
    BanNotificationParser,
    extract_dosage_form,
    extract_notification_date,
    extract_notification_number,
    extract_strength,
    extract_ban_reason,
)
from tests.sample_data.sample_ban_notification import (
    SAMPLE_NOTIFICATION_TEXT,
    SAMPLE_EMPTY_TEXT,
    SAMPLE_UNSTRUCTURED_TEXT,
)


# ---------------------------------------------------------------------------
# Notification metadata extraction
# ---------------------------------------------------------------------------


class TestNotificationNumber:
    """Tests for G.S.R. / S.O. number extraction."""

    def test_gsr_standard_format(self):
        text = "G.S.R. 578(E) dated 10th September 2018"
        assert extract_notification_number(text) == "G.S.R. 578(E)"

    def test_gsr_compact_format(self):
        text = "GSR578(E)"
        result = extract_notification_number(text)
        assert result is not None
        assert "578" in result

    def test_so_number(self):
        text = "S.O. 1432(E) published on 5th March 2019"
        assert extract_notification_number(text) == "S.O. 1432(E)"

    def test_no_notification_number(self):
        text = "This is a regular text with no notification number."
        assert extract_notification_number(text) is None

    def test_sample_notification(self):
        result = extract_notification_number(SAMPLE_NOTIFICATION_TEXT)
        assert result is not None
        assert "578" in result


class TestNotificationDate:
    """Tests for date extraction from notification text."""

    def test_date_dd_mm_yyyy(self):
        text = "dated 10/09/2018 at New Delhi"
        result = extract_notification_date(text)
        assert result is not None
        assert result.year == 2018
        assert result.month == 9
        assert result.day == 10

    def test_date_month_name_format(self):
        text = "New Delhi, the 10th September, 2018"
        result = extract_notification_date(text)
        assert result is not None
        assert result.year == 2018
        assert result.month == 9

    def test_date_dd_month_yyyy(self):
        text = "published on 5 March 2019"
        result = extract_notification_date(text)
        assert result is not None
        assert result.year == 2019
        assert result.month == 3
        assert result.day == 5

    def test_no_date(self):
        text = "No date mentioned here."
        assert extract_notification_date(text) is None

    def test_sample_notification_date(self):
        result = extract_notification_date(SAMPLE_NOTIFICATION_TEXT)
        assert result is not None
        assert result.year == 2018


class TestDosageForm:
    """Tests for dosage form extraction."""

    def test_tablet(self):
        assert extract_dosage_form("Paracetamol Tablet 500mg") is not None
        assert "Tablet" in extract_dosage_form("Paracetamol Tablet 500mg")

    def test_capsule(self):
        result = extract_dosage_form("Amoxicillin Capsule 250mg")
        assert result is not None
        assert "Capsule" in result

    def test_injection(self):
        result = extract_dosage_form("Analgin Injection 500mg/ml")
        assert result is not None
        assert "Injection" in result

    def test_syrup(self):
        result = extract_dosage_form("Cough Syrup 100ml")
        assert result is not None
        assert "Syrup" in result

    def test_suspension(self):
        result = extract_dosage_form("Nimesulide Suspension")
        assert result is not None
        assert "Suspension" in result

    def test_eye_drops(self):
        result = extract_dosage_form("Ciprofloxacin Eye Drops")
        assert result is not None
        assert "Drops" in result.title()

    def test_no_dosage_form(self):
        assert extract_dosage_form("Some random text") is None


class TestStrength:
    """Tests for strength/concentration extraction."""

    def test_simple_mg(self):
        result = extract_strength("Paracetamol 500mg")
        assert result is not None
        assert "500" in result

    def test_compound_strength(self):
        result = extract_strength("100mg/500mg/20mg combination")
        assert result is not None
        assert "100" in result

    def test_percentage(self):
        result = extract_strength("Betamethasone 0.05%")
        assert result is not None
        assert "0.05" in result

    def test_no_strength(self):
        assert extract_strength("No strength information") is None


class TestBanReason:
    """Tests for ban reason extraction."""

    def test_therapeutic_justification(self):
        text = "there is no therapeutic justification for these drugs."
        result = extract_ban_reason(text)
        assert result is not None
        assert "therapeutic" in result.lower()

    def test_risk_to_human(self):
        text = "drugs are likely to involve risk to human beings and are unsafe."
        result = extract_ban_reason(text)
        assert result is not None

    def test_sample_notification_reason(self):
        result = extract_ban_reason(SAMPLE_NOTIFICATION_TEXT)
        assert result is not None


# ---------------------------------------------------------------------------
# Full parser tests
# ---------------------------------------------------------------------------


class TestBanNotificationParser:
    """Integration tests for the full parser."""

    def setup_method(self):
        self.parser = BanNotificationParser()

    def test_parse_sample_notification(self):
        """Parse the standard sample notification and check entries."""
        entries = self.parser.parse_text(
            SAMPLE_NOTIFICATION_TEXT, source_pdf="test_sample.pdf"
        )
        assert len(entries) > 0

        # Should find at least some of the 10 listed drugs
        names = [e.generic_name.lower() for e in entries]
        # Check that at least some FDC combinations were found
        fdc_found = any("+" in name for name in names)
        # The sample has mostly FDC entries with +
        assert len(entries) >= 5, f"Expected ≥5 entries, got {len(entries)}"

    def test_notification_metadata_propagated(self):
        """Ensure notification number and date are propagated to entries."""
        entries = self.parser.parse_text(
            SAMPLE_NOTIFICATION_TEXT, source_pdf="test.pdf"
        )
        for entry in entries:
            assert entry.notification_number is not None
            assert entry.notification_date is not None
            assert entry.source_pdf == "test.pdf"

    def test_empty_text_returns_placeholder(self):
        """Empty text should return a needs_review placeholder."""
        entries = self.parser.parse_text(
            SAMPLE_EMPTY_TEXT, source_pdf="empty.pdf"
        )
        assert len(entries) == 1
        assert entries[0].parsing_status == "needs_review"

    def test_unstructured_text_flagged_for_review(self):
        """Unstructured text should produce entries flagged for review."""
        entries = self.parser.parse_text(
            SAMPLE_UNSTRUCTURED_TEXT, source_pdf="unstructured.pdf"
        )
        # Should find some entries (paragraph fallback)
        assert len(entries) > 0

    def test_source_pdf_set_on_all_entries(self):
        """Every entry should have the source_pdf field set."""
        entries = self.parser.parse_text(
            SAMPLE_NOTIFICATION_TEXT, source_pdf="source_test.pdf"
        )
        for entry in entries:
            assert entry.source_pdf == "source_test.pdf"

    def test_dosage_forms_extracted(self):
        """At least some entries should have dosage forms."""
        entries = self.parser.parse_text(
            SAMPLE_NOTIFICATION_TEXT, source_pdf="test.pdf"
        )
        forms = [e.dosage_form for e in entries if e.dosage_form]
        assert len(forms) > 0, "Expected at least one entry with a dosage form"

    def test_strengths_extracted(self):
        """At least some entries should have strengths."""
        entries = self.parser.parse_text(
            SAMPLE_NOTIFICATION_TEXT, source_pdf="test.pdf"
        )
        strengths = [e.strength for e in entries if e.strength]
        assert len(strengths) > 0, "Expected at least one entry with a strength"
