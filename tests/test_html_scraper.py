"""
Unit and integration tests for the HTML aggregator scrapers (Vaayath, TheHealthMaster).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch, MagicMock

import pytest
from bs4 import BeautifulSoup

from src.html_scraper import (
    parse_notification_field,
    VaayathScraper,
    TheHealthMasterScraper,
    run_all_html_scrapers,
)
from src.validators import BannedMedicineEntry


# ---------------------------------------------------------------------------
# Test parse_notification_field
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "input_text,expected_num,expected_date",
    [
        ("GSR NO. 578(E) Dated 23.07.1983", "G.S.R. 578(E)", date(1983, 7, 23)),
        ("GSR NO. 999 (E) Dated 01-10-1983", "G.S.R. 999(E)", date(1983, 10, 1)),
        ("Substituted vide GSR NO. 793(E) Dated 01.10.1983", "G.S.R. 793(E)", date(1983, 10, 1)),
        ("G.S.R. 304(E) Dated 23.07.1983", "G.S.R. 304(E)", date(1983, 7, 23)),
        ("S.O. 123 (E) Dated January 15, 2020", "S.O. 123(E)", date(2020, 1, 15)),
        ("No notification details", None, None),
        ("", None, None),
        ("GSR NO. 578(E) without date", "G.S.R. 578(E)", None),
        ("Dated 23.07.1983 without number", None, date(1983, 7, 23)),
    ]
)
def test_parse_notification_field(input_text, expected_num, expected_date):
    num, dt = parse_notification_field(input_text)
    assert num == expected_num
    assert dt == expected_date


# ---------------------------------------------------------------------------
# Mock HTML responses
# ---------------------------------------------------------------------------

VAAYATH_MOCK_HTML = """
<html>
<body>
    <table>
        <tr>
            <th>Sr. No.</th>
            <th>Drugs Name</th>
            <th>Notification  No.  & Date</th>
        </tr>
        <tr>
            <td>1</td>
            <td>Amidopyrine.</td>
            <td>GSR NO. 578(E) Dated 23.07.1983</td>
        </tr>
        <tr>
            <td>2</td>
            <td>Fixed dose combinations of vitamins with anti-inflammatory agents</td>
            <td>GSR NO. 578(E) Dated 23.07.1983</td>
        </tr>
    </table>
</body>
</html>
"""

HEALTHMASTER_MOCK_HTML = """
<html>
<body>
    <table>
        <tr>
            <th>Sr. No.</th>
            <th>Drugs Name</th>
            <th>NotificationNo. & Date</th>
        </tr>
        <tr>
            <td>1.</td>
            <td>Amidopyrine.</td>
            <td>GSR NO. 578(E) Dated 23.07.1983</td>
        </tr>
    </table>
    <table>
        <tr>
            <td>2.</td>
            <td>Phenacetin.</td>
            <td>GSR NO. 578(E) Dated 23.07.1983</td>
        </tr>
    </table>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Test Scrapers
# ---------------------------------------------------------------------------

class TestVaayathScraper:
    @patch("src.html_scraper.BaseHTMLScraper._request_with_retries")
    def test_vaayath_scrape(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.text = VAAYATH_MOCK_HTML
        mock_request.return_value = mock_resp

        scraper = VaayathScraper()
        entries = scraper.scrape()

        assert len(entries) == 2
        assert entries[0].generic_name == "Amidopyrine"
        assert entries[0].notification_number == "G.S.R. 578(E)"
        assert entries[0].notification_date == date(1983, 7, 23)
        assert entries[0].source_pdf == "vaayath.com"

        assert entries[1].generic_name == "Fixed dose combinations of vitamins with anti-inflammatory agents"
        assert entries[1].notification_number == "G.S.R. 578(E)"


class TestTheHealthMasterScraper:
    @patch("src.html_scraper.BaseHTMLScraper._request_with_retries")
    def test_healthmaster_scrape(self, mock_request):
        mock_resp = MagicMock()
        mock_resp.text = HEALTHMASTER_MOCK_HTML
        mock_request.return_value = mock_resp

        scraper = TheHealthMasterScraper()
        entries = scraper.scrape()

        assert len(entries) == 2
        assert entries[0].generic_name == "Amidopyrine"
        assert entries[0].notification_number == "G.S.R. 578(E)"
        assert entries[0].source_pdf == "thehealthmaster.com"

        assert entries[1].generic_name == "Phenacetin"
        assert entries[1].notification_number == "G.S.R. 578(E)"


@patch("src.html_scraper.TheHealthMasterScraper.scrape")
@patch("src.html_scraper.VaayathScraper.scrape")
def test_run_all_html_scrapers(mock_vaayath, mock_healthmaster):
    mock_vaayath.return_value = [BannedMedicineEntry(generic_name="TestDrug1")]
    mock_healthmaster.return_value = [BannedMedicineEntry(generic_name="TestDrug2")]
    entries = run_all_html_scrapers()
    assert len(entries) == 2
    assert entries[0].generic_name == "TestDrug1"
    assert entries[1].generic_name == "TestDrug2"
