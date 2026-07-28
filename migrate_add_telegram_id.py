"""
One-time migration: adds telegram_user_id to the existing students table
without touching any existing data. Safe to run once; running it a second
time will just fail with 'duplicate column' (harmless -- means it already ran).
"""
import sqlite3

conn = sqlite3.connect("academic_records.db")
cur = conn.cursor()
try:
    cur.execute("ALTER TABLE students ADD COLUMN telegram_user_id TEXT")
    conn.commit()
    print("✅ Migration applied: telegram_user_id column added.")
except sqlite3.OperationalError as e:
    print(f"⚠️ Migration skipped (likely already applied): {e}")
finally:
    conn.close()