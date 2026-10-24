import json
import sqlite3
from pathlib import Path


database = Path(__file__).resolve().parents[2] / "data" / "keji.db"
connection = sqlite3.connect(database)
connection.row_factory = sqlite3.Row


def normalize_sections(value: str | None) -> list:
    if not value:
        return []
    try:
        items = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        section = dict(item)
        summary = section.get("summary")
        if isinstance(summary, list):
            summary = "\n".join(
                str(part).strip() for part in summary if str(part).strip()
            )
        section["summary"] = str(summary or "")
        normalized.append(section)
    return normalized


changed = 0
for row in connection.execute("SELECT id, sections FROM courses"):
    current = row["sections"] or ""
    try:
        parsed = json.loads(current)
    except json.JSONDecodeError:
        continue
    if not isinstance(parsed, list):
        continue
    needs_migration = any(
        isinstance(item, dict) and isinstance(item.get("summary"), list)
        for item in parsed
    )
    if not needs_migration:
        continue
    normalized = normalize_sections(current)
    connection.execute(
        "UPDATE courses SET sections=? WHERE id=?",
        (
            json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
            row["id"],
        ),
    )
    changed += 1
    print(f"migrated {row['id']}")

connection.commit()
connection.close()
print(f"migrated={changed}")
