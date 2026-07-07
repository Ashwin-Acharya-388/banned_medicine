"""
Pipeline tests for FDC splitting, manual review routing, and cross-referencing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import DatabaseManager, BannedMedicine, UnofficialMedicine, AyushFssaiMedicine
from src.parser import NotificationParser
from src.cross_reference import CrossReferencer
from src.validators import BannedMedicineEntry


class TestNotificationParserAndFDC:
    """Tests for the enhanced NotificationParser and FDC splitting logic."""

    def test_fdc_splitting_and_flagging(self):
        """Verify that FDC combination names are split into ingredients and flagged."""
        parser = NotificationParser(db_manager=MagicMock())
        
        # 1. Combination with +
        is_fdc, ingredients = parser.split_fdc_ingredients("Alprazolam + Propranolol")
        assert is_fdc is True
        assert ingredients == ["Alprazolam", "Propranolol"]
        
        # 2. Combination with &
        is_fdc, ingredients = parser.split_fdc_ingredients("Amoxicillin & Clavulanate Potassium")
        assert is_fdc is True
        assert ingredients == ["Amoxicillin", "Clavulanate Potassium"]
        
        # 3. Combination with and
        is_fdc, ingredients = parser.split_fdc_ingredients("Paracetamol and Ibuprofen")
        assert is_fdc is True
        assert ingredients == ["Paracetamol", "Ibuprofen"]
        
        # 4. Single drug (not FDC)
        is_fdc, ingredients = parser.split_fdc_ingredients("Gatifloxacin")
        assert is_fdc is False
        assert ingredients == ["Gatifloxacin"]

    @patch("src.parser.BanNotificationParser")
    def test_scanned_pdf_routes_to_manual_review(self, mock_ban_parser_cls):
        """Verify that scanned/unreadable PDFs route to the manual review queue."""
        # Mock base parser returning a needs_review placeholder (signifying unreadable/scanned PDF)
        mock_ban_parser = mock_ban_parser_cls.return_value
        mock_ban_parser.parse_pdf.return_value = [
            BannedMedicineEntry(
                generic_name="[UNREADABLE PDF] No readable text found.",
                parsing_status="needs_review",
                notification_number="G.S.R. 578(E)"
            )
        ]
        
        mock_db = MagicMock()
        parser = NotificationParser(db_manager=mock_db)
        
        filepath = Path("/tmp/mock_scanned.pdf")
        entries = parser.parse_pdf_notification(filepath, source_url="http://example.com/mock.pdf")
        
        # Should return no valid entries
        assert len(entries) == 0
        
        # Check that upsert_notification_processing was called with status failed
        mock_db.upsert_notification_processing.assert_called_once()
        args = mock_db.upsert_notification_processing.call_args[0][1]
        assert args["parsing_status"] == "failed"
        
        # Check that add_to_review_queue was called with issue_type scanned_pdf or similar
        mock_db.add_to_review_queue.assert_called_once()
        review_args = mock_db.add_to_review_queue.call_args[0][1]
        assert "scanned_pdf" in review_args["issue_type"] or "unreadable" in review_args["description"].lower()


class TestCrossReferencerPipeline:
    """Tests for the CrossReferencer linking and mismatch-handling engine."""

    def test_cross_reference_unlinked_vs_linked(self):
        """Test that cross_reference_all identifies linked and unlinked items correctly."""
        mock_db = MagicMock()
        session = MagicMock()
        mock_db.session_scope.return_value.__enter__.return_value = session
        
        # Setup mock official CDSCO records
        bm1 = BannedMedicine(
            generic_name="Alprazolam + Propranolol",
            notification_number="G.S.R. 578(E)",
            is_fdc=True,
            ingredients=["Alprazolam", "Propranolol"]
        )
        bm2 = BannedMedicine(
            generic_name="Gatifloxacin",
            notification_number="G.S.R. 218(E)",
            is_fdc=False,
            ingredients=["Gatifloxacin"]
        )
        
        # Setup mock unofficial records
        un1 = UnofficialMedicine(
            generic_name="Alprazolam + Propranolol",
            notification_number="G.S.R. 578(E)"
        )
        # un2 is unlinked: notification number doesn't exist
        un2 = UnofficialMedicine(
            generic_name="Some Other Drug",
            notification_number="G.S.R. 999(E)"
        )
        # un3 is unlinked: notification number matches but drug ingredients/name do not match
        un3 = UnofficialMedicine(
            generic_name="Aspirin",
            notification_number="G.S.R. 218(E)"
        )
        
        # Mock session queries
        session.query.side_effect = lambda model_cls: {
            BannedMedicine: MagicMock(all=MagicMock(return_value=[bm1, bm2])),
            UnofficialMedicine: MagicMock(all=MagicMock(return_value=[un1, un2, un3])),
            AyushFssaiMedicine: MagicMock(all=MagicMock(return_value=[]))
        }.get(model_cls, MagicMock())
        
        referencer = CrossReferencer(db_manager=mock_db)
        stats = referencer.cross_reference_all()
        
        # Verify stats
        assert stats["unofficial_total"] == 3
        assert stats["unofficial_linked"] == 1
        assert stats["unofficial_unlinked"] == 2
        
        # Verify review queue insertions for the unlinked records
        assert mock_db.add_to_review_queue.call_count == 2
