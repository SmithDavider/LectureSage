import sqlite3
from pathlib import Path


database = Path(__file__).resolve().parents[2] / "data" / "keji.db"
connection = sqlite3.connect(database)
connection.row_factory = sqlite3.Row


def rows(sql):
    return connection.execute(sql).fetchall()


print("COURSES")
for row in rows("""
    SELECT id, title, status, created_at, duration_ms,
           length(transcript) AS transcript_len,
           llm_base_url, llm_model
    FROM courses ORDER BY created_at DESC LIMIT 5
"""):
    print(dict(row))

print("\nLATEST AUDIO")
for row in rows("""
    SELECT id, course_id, sequence, offset_ms, filename, size_bytes,
           length(draft_text) AS draft_len, draft_confidence, review_state
    FROM audio_segments ORDER BY id DESC LIMIT 20
"""):
    print(dict(row))

print("\nLATEST SCREENS")
for row in rows("""
    SELECT id, course_id, offset_ms, filename, size_bytes,
           page, title, ocr_status, length(ocr_json) AS ocr_len
    FROM screenshots ORDER BY id DESC LIMIT 10
"""):
    print(dict(row))

print("\nLATEST JOBS")
for row in rows("""
    SELECT id, course_id, task_type, status, message, created_at
    FROM jobs ORDER BY id DESC LIMIT 10
"""):
    print(dict(row))

connection.close()

