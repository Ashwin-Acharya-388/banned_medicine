"""
Tests for the validators module.

Covers:
- Entry validation (required fields, date checks, dosage form recognition)
- Normalization of generic names, dosage forms, strengths
- Batch deduplication logic
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.validators import (
    BannedMedicineEntry,
    ValidationResult,
    deduplicate_entries,
    normalize_dosage_form,
    normalize_entry,
    normalize_generic_name,
    normalize_notification_number,
    normalize_strength,
    validate_medicine_entry,
)


# ---------------------------------------------------------------------------
# normalize_generic_name
# ---------------------------------------------------------------------------


class TestNormalizeGenericName:
    def test_basic_cleanup(self):
        assert normalize_generic_name("  paracetamol  ") == "Paracetamol"

    def test_title_case(self):
        assert normalize_generic_name("ACECLOFENAC") == "Aceclofenac"

    def test_collapse_whitespace(self):
        result = normalize_generic_name("some   drug   name")
        assert result == "Some Drug Name"

    def test_strip_trailing_punctuation(self):
        assert normalize_generic_name("Paracetamol.") == "Paracetamol"
        assert normalize_generic_name("Ibuprofen,") == "Ibuprofen"

    def test_empty_string(self):
        assert normalize_generic_name("") == ""

    def test_roman_numerals_preserved(self):
        result = normalize_generic_name("factor viii")
        assert "VIII" in result


# ---------------------------------------------------------------------------
# normalize_dosage_form
# ---------------------------------------------------------------------------


class TestNormalizeDosageForm:
    def test_tablet_from_abbreviation(self):
        assert normalize_dosage_form("tab") == "Tablet"

    def test_tablet_from_full_name(self):
        assert normalize_dosage_form("tablet") == "Tablet"

    def test_capsule(self):
        assert normalize_dosage_form("cap") == "Capsule"

    def test_injection(self):
        assert normalize_dosage_form("inj") == "Injection"

    def test_syrup(self):
        assert normalize_dosage_form("syr") == "Syrup"

    def test_unknown_form(self):
        result = normalize_dosage_form("nebulizer")
        assert result == "Nebulizer"  # title-cased but not mapped

    def test_none_input(self):
        assert normalize_dosage_form(None) is None

    def test_empty_string(self):
        assert normalize_dosage_form("") is None


# ---------------------------------------------------------------------------
# normalize_strength
# ---------------------------------------------------------------------------


class TestNormalizeStrength:
    def test_add_space_between_number_and_unit(self):
        result = normalize_strength("500mg")
        assert result == "500 mg"

    def test_already_spaced(self):
        result = normalize_strength("500 mg")
        assert result == "500 mg"

    def test_none_input(self):
        assert normalize_strength(None) is None


# ---------------------------------------------------------------------------
# normalize_notification_number
# ---------------------------------------------------------------------------


class TestNormalizeNotificationNumber:
    def test_standard_gsr(self):
        result = normalize_notification_number("G.S.R. 578 (E)")
        assert result == "G.S.R. 578(E)"

    def test_extra_spaces(self):
        result = normalize_notification_number("G.S.R.  578  ( E )")
        assert result == "G.S.R. 578(E)"

    def test_none_input(self):
        assert normalize_notification_number(None) is None


# ---------------------------------------------------------------------------
# validate_medicine_entry
# ---------------------------------------------------------------------------


class TestValidateMedicineEntry:
    def test_valid_entry(self):
        entry = BannedMedicineEntry(
            generic_name="Paracetamol",
            dosage_form="Tablet",
            strength="500 mg",
            notification_number="G.S.R. 578(E)",
            notification_date=date(2018, 9, 10),
        )
        result = validate_medicine_entry(entry)
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_empty_generic_name_fails(self):
        entry = BannedMedicineEntry(generic_name="")
        result = validate_medicine_entry(entry)
        assert result.is_valid is False
        assert any("generic_name" in e for e in result.errors)

    def test_whitespace_generic_name_fails(self):
        entry = BannedMedicineEntry(generic_name="   ")
        result = validate_medicine_entry(entry)
        assert result.is_valid is False

    def test_future_date_fails(self):
        future = date.today() + timedelta(days=365)
        entry = BannedMedicineEntry(
            generic_name="FutureDrug",
            notification_date=future,
        )
        result = validate_medicine_entry(entry)
        assert result.is_valid is False
        assert any("future" in e for e in result.errors)

    def test_unrecognised_dosage_form_warns(self):
        entry = BannedMedicineEntry(
            generic_name="TestDrug",
            dosage_form="Nebulizer",
        )
        result = validate_medicine_entry(entry)
        assert result.is_valid is True  # warning, not error
        assert len(result.warnings) > 0

    def test_bad_notification_number_warns(self):
        entry = BannedMedicineEntry(
            generic_name="TestDrug",
            notification_number="XYZ-999",
        )
        result = validate_medicine_entry(entry)
        assert result.is_valid is True  # warning, not error
        assert len(result.warnings) > 0

    def test_none_date_is_ok(self):
        entry = BannedMedicineEntry(generic_name="TestDrug")
        result = validate_medicine_entry(entry)
        assert result.is_valid is True

    def test_blacklisted_month_name_fails(self):
        entry = BannedMedicineEntry(generic_name="January")
        result = validate_medicine_entry(entry)
        assert result.is_valid is False
        assert any("blacklisted" in e for e in result.errors)

    def test_blacklisted_admin_term_fails(self):
        for name in ["Total Queries", "Fact Check", "Till", "Actionable Queries"]:
            entry = BannedMedicineEntry(generic_name=name)
            result = validate_medicine_entry(entry)
            assert result.is_valid is False, f"{name!r} should be rejected"

    def test_administrative_navigation_keywords_fail(self):
        names = [
            "Labour Department",
            "Home Department",
            "Information Technology Department",
            "Grievance Service",
            "Contact Officer",
            "Tender Advertisement",
            "Login",
        ]
        for name in names:
            entry = BannedMedicineEntry(generic_name=name)
            result = validate_medicine_entry(entry)
            assert result.is_valid is False, f"{name!r} should be rejected"
            assert any("administrative/navigation" in e for e in result.errors)

    def test_domain_name_fails(self):
        entry = BannedMedicineEntry(generic_name="nchmr.com")
        result = validate_medicine_entry(entry)
        assert result.is_valid is False
        assert any("domain" in e or "blacklisted" in e for e in result.errors)

    def test_url_fails(self):
        entry = BannedMedicineEntry(generic_name="http://example.com/drug")
        result = validate_medicine_entry(entry)
        assert result.is_valid is False

    def test_too_short_name_fails(self):
        entry = BannedMedicineEntry(generic_name="AB")
        result = validate_medicine_entry(entry)
        assert result.is_valid is False
        assert any("short" in e for e in result.errors)

    def test_all_numeric_fails(self):
        entry = BannedMedicineEntry(generic_name="12345")
        result = validate_medicine_entry(entry)
        assert result.is_valid is False
        assert any("numeric" in e for e in result.errors)

    def test_valid_drug_name_with_numbers_passes(self):
        entry = BannedMedicineEntry(generic_name="Amoxicillin 500mg + Clavulanic Acid 125mg")
        result = validate_medicine_entry(entry)
        assert result.is_valid is True

    def test_expanded_admin_keywords_fail(self):
        """All newly-added admin/navigation terms should be rejected."""
        names = [
            "Who Is Who",
            "Organization Chart",
            "Government Orders",
            "Blood Bank Licence",
            "Citizen Charter",
            "Forms and Applications",
            "Screen Reader Access",
            "Skip To Navigation",
            "Officers Directory",
            "Photo Gallery Events",
            "Jobs And Recruitment",
            "Standard Treatment Guidelines",
            "Know Your Drugs Controller",
        ]
        for name in names:
            entry = BannedMedicineEntry(generic_name=name)
            result = validate_medicine_entry(entry)
            assert result.is_valid is False, f"{name!r} should be rejected"

    def test_archive_labels_fail(self):
        """Calendar archive labels like 'January (72)' should be rejected."""
        names = [
            "January (72)",
            "February (8)",
            "December (120)",
            "March (3)",
        ]
        for name in names:
            entry = BannedMedicineEntry(generic_name=name)
            result = validate_medicine_entry(entry)
            assert result.is_valid is False, f"{name!r} should be rejected"


# ---------------------------------------------------------------------------
# deduplicate_entries
# ---------------------------------------------------------------------------


class TestDeduplicateEntries:
    def test_no_duplicates(self):
        entries = [
            BannedMedicineEntry(generic_name="Drug A", dosage_form="Tablet", strength="500 mg"),
            BannedMedicineEntry(generic_name="Drug B", dosage_form="Capsule", strength="250 mg"),
        ]
        result = deduplicate_entries(entries)
        assert len(result) == 2

    def test_remove_exact_duplicates(self):
        entries = [
            BannedMedicineEntry(generic_name="Drug A", dosage_form="Tablet", strength="500 mg"),
            BannedMedicineEntry(generic_name="Drug A", dosage_form="Tablet", strength="500 mg"),
        ]
        result = deduplicate_entries(entries)
        assert len(result) == 1

    def test_case_insensitive_dedup(self):
        entries = [
            BannedMedicineEntry(generic_name="Drug A", dosage_form="Tablet", strength="500 mg"),
            BannedMedicineEntry(generic_name="drug a", dosage_form="tablet", strength="500 mg"),
        ]
        result = deduplicate_entries(entries)
        assert len(result) == 1

    def test_same_generic_with_different_strength_is_deduped(self):
        entries = [
            BannedMedicineEntry(generic_name="Drug A", dosage_form="Tablet", strength="500 mg"),
            BannedMedicineEntry(generic_name="Drug A", dosage_form="Tablet", strength="250 mg"),
        ]
        result = deduplicate_entries(entries)
        assert len(result) == 1

    def test_duplicate_sources_are_merged(self):
        entries = [
            BannedMedicineEntry(generic_name="Drug A", source_pdf="fda.maharashtra.gov.in"),
            BannedMedicineEntry(generic_name="drug a", source_pdf="dc.kerala.gov.in"),
            BannedMedicineEntry(generic_name="Drug A", source_pdf="fda.maharashtra.gov.in"),
        ]
        result = deduplicate_entries(entries)
        assert len(result) == 1
        assert result[0].source_pdf == "fda.maharashtra.gov.in, dc.kerala.gov.in"

    def test_empty_list(self):
        assert deduplicate_entries([]) == []


# ---------------------------------------------------------------------------
# normalize_entry (integration)
# ---------------------------------------------------------------------------


class TestNormalizeEntry:
    def test_full_normalization(self):
        entry = BannedMedicineEntry(
            generic_name="  paracetamol  ",
            dosage_form="tab",
            strength="500mg",
            notification_number="G.S.R.  578  ( E )",
            brand_names=["  Crocin  ", "  Dolo  "],
        )
        result = normalize_entry(entry)
        assert result.generic_name == "Paracetamol"
        assert result.dosage_form == "Tablet"
        assert result.strength == "500 mg"
        assert result.notification_number == "G.S.R. 578(E)"
        assert "Crocin" in result.brand_names
        assert "Dolo" in result.brand_names
