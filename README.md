# Indian Banned Medicines Data Pipeline

A production-ready Python pipeline that automatically scrapes official Indian government sources, downloads PDF notifications listing banned medicines, extracts structured data, and stores it in a PostgreSQL database.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Database Setup](#database-setup)
- [Usage](#usage)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Ethical & Legal Notes](#ethical--legal-notes)
- [Troubleshooting](#troubleshooting)

---

## Overview

The Central Drugs Standard Control Organisation (CDSCO) and other Indian government bodies publish notifications banning certain medicines and Fixed-Dose Combinations (FDCs) under Section 26A of the Drugs & Cosmetics Act, 1940. These notifications are typically published as PDF documents on official websites.

This pipeline automates the process of:
1. **Discovering** ban-related PDF notifications from CDSCO and PIB websites
2. **Downloading** PDFs with respectful scraping practices
3. **Extracting** structured data (drug names, dosage forms, strengths, notification details)
4. **Storing** the data in a PostgreSQL database for querying and analysis

---

## Features

- **Multi-source scraping** — CDSCO notifications page + PIB press release archive
- **Respectful scraping** — robots.txt compliance, rate limiting, user-agent rotation, exponential backoff
- **Multi-strategy PDF parsing** — Table extraction, FDC combination parsing, paragraph text fallback
- **Data validation & normalisation** — Drug name standardisation, dosage form mapping, deduplication
- **PostgreSQL storage** — SQLAlchemy ORM, connection pooling, upsert logic
- **Alembic migrations** — Version-controlled database schema
- **Incremental updates** — Only processes new/unprocessed PDFs
- **Dry-run mode** — Preview what would happen without making changes
- **Comprehensive logging** — Dual output (console + rotating file), structured format
- **Configurable** — All settings via environment variables with sensible defaults

---

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  CDSCO/PIB  │────>│   Scraper    │────>│  PDF Parser  │────>│  PostgreSQL  │
│  Websites   │     │  (scraper.py)│     │(pdf_parser.py│     │ (database.py)│
└─────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
                          │                     │                     │
                    downloads/*.pdf        Validated &           banned_medicines
                    metadata.json          normalised            source_documents
                                           entries
```

---

## Prerequisites

- **Python 3.10+**
- **PostgreSQL 13+** (running locally or remotely)
- **pip** (Python package manager)

---

## Installation

```bash
# 1. Clone the repository
git clone <repository-url>
cd banneddrugs

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate   # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and configure environment variables
cp .env.example .env
# Edit .env with your PostgreSQL credentials
```

---

## Configuration

All settings are loaded from environment variables (`.env` file). See [`.env.example`](.env.example) for the full list.

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/banned_drugs` | PostgreSQL connection string |
| `DOWNLOAD_DIR` | `./downloads` | Where PDFs are saved |
| `LOG_DIR` | `./logs` | Where log files are written |
| `SCRAPE_DELAY_MIN` | `2` | Minimum delay between requests (seconds) |
| `SCRAPE_DELAY_MAX` | `5` | Maximum delay between requests (seconds) |
| `MAX_RETRIES` | `3` | Max retry attempts for failed downloads |
| `REQUEST_TIMEOUT` | `30` | HTTP request timeout (seconds) |

---

## Database Setup

### 1. Create the database

```bash
# Connect to PostgreSQL
psql -U postgres

# Create the database
CREATE DATABASE banned_drugs;
\q
```

### 2. Run migrations

```bash
# Apply all migrations
alembic upgrade head
```

### 3. Verify

```bash
# Check tables were created
psql -U postgres -d banned_drugs -c "\dt"
```

---

## Usage

The pipeline is controlled via `src/main.py` with three operational modes:

### Full Pipeline (scrape + parse + store)

```bash
python -m src.main --update
```

### Scrape Only (download new PDFs)

```bash
python -m src.main --scrape-only
```

### Parse Only (process existing PDFs)

```bash
python -m src.main --parse-only
```

### Dry Run (preview without changes)

```bash
python -m src.main --update --dry-run
```

### Verbose Mode

```bash
python -m src.main --update --verbose
```

### Custom Download Directory

```bash
python -m src.main --update --download-dir /path/to/pdfs
```

---

## Project Structure

```
banneddrugs/
├── src/
│   ├── __init__.py
│   ├── config.py          # Configuration from environment variables
│   ├── scraper.py          # Web scraping (CDSCO + PIB)
│   ├── pdf_parser.py       # PDF text extraction & parsing
│   ├── database.py         # SQLAlchemy ORM & database operations
│   ├── main.py             # CLI orchestration script
│   └── validators.py       # Data validation & normalisation
├── alembic/
│   ├── env.py              # Alembic environment (loads DB URL from config)
│   ├── script.py.mako      # Migration template
│   └── versions/
│       └── 001_initial_schema.py  # Initial table creation
├── tests/
│   ├── test_parser.py      # Parser unit tests
│   ├── test_validators.py  # Validator unit tests
│   └── sample_data/
│       └── sample_ban_notification.py  # Mock data
├── downloads/              # Downloaded PDFs (gitignored)
├── logs/                   # Log files (gitignored)
├── alembic.ini             # Alembic configuration
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
├── .gitignore
└── README.md               # This file
```

---

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run parser tests only
python -m pytest tests/test_parser.py -v

# Run validator tests only
python -m pytest tests/test_validators.py -v

# Run with coverage (install pytest-cov first)
python -m pytest tests/ --cov=src --cov-report=term-missing
```

Tests use sample notification text and do **not** require a database connection or network access.

---

## Ethical & Legal Notes

- **Public data**: This tool scrapes *publicly available* government notifications published on official `.gov.in` websites. No authentication or paywall bypass is involved.
- **robots.txt compliance**: The scraper checks and respects `robots.txt` before making any request.
- **Rate limiting**: Random delays (2–5 seconds by default) between requests prevent overloading government servers.
- **Purpose**: This project is intended for **public health research and technology development**. It should not be used for commercial purposes that violate the terms of service of the source websites.
- **No personal data**: The scraped data consists only of medicine names, dosage forms, and regulatory notification details. No personal or patient data is collected.

---

## Troubleshooting

### CDSCO website is unreachable

The CDSCO website (`cdsco.gov.in`) is intermittently unavailable. The scraper includes retry logic with exponential backoff. If the site is down for extended periods, try again later.

### PDF cannot be parsed

Some government PDFs are scanned images rather than text-based documents. These are flagged with `parsing_status='needs_review'` in the database. Consider adding OCR (e.g., Tesseract) for these cases.

### Database connection refused

Ensure PostgreSQL is running and the `DATABASE_URL` in `.env` is correct:

```bash
# Test the connection
psql -U postgres -d banned_drugs -c "SELECT 1"
```

### Duplicate entries

The database enforces a unique constraint on `(generic_name, dosage_form, strength)`. The pipeline uses upsert logic to update existing entries rather than failing on duplicates.

---

## License

This project is for educational and public health research purposes.
# banned_medicine
