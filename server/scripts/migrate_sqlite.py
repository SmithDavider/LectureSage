import sqlite3
from pathlib import Path


def migrate_file(database: Path) -> bool:
    connection = sqlite3.connect(database)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(audio_segments)")}

    if "draft_text" not in columns:
        connection.execute("ALTER TABLE audio_segments ADD COLUMN draft_text TEXT DEFAULT ''")
    if "draft_confidence" not in columns:
        connection.execute("ALTER TABLE audio_segments ADD COLUMN draft_confidence INTEGER DEFAULT 0")
    if "review_state" not in columns:
        connection.execute("ALTER TABLE audio_segments ADD COLUMN review_state TEXT DEFAULT 'none'")

    course_columns = {row[1] for row in connection.execute("PRAGMA table_info(courses)")}
    if "owner_id" not in course_columns:
        connection.execute("ALTER TABLE courses ADD COLUMN owner_id INTEGER")
        admin = connection.execute(
            "SELECT id FROM users WHERE username = 'admin' LIMIT 1"
        ).fetchone()
        if admin is not None:
            connection.execute(
                "UPDATE courses SET owner_id = ? WHERE owner_id IS NULL",
                (admin[0],),
            )
        else:
            first_user = connection.execute(
                "SELECT id FROM users ORDER BY id LIMIT 1"
            ).fetchone()
            if first_user is not None:
                connection.execute(
                    "UPDATE courses SET owner_id = ? WHERE owner_id IS NULL",
                    (first_user[0],),
                )
    if "llm_base_url" not in course_columns:
        connection.execute("ALTER TABLE courses ADD COLUMN llm_base_url TEXT DEFAULT ''")
    if "llm_api_key" not in course_columns:
        connection.execute("ALTER TABLE courses ADD COLUMN llm_api_key TEXT DEFAULT ''")
    if "llm_model" not in course_columns:
        connection.execute("ALTER TABLE courses ADD COLUMN llm_model TEXT DEFAULT ''")
    if "last_activity_at" not in course_columns:
        connection.execute("ALTER TABLE courses ADD COLUMN last_activity_at INTEGER DEFAULT 0")
    if "screen_enabled" not in course_columns:
        connection.execute("ALTER TABLE courses ADD COLUMN screen_enabled INTEGER DEFAULT 1")
    if "microphone_enabled" not in course_columns:
        connection.execute("ALTER TABLE courses ADD COLUMN microphone_enabled INTEGER DEFAULT 1")

    connection.commit()
    connection.close()
    return True


base_dir = Path(__file__).resolve().parents[2] / "data"
databases = [base_dir / "keji.db", *sorted((base_dir / "users").glob("u*.db"))]
for database in databases:
    if not database.exists():
        continue
    migrate_file(database)

print(f"MIGRATE_OK count={len(databases)}")
