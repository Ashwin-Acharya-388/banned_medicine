"""
Database module for the Indian Banned Medicines Data Pipeline.

Defines SQLAlchemy ORM models for ``banned_medicines`` and ``source_documents``
tables and exposes a ``DatabaseManager`` class for all CRUD operations.

Connection pooling, upsert logic, and proper error handling are built in.
Database credentials are **never** hard-coded — they come from ``config.DATABASE_URL``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, Optional, Sequence

from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    Boolean,
    Column,
    Date,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from src import config

logger = logging.getLogger(__name__)

Base = declarative_base()

# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class BannedMedicine(Base):
    """Represents a single banned medicine entry."""

    __tablename__ = "banned_medicines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    generic_name = Column(Text, nullable=False, index=True)
    brand_names = Column(ARRAY(Text), default=[])
    dosage_form = Column(String(100))
    strength = Column(String(50))
    notification_number = Column(String(50), index=True)
    notification_date = Column(Date, index=True)
    ban_reason = Column(Text)
    source_pdf = Column(String(255))
    is_fdc = Column(Boolean, default=False, nullable=False)
    ingredients = Column(ARRAY(Text), default=[])
    source_url = Column(Text)
    date_added = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    last_updated = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "generic_name",
            "dosage_form",
            "strength",
            "notification_number",
            name="uq_medicine_form_strength_notif",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<BannedMedicine(id={self.id}, generic_name={self.generic_name!r}, "
            f"dosage_form={self.dosage_form!r})>"
        )


class UnofficialMedicine(Base):
    """Represents a single unofficial banned medicine entry."""

    __tablename__ = "unofficial_medicines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    generic_name = Column(Text, nullable=False, index=True)
    brand_names = Column(ARRAY(Text), default=[])
    dosage_form = Column(String(100))
    strength = Column(String(50))
    notification_number = Column(String(50), index=True)
    notification_date = Column(Date, index=True)
    ban_reason = Column(Text)
    source_pdf = Column(String(255))
    date_added = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    last_updated = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "generic_name",
            "dosage_form",
            "strength",
            name="uq_unofficial_medicine_form_strength",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<UnofficialMedicine(id={self.id}, generic_name={self.generic_name!r}, "
            f"dosage_form={self.dosage_form!r})>"
        )


class AyushFssaiMedicine(Base):
    """Represents a single AYUSH or FSSAI ingredient entry."""

    __tablename__ = "ayush_fssai_medicines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    generic_name = Column(Text, nullable=False, index=True)
    brand_names = Column(ARRAY(Text), default=[])
    dosage_form = Column(String(100))
    strength = Column(String(50))
    notification_number = Column(String(50), index=True)
    notification_date = Column(Date, index=True)
    ban_reason = Column(Text)
    source_pdf = Column(String(255))
    date_added = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    last_updated = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "generic_name",
            "dosage_form",
            "strength",
            name="uq_ayush_fssai_medicine_form_strength",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AyushFssaiMedicine(id={self.id}, generic_name={self.generic_name!r}, "
            f"dosage_form={self.dosage_form!r})>"
        )


class SourceDocument(Base):
    """Tracks source PDF files that have been downloaded and/or processed."""

    __tablename__ = "source_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_name = Column(String(255), unique=True, nullable=False)
    file_path = Column(Text)
    download_url = Column(Text)
    download_date = Column(TIMESTAMP(timezone=True))
    notification_number = Column(String(50))
    processing_status = Column(String(20), default="pending")  # pending | processed | error | needs_review
    notes = Column(Text)

    def __repr__(self) -> str:
        return (
            f"<SourceDocument(id={self.id}, file_name={self.file_name!r}, "
            f"status={self.processing_status!r})>"
        )


class NotificationProcessed(Base):
    """Tracks notification downloading and parsing status."""

    __tablename__ = "notifications_processed"

    id = Column(Integer, primary_key=True, autoincrement=True)
    notification_number = Column(String(50), unique=True, nullable=False, index=True)
    notification_date = Column(Date, index=True)
    source_url = Column(Text)
    pdf_path = Column(Text)
    download_status = Column(String(20), default="pending")  # pending | downloaded | failed | missing
    parsing_status = Column(String(20), default="pending")   # pending | parsed | failed | skipped
    error_message = Column(Text)
    last_attempt = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationProcessed(id={self.id}, notification_number={self.notification_number!r}, "
            f"download_status={self.download_status!r}, parsing_status={self.parsing_status!r})>"
        )


class ManualReviewItem(Base):
    """Tracks items requiring manual verification (e.g., scanned PDFs or parse errors)."""

    __tablename__ = "manual_review_queue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    notification_number = Column(String(50), index=True)
    notification_date = Column(Date, index=True)
    source_url = Column(Text)
    issue_type = Column(String(50), nullable=False)  # e.g., "scanned_pdf", "parsing_failed", "missing_fields", "validation_failed"
    description = Column(Text)
    raw_text = Column(Text)
    status = Column(String(20), default="pending")  # pending | reviewed | resolved
    date_added = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    last_updated = Column(
        TIMESTAMP(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<ManualReviewItem(id={self.id}, notification_number={self.notification_number!r}, "
            f"issue_type={self.issue_type!r}, status={self.status!r})>"
        )


# ---------------------------------------------------------------------------
# Database Manager
# ---------------------------------------------------------------------------


class DatabaseManager:
    """
    Central class for all database interactions.

    Usage::

        db = DatabaseManager()
        with db.session_scope() as session:
            db.upsert_medicine(session, entry_dict)
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        self.database_url = database_url or config.DATABASE_URL
        self.engine = create_engine(
            self.database_url,
            pool_size=config.DB_POOL_SIZE,
            max_overflow=config.DB_MAX_OVERFLOW,
            pool_timeout=config.DB_POOL_TIMEOUT,
            pool_pre_ping=True,  # verify connections before checkout
            echo=False,
        )
        self._SessionFactory = sessionmaker(bind=self.engine)
        logger.info("DatabaseManager initialised (pool_size=%d)", config.DB_POOL_SIZE)

    # ---- session helpers ---------------------------------------------------

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """Provide a transactional scope around a series of operations."""
        session: Session = self._SessionFactory()
        try:
            yield session
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            raise
        finally:
            session.close()

    # ---- schema management -------------------------------------------------

    def create_tables(self) -> None:
        """Create all tables defined by the ORM models (for dev/testing)."""
        Base.metadata.create_all(self.engine)
        logger.info("Database tables created (if they did not exist).")

    def drop_tables(self) -> None:
        """Drop all ORM-managed tables. **Destructive — use with caution.**"""
        Base.metadata.drop_all(self.engine)
        logger.warning("All database tables dropped.")

    # ---- health check ------------------------------------------------------

    def check_connection(self) -> bool:
        """Return True if the database is reachable."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection OK.")
            return True
        except OperationalError as exc:
            logger.error("Database connection FAILED: %s", exc)
            return False

    # ---- medicine CRUD -----------------------------------------------------

    def _upsert_generic_medicine(
        self,
        session: Session,
        model_class,
        entry: dict,
    ):
        filter_kwargs = {
            "generic_name": entry.get("generic_name"),
            "dosage_form": entry.get("dosage_form"),
            "strength": entry.get("strength"),
        }
        if model_class is BannedMedicine:
            filter_kwargs["notification_number"] = entry.get("notification_number")

        existing = (
            session.query(model_class)
            .filter_by(**filter_kwargs)
            .first()
        )

        attrs = [
            "brand_names",
            "notification_number",
            "notification_date",
            "ban_reason",
            "source_pdf",
        ]
        if model_class is BannedMedicine:
            attrs.extend(["is_fdc", "ingredients", "source_url"])

        if existing:
            # Update mutable fields
            for key in attrs:
                if key in entry and entry[key] is not None:
                    setattr(existing, key, entry[key])
            existing.last_updated = datetime.now(timezone.utc)
            logger.debug(
                "Updated existing %s: %s", model_class.__name__, existing.generic_name
            )
            return existing

        init_kwargs = {
            "generic_name": entry["generic_name"],
            "brand_names": entry.get("brand_names", []),
            "dosage_form": entry.get("dosage_form"),
            "strength": entry.get("strength"),
            "notification_number": entry.get("notification_number"),
            "notification_date": entry.get("notification_date"),
            "ban_reason": entry.get("ban_reason"),
            "source_pdf": entry.get("source_pdf"),
        }
        if model_class is BannedMedicine:
            init_kwargs["is_fdc"] = entry.get("is_fdc", False)
            init_kwargs["ingredients"] = entry.get("ingredients", [])
            init_kwargs["source_url"] = entry.get("source_url")

        medicine = model_class(**init_kwargs)
        session.add(medicine)
        try:
            session.flush()
            logger.debug("Inserted new %s: %s", model_class.__name__, medicine.generic_name)
        except IntegrityError:
            session.rollback()
            logger.warning(
                "Duplicate %s skipped: %s | %s | %s | %s",
                model_class.__name__,
                entry.get("generic_name"),
                entry.get("dosage_form"),
                entry.get("strength"),
                entry.get("notification_number") if model_class is BannedMedicine else "",
            )
        return medicine

    def upsert_medicine(
        self,
        session: Session,
        entry: dict,
    ) -> BannedMedicine:
        return self._upsert_generic_medicine(session, BannedMedicine, entry)

    def upsert_unofficial_medicine(
        self,
        session: Session,
        entry: dict,
    ) -> UnofficialMedicine:
        return self._upsert_generic_medicine(session, UnofficialMedicine, entry)

    def upsert_ayush_fssai_medicine(
        self,
        session: Session,
        entry: dict,
    ) -> AyushFssaiMedicine:
        return self._upsert_generic_medicine(session, AyushFssaiMedicine, entry)

    # ---- source document CRUD ----------------------------------------------

    def upsert_source_document(
        self,
        session: Session,
        doc: dict,
    ) -> SourceDocument:
        """
        Insert a new source-document record or update an existing one.

        Matching is based on the unique ``file_name`` column.
        """
        existing: Optional[SourceDocument] = (
            session.query(SourceDocument)
            .filter_by(file_name=doc.get("file_name"))
            .first()
        )

        if existing:
            for key in (
                "file_path",
                "download_url",
                "download_date",
                "notification_number",
                "processing_status",
                "notes",
            ):
                if doc.get(key) is not None:
                    setattr(existing, key, doc[key])
            logger.debug("Updated source document: %s", existing.file_name)
            return existing

        source = SourceDocument(
            file_name=doc["file_name"],
            file_path=doc.get("file_path"),
            download_url=doc.get("download_url"),
            download_date=doc.get("download_date"),
            notification_number=doc.get("notification_number"),
            processing_status=doc.get("processing_status", "pending"),
            notes=doc.get("notes"),
        )
        session.add(source)
        try:
            session.flush()
            logger.debug("Inserted source document: %s", source.file_name)
        except IntegrityError:
            session.rollback()
            logger.warning(
                "Duplicate source document skipped: %s", doc.get("file_name")
            )
        return source

    def get_pending_documents(self, session: Session) -> Sequence[SourceDocument]:
        """Return all source documents with ``processing_status='pending'``."""
        return (
            session.query(SourceDocument)
            .filter_by(processing_status="pending")
            .all()
        )

    def mark_document_processed(
        self,
        session: Session,
        doc_id: int,
        status: str = "processed",
        notes: Optional[str] = None,
    ) -> None:
        """Update the processing status of a source document."""
        doc = session.query(SourceDocument).get(doc_id)
        if doc:
            doc.processing_status = status
            if notes:
                doc.notes = notes
            logger.info(
                "Marked document %s as '%s'.", doc.file_name, status
            )

    def get_all_medicines(self, session: Session) -> Sequence[BannedMedicine]:
        """Retrieve all banned medicines, ordered by generic name."""
        return (
            session.query(BannedMedicine)
            .order_by(BannedMedicine.generic_name)
            .all()
        )

    def get_all_unofficial_medicines(self, session: Session) -> Sequence[UnofficialMedicine]:
        """Retrieve all unofficial medicines, ordered by generic name."""
        return (
            session.query(UnofficialMedicine)
            .order_by(UnofficialMedicine.generic_name)
            .all()
        )

    def get_all_ayush_fssai_medicines(self, session: Session) -> Sequence[AyushFssaiMedicine]:
        """Retrieve all AYUSH/FSSAI medicines, ordered by generic name."""
        return (
            session.query(AyushFssaiMedicine)
            .order_by(AyushFssaiMedicine.generic_name)
            .all()
        )

    def get_medicine_count(self, session: Session) -> int:
        """Return total number of banned-medicine records."""
        return session.query(BannedMedicine).count()

    def get_unofficial_medicine_count(self, session: Session) -> int:
        """Return total number of unofficial-medicine records."""
        return session.query(UnofficialMedicine).count()

    def get_ayush_fssai_medicine_count(self, session: Session) -> int:
        """Return total number of AYUSH/FSSAI records."""
        return session.query(AyushFssaiMedicine).count()

    def get_source_document_count(self, session: Session) -> int:
        """Return total number of source-document records."""
        return session.query(SourceDocument).count()

    def upsert_notification_processing(
        self,
        session: Session,
        notification: dict,
    ) -> NotificationProcessed:
        """Insert or update a notification processing record."""
        existing = (
            session.query(NotificationProcessed)
            .filter_by(notification_number=notification.get("notification_number"))
            .first()
        )

        if existing:
            for key in (
                "notification_date",
                "source_url",
                "pdf_path",
                "download_status",
                "parsing_status",
                "error_message",
            ):
                if key in notification and notification[key] is not None:
                    setattr(existing, key, notification[key])
            existing.last_attempt = datetime.now(timezone.utc)
            logger.debug("Updated notification processing: %s", existing.notification_number)
            return existing

        notif = NotificationProcessed(
            notification_number=notification["notification_number"],
            notification_date=notification.get("notification_date"),
            source_url=notification.get("source_url"),
            pdf_path=notification.get("pdf_path"),
            download_status=notification.get("download_status", "pending"),
            parsing_status=notification.get("parsing_status", "pending"),
            error_message=notification.get("error_message"),
        )
        session.add(notif)
        try:
            session.flush()
            logger.debug("Inserted notification processing: %s", notif.notification_number)
        except IntegrityError:
            session.rollback()
            logger.warning(
                "Duplicate notification processing skipped: %s", notification.get("notification_number")
            )
        return notif

    def add_to_review_queue(
        self,
        session: Session,
        item: dict,
    ) -> ManualReviewItem:
        """Add an item to the manual review queue."""
        existing = (
            session.query(ManualReviewItem)
            .filter_by(
                notification_number=item.get("notification_number"),
                issue_type=item.get("issue_type"),
                status="pending"
            )
            .first()
        )
        if existing:
            if item.get("description"):
                existing.description = item.get("description")
            if item.get("raw_text"):
                existing.raw_text = item.get("raw_text")
            existing.last_updated = datetime.now(timezone.utc)
            logger.debug("Updated existing pending review item: %s", existing.notification_number)
            return existing

        review_item = ManualReviewItem(
            notification_number=item.get("notification_number"),
            notification_date=item.get("notification_date"),
            source_url=item.get("source_url"),
            issue_type=item["issue_type"],
            description=item.get("description"),
            raw_text=item.get("raw_text"),
            status=item.get("status", "pending"),
        )
        session.add(review_item)
        try:
            session.flush()
            logger.debug("Added item to manual review queue: %s", review_item.notification_number)
        except IntegrityError:
            session.rollback()
            logger.warning("Failed to add review item to queue: %s", item.get("notification_number"))
        return review_item

    def get_medicines_by_notification(
        self,
        session: Session,
        notification_number: str,
    ) -> Sequence[BannedMedicine]:
        """Retrieve all banned medicines matching a specific notification number."""
        return (
            session.query(BannedMedicine)
            .filter_by(notification_number=notification_number)
            .order_by(BannedMedicine.generic_name)
            .all()
        )

    def get_master_list(self, session: Session) -> Sequence[BannedMedicine]:
        """Retrieve the master list of all unique banned medicines."""
        return self.get_all_medicines(session)

    def get_statistics(self, session: Session) -> dict:
        """Get summary statistics of the database tables."""
        total_banned = self.get_medicine_count(session)
        total_unofficial = self.get_unofficial_medicine_count(session)
        total_ayush_fssai = self.get_ayush_fssai_medicine_count(session)
        total_docs = self.get_source_document_count(session)
        
        total_processed_notifications = session.query(NotificationProcessed).count()
        downloaded_count = session.query(NotificationProcessed).filter_by(download_status="downloaded").count()
        parsed_count = session.query(NotificationProcessed).filter_by(parsing_status="parsed").count()
        pending_review = session.query(ManualReviewItem).filter_by(status="pending").count()
        
        fdc_count = session.query(BannedMedicine).filter_by(is_fdc=True).count()
        
        return {
            "total_banned_medicines": total_banned,
            "total_unofficial_medicines": total_unofficial,
            "total_ayush_fssai_medicines": total_ayush_fssai,
            "total_source_documents": total_docs,
            "total_processed_notifications": total_processed_notifications,
            "downloaded_notifications": downloaded_count,
            "parsed_notifications": parsed_count,
            "pending_manual_reviews": pending_review,
            "fdc_count": fdc_count,
            "single_ingredient_count": total_banned - fdc_count
        }

    def export_to_csv(self, session: Session, filepath: str) -> None:
        """Export the master banned medicines list to a CSV file."""
        import csv
        result = session.execute(text(
            "SELECT id, generic_name, brand_names, dosage_form, strength, "
            "notification_number, notification_date, ban_reason, source_pdf, "
            "is_fdc, ingredients, source_url, date_added "
            "FROM banned_medicines ORDER BY id"
        ))
        headers = list(result.keys())
        with open(filepath, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(headers)
            for row in result:
                row_data = []
                for val in row:
                    if isinstance(val, list):
                        row_data.append(", ".join(val))
                    else:
                        row_data.append(str(val) if val is not None else "")
                writer.writerow(row_data)
        logger.info("Exported banned medicines to CSV: %s", filepath)
