# migrate_batch_nullable.py
import sqlite3
conn = sqlite3.connect("academic_records.db")
cur = conn.cursor()
cur.execute("PRAGMA foreign_keys=off")
cur.execute("""
CREATE TABLE students_new (
    id TEXT PRIMARY KEY, telegram_user_id TEXT UNIQUE, name TEXT NOT NULL,
    batch_id INTEGER, stream_id INTEGER, cgpa REAL, status TEXT DEFAULT 'ACTIVE'
)""")
cur.execute("INSERT INTO students_new SELECT * FROM students")
cur.execute("DROP TABLE students")
cur.execute("ALTER TABLE students_new RENAME TO students")
conn.commit()
conn.close()
print("Migration applied.")