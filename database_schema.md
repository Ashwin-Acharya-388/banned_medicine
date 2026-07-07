# Database Schema Documentation

This document describes the database schema designed and implemented for the **Indian Banned Medicines Data Pipeline** project. 

The schema is built on **PostgreSQL** and focuses on high data integrity, strict decoupling of official regulatory data from unofficial aggregates, robust audit trails, and clean tracking of Fixed Dose Combination (FDC) active ingredients.

---

## 1. Architectural Introduction

The database is structured to support a multi-layered public health dataset. The primary architectural goals include:
- **Decoupling Data Sources**: Separating verified Central CDSCO bans (`banned_medicines`) from third-party roadmap compilations (`unofficial_medicines`) and traditional/supplement regulations (`ayush_fssai_medicines`).
- **Granular Ingredient Tracking**: Storing raw generic names alongside normalized boolean flags and constituent ingredient arrays (`is_fdc` and `ingredients`) to enable chemical-level searches across complex combination products.
- **Pipeline and Exception Auditing**: Maintaining a direct operational link between file download tracking (`source_documents`), unique notification processing states (`notifications_processed`), and a manual exception triage system (`manual_review_queue`).

---

## 2. Data Dictionary

### 2.1. `banned_medicines`
Contains the verified, deduplicated list of banned drug formulations under the Central CDSCO regulatory authority.

| Column Name | SQL Data Type | Indexes / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | Serial / Autoincrement | | Unique record identifier. |
| `generic_name` | `TEXT` | INDEX, NOT NULL | | Standardized generic drug name or FDC title. |
| `brand_names` | `TEXT[]` | | `{}` | Common commercial brand names associated with the ban. |
| `dosage_form` | `VARCHAR(100)` | | `NULL` | Canonical dosage form (e.g., Tablet, Syrup, Injection). |
| `strength` | `VARCHAR(50)` | | `NULL` | Formulation strength or concentration limits. |
| `notification_number` | `VARCHAR(50)` | INDEX | `NULL` | Legal Gazette publication key (e.g., G.S.R. 578(E)). |
| `notification_date` | `DATE` | INDEX | `NULL` | Publication date of the Gazette notification. |
| `ban_reason` | `TEXT` | | `NULL` | Detailed legal or therapeutic justification for the ban. |
| `source_pdf` | `VARCHAR(255)` | | `NULL` | Target file name from which the ban was parsed. |
| `is_fdc` | `BOOLEAN` | NOT NULL | `FALSE` | Flag indicating if this is a Fixed Dose Combination. |
| `ingredients` | `TEXT[]` | | `{}` | Array of split active chemical ingredients (for FDCs). |
| `source_url` | `TEXT` | | `NULL` | Original download link on the CDSCO portal. |
| `date_added` | `TIMESTAMP WITH TZ` | | `now() UTC` | Timestamp when the entry was first created. |
| `last_updated` | `TIMESTAMP WITH TZ` | | `now() UTC` | Timestamp when the entry was last modified. |

*   **Composite Constraint**: `UniqueConstraint("generic_name", "dosage_form", "strength", "notification_number", name="uq_medicine_form_strength_notif")` prevents duplicate tracking of identical bans while permitting historical amendment logging.

---

### 2.2. `unofficial_medicines`
Stores aggregated banned drug records compiled from third-party compliance listings (Vaayath, TheHealthMaster) used as a discovery roadmap.

| Column Name | SQL Data Type | Indexes / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | Serial / Autoincrement | | Unique record identifier. |
| `generic_name` | `TEXT` | INDEX, NOT NULL | | Generic drug name as reported by the aggregator. |
| `brand_names` | `TEXT[]` | | `{}` | Associated brand names. |
| `dosage_form` | `VARCHAR(100)` | | `NULL` | Reported dosage form. |
| `strength` | `VARCHAR(50)` | | `NULL` | Reported strength description. |
| `notification_number` | `VARCHAR(50)` | INDEX | `NULL` | Suggested notification identifier. |
| `notification_date` | `DATE` | INDEX | `NULL` | Suggested date of publication. |
| `ban_reason` | `TEXT` | | `NULL` | Stated reason for recall or ban. |
| `source_pdf` | `VARCHAR(255)` | | `NULL` | Identifier of the compiler (e.g. `vaayath.com`). |
| `date_added` | `TIMESTAMP WITH TZ` | | `now() UTC` | Record creation timestamp. |
| `last_updated` | `TIMESTAMP WITH TZ` | | `now() UTC` | Record modification timestamp. |

*   **Composite Constraint**: `UniqueConstraint("generic_name", "dosage_form", "strength", name="uq_unofficial_medicine_form_strength")`

---

### 2.3. `ayush_fssai_medicines`
Stores traditional herbal safety notifications (Ministry of AYUSH) and prohibited supplement/additive ingredient listings (FSSAI).

| Column Name | SQL Data Type | Indexes / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | Serial / Autoincrement | | Unique record identifier. |
| `generic_name` | `TEXT` | INDEX, NOT NULL | | Prohibited ingredient, botanical, or formulation. |
| `brand_names` | `TEXT[]` | | `{}` | Associated commercial products (if any). |
| `dosage_form` | `VARCHAR(100)` | | `NULL` | Targeted delivery type. |
| `strength` | `VARCHAR(50)` | | `NULL` | Concentration limits. |
| `notification_number` | `VARCHAR(50)` | INDEX | `NULL` | Circular, order, or regulation reference. |
| `notification_date` | `DATE` | INDEX | `NULL` | Publication date of safety advisory. |
| `ban_reason` | `TEXT` | | `NULL` | Advisory context or toxicological rationale. |
| `source_pdf` | `VARCHAR(255)` | | `NULL` | Originating regulatory domain (`ayush.gov.in` \| `fssai.gov.in`). |
| `date_added` | `TIMESTAMP WITH TZ` | | `now() UTC` | Creation timestamp. |
| `last_updated` | `TIMESTAMP WITH TZ` | | `now() UTC` | Update timestamp. |

