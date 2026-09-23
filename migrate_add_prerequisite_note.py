"""
migrate_add_prerequisite_note.py
===================================
One-off migration for an EXISTING academic_records.db: adds the 'note'
column to the 'prerequisites' table.

Why this is needed: models.py's Prerequisite class was missing a `note`
column that app.py's prerequisite-management form has always tried to
save, causing:

    TypeError: 'note' is an invalid keyword argument for Prerequisite

Updating models.py alone fixes this for a BRAND NEW database (created
via Base.metadata.create_all()), but SQLAlchemy's create_all() only
creates tables that don't exist yet -- it never alters an existing
table to add a missing column. Since your prerequisites table already
exists with real data in it, this script patches it in place with a
plain ALTER TABLE, instead of you having to delete and rebuild the
whole database from the CSV.

Safe to run more than once -- it checks whether the column already
exists first and does nothing if so.

Usage:
    python migrate_add_prerequisite_note.py
"""

from sqlalchemy import inspect, text
from models import engine

TABLE = "prerequisites"
COLUMN = "note"


def column_exists(conn, table, column):
    inspector = inspect(conn)
    existing_columns = [c["name"] for c in inspector.get_columns(table)]
    return column in existing_columns


def main():
    with engine.connect() as conn:
        if column_exists(conn, TABLE, COLUMN):
            print(f"'{COLUMN}' already exists on '{TABLE}' -- nothing to do.")
            return

        print(f"Adding '{COLUMN}' column to '{TABLE}'...")
        # SQLite supports simple ADD COLUMN directly; no data is touched
        # or lost, every existing row just gets NULL for the new column.
        conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} VARCHAR"))
        conn.commit()
        print("Done. Existing prerequisite links are unaffected (note is NULL for all of them until edited).")


if __name__ == "__main__":
    main()
