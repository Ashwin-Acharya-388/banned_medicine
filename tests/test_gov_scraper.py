"""
Unit and mock tests for the government website scrapers (State FDA, AYUSH, FSSAI).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pytest

from src import config
from src.gov_scraper import (
    AYUSHScraper,
    FSSAIScraper,
    run_all_gov_scrapers,
)
from src.validators import BannedMedicineEntry


# ---------------------------------------------------------------------------
# Mock HTML Responses
# ---------------------------------------------------------------------------

STATE_FDA_MOCK_TABLE = """
<html>
<body>
    <p>Drug alert report — batch recalls, manufacturer notices, not of standard quality drugs</p>
    <table>
        <tr>
            <th>Sr. No.</th>
            <th>Drug Name</th>
            <th>Notification Details</th>
        </tr>
        <tr>
            <td>1</td>
            <td>Nimesulide Paracetamol Suspension</td>
            <td>GSR NO. 304(E) Dated 23.07.1983</td>
        </tr>
        <tr>
            <td>2</td>
            <td>Alprazolam Tablets</td>
            <td>GSR NO. 578(E) Dated 01-10-1983</td>
        </tr>
    </table>
</body>
</html>
"""

STATE_FDA_MOCK_LIST = """
<html>
<body>
    <p>Drug recall alerts — batch sample report, manufacturer not of standard quality</p>
    <ul>
        <li>1. Nimesulide Suspension - Recalled on safety concerns (GSR NO. 304(E) Dated 23.07.1983)</li>
        <li>2. Phenacetin Tablets - Stop use warning (GSR NO. 578(E) Dated 01-10-1983)</li>
    </ul>
</body>
</html>
"""

AYUSH_MOCK_LIST = """
<html>
<body>
    <ol>
        <li>Advisory: Prohibition of Ashwagandha leaves (Withania somnifera) - Circular No. 123 Dated 23.07.1983</li>
        <li>Prohibition of certain herbs in formulations - Circular No. 456 Dated 01-10-1983</li>
    </ol>
</body>
</html>
"""

FSSAI_MOCK_TABLE = """
<html>
<body>
    <table>
        <tr>
            <th>Sr No</th>
            <th>Prohibited Botanical / Substance</th>
            <th>Regulation Reference</th>
        </tr>
        <tr>
            <td>1</td>
            <td>Para-amino benzoic acid (PABA)</td>
            <td>FSS Regulation 2022 Dated 23.07.1983</td>
        </tr>
        <tr>
            <td>2</td>
            <td>Vanadium</td>
            <td>FSS Regulation 2022 Dated 01-10-1983</td>
        </tr>
    </table>
</body>
</html>
"""





# ---------------------------------------------------------------------------
# Test Cases — AYUSHScraper
# ---------------------------------------------------------------------------

class TestAYUSHScraper:
    @patch("src.gov_scraper.BaseGovScraper._request_with_retries")
    def test_ayush_list_parsing(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.text = AYUSH_MOCK_LIST
        mock_request.return_value = mock_resp

        scraper = AYUSHScraper()
        entries = scraper.scrape()

        assert len(entries) == 2
        assert entries[0].generic_name == "Ashwagandha leaves (Withania somnifera)"
        assert entries[0].notification_date == date(1983, 7, 23)
        assert entries[0].source_pdf == "ayush.gov.in"

        assert entries[1].generic_name == "certain herbs in formulations"
        assert entries[1].notification_date == date(1983, 10, 1)


# ---------------------------------------------------------------------------
# Test Cases — FSSAIScraper
# ---------------------------------------------------------------------------

class TestFSSAIScraper:
    @patch("src.gov_scraper.BaseGovScraper._request_with_retries")
    def test_fssai_table_parsing(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.text = FSSAI_MOCK_TABLE
        mock_request.return_value = mock_resp

        scraper = FSSAIScraper()
        entries = scraper.scrape()

        assert len(entries) == 2
        assert entries[0].generic_name == "Para-amino benzoic acid (PABA)"
        assert entries[0].notification_date == date(1983, 7, 23)
        assert entries[0].source_pdf == "fssai.gov.in"

        assert entries[1].generic_name == "Vanadium"
        assert entries[1].notification_date == date(1983, 10, 1)


# ---------------------------------------------------------------------------
# Test run_all_gov_scrapers
# ---------------------------------------------------------------------------

@patch("src.gov_scraper.AYUSHScraper.scrape")
@patch("src.gov_scraper.FSSAIScraper.scrape")
def test_run_all_gov_scrapers(mock_fssai, mock_ayush):
    mock_ayush.return_value = [BannedMedicineEntry(generic_name="AYUSHDrug")]
    mock_fssai.return_value = [BannedMedicineEntry(generic_name="FSSAIDrug")]

    entries = run_all_gov_scrapers()
    assert len(entries) == 2
    assert entries[0].generic_name == "AYUSHDrug"
    assert entries[1].generic_name == "FSSAIDrug"
