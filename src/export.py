import csv
from sqlalchemy import text
from src.database import DatabaseManager

def export_table_to_csv(db: DatabaseManager, table_name: str, export_file: str) -> None:
    """Helper to query a specific table and export all records to a CSV file."""
    with db.session_scope() as session:
        result = session.execute(text(
            f"SELECT id, generic_name, brand_names, dosage_form, strength, "
            f"notification_number, notification_date, ban_reason, source_pdf, date_added "
            f"FROM {table_name} ORDER BY id"
        ))
        headers = list(result.keys())
        
        with open(export_file, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(headers)
            for row in result:
                row_data = []
                for val in row:
                    if isinstance(val, list):
                        # brand_names array representation
                        row_data.append(", ".join(val))
                    else:
                        row_data.append(str(val) if val is not None else "")
                writer.writerow(row_data)
                
    print(f"Database table '{table_name}' successfully exported to {export_file}")


def export_to_csv() -> None:
    """Export all separated database tables to distinct CSV files."""
    db = DatabaseManager()
    export_table_to_csv(db, "banned_medicines", "banned_medicines_export.csv")
    export_table_to_csv(db, "unofficial_medicines", "unofficial_medicines_export.csv")
    export_table_to_csv(db, "ayush_fssai_medicines", "ayush_fssai_medicines_export.csv")


if __name__ == "__main__":
    export_to_csv()