*   **Composite Constraint**: `UniqueConstraint("generic_name", "dosage_form", "strength", name="uq_ayush_fssai_medicine_form_strength")`

---

### 2.4. `source_documents`
Tracks downloaded Gazette PDF files and their processing pipeline states.

| Column Name | SQL Data Type | Indexes / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | Serial / Autoincrement | | Unique document identifier. |
| `file_name` | `VARCHAR(255)` | UNIQUE, NOT NULL | | Sanitized local filename. |
| `file_path` | `TEXT` | | `NULL` | Local workspace storage directory path. |
| `download_url` | `TEXT` | | `NULL` | Source HTTP URL page link. |
| `download_date` | `TIMESTAMP WITH TZ` | | `NULL` | Download completion timestamp. |
| `notification_number` | `VARCHAR(50)` | | `NULL` | Gazette identifier linked to the document. |
| `processing_status` | `VARCHAR(20)` | | `'pending'` | Processing state (`pending` \| `processed` \| `error`). |
| `notes` | `TEXT` | | `NULL` | Operational notes or processing error logs. |

---

### 2.5. `notifications_processed`
Provides a detailed audit trail of crawler discovery and download attempts for every unique Gazette identifier.

| Column Name | SQL Data Type | Indexes / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | Serial / Autoincrement | | Unique record identifier. |
| `notification_number` | `VARCHAR(50)` | UNIQUE, INDEX, NOT NULL | | Normalized Gazette notification number. |
| `notification_date` | `DATE` | INDEX | `NULL` | Extracted notification date. |
| `source_url` | `TEXT` | | `NULL` | Resolved PDF download endpoint link. |
| `pdf_path` | `TEXT` | | `NULL` | Local path to verified download file. |
| `download_status` | `VARCHAR(20)` | | `'pending'` | Status (`pending` \| `downloaded` \| `failed` \| `missing`). |
| `parsing_status` | `VARCHAR(20)` | | `'pending'` | Status (`pending` \| `parsed` \| `failed` \| `skipped`). |
| `error_message` | `TEXT` | | `NULL` | Failure traceback or connection timeout error detail. |
| `last_attempt` | `TIMESTAMP WITH TZ` | | `now() UTC` | Timestamp of the most recent crawl/acquire retry. |

---

### 2.6. `manual_review_queue`
Manages pipeline exceptions, ensuring data anomalies, scanned PDFs, or unlinked aggregator records are safely flagged for human verification.

| Column Name | SQL Data Type | Indexes / Constraints | Default Value | Description |
| :--- | :--- | :--- | :--- | :--- |
| `id` | `INTEGER` | Serial / Autoincrement | | Unique queue item identifier. |
| `notification_number` | `VARCHAR(50)` | INDEX | `NULL` | Gazette reference associated with the exception. |
| `notification_date` | `DATE` | INDEX | `NULL` | Associated date. |
| `source_url` | `TEXT` | | `NULL` | Originating link. |
| `issue_type` | `VARCHAR(50)` | NOT NULL | | Category (`scanned_pdf` \| `parsing_failed` \| `validation_failed` \| `unlinked_record`). |
| `description` | `TEXT` | | `NULL` | Detailed error description. |
| `raw_text` | `TEXT` | | `NULL` | Extracted unformatted parser text for inspection. |
| `status` | `VARCHAR(20)` | | `'pending'` | Review state (`pending` \| `reviewed` \| `resolved`). |
| `date_added` | `TIMESTAMP WITH TZ` | | `now() UTC` | Date queued. |
| `last_updated` | `TIMESTAMP WITH TZ` | | `now() UTC` | Last state transition timestamp. |

---

## 3. Key Design Decisions & Mechanisms

### 3.1. Composite Unique Constraints for Revision Auditing
Historically, India's CDSCO has published amendments that alter previous notifications. To accommodate this without throwing unique key constraint violations (e.g. if a drug formulation is banned, then amended, then revoked), the primary unique constraint `uq_medicine_form_strength_notif` incorporates both the drug profile and the **Gazette notification number**. This enables tracking the entire legal lifecycle of a molecule.

### 3.2. Chemical-Level FDC Indexing
Bans are often written at the combination level (e.g., *Aceclofenac + Paracetamol + Rabeprazole*). To make the dataset searchable by individual ingredients, the parsing pipeline extracts, splits, and stores the constituents in the Postgres `ingredients` array column, alongside a boolean `is_fdc` flag. This allows high-performance queries using the Postgres array containment operator (`@>`):
```sql
-- Find all FDCs containing Paracetamol
SELECT * FROM banned_medicines WHERE is_fdc = TRUE AND ingredients @> ARRAY['Paracetamol'];
```

### 3.3. Sandboxed Table Decoupling
To safeguard verified central data, data imported from aggregators (`unofficial_medicines`) and traditional/supplement bodies (`ayush_fssai_medicines`) are isolated in dedicated tables. This isolates distinct legal jurisdictions (such as Drugs vs. Foods) and prevents unverified third-party records from polluting the primary `banned_medicines` CDSCO registry.
