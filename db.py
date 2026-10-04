from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from classifier import CLASSIFIER_VERSION, classify_saved_post

DB_PATH = Path(os.getenv("DATABASE_PATH", "data/competitions.db"))


def ensure_parent() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect():
    ensure_parent()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(competitions)").fetchall()}
    if name not in columns:
        conn.execute(f"ALTER TABLE competitions ADD COLUMN {name} {definition}")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS competitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                import_title TEXT,
                raw_text TEXT,
                instagram_url TEXT,
                imported_official_url TEXT,
                competition_name TEXT,
                organizer TEXT,
                category TEXT,
                deadline TEXT,
                deadline_text TEXT,
                entry_fee TEXT,
                prize TEXT,
                eligibility TEXT,
                requirements TEXT,
                official_url TEXT,
                ai_confidence REAL DEFAULT 0,
                verified INTEGER DEFAULT 0,
                status TEXT DEFAULT 'unreviewed',
                verification_notes TEXT,
                sources_json TEXT DEFAULT '[]',
                local_kind TEXT,
                local_score INTEGER DEFAULT 0,
                local_reason TEXT,
                local_is_opportunity INTEGER DEFAULT 0,
                local_classifier_version INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_comp_deadline ON competitions(deadline);
            CREATE INDEX IF NOT EXISTS idx_comp_category ON competitions(category);
            CREATE INDEX IF NOT EXISTS idx_comp_verified ON competitions(verified);
            """
        )

        # Lightweight migration for databases created by earlier versions.
        _ensure_column(conn, "local_kind", "TEXT")
        _ensure_column(conn, "local_score", "INTEGER DEFAULT 0")
        _ensure_column(conn, "local_reason", "TEXT")
        _ensure_column(conn, "local_is_opportunity", "INTEGER DEFAULT 0")
        _ensure_column(conn, "local_classifier_version", "INTEGER DEFAULT 0")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comp_local_opportunity ON competitions(local_is_opportunity)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comp_local_kind ON competitions(local_kind)"
        )

    reclassify_all(force=False)


def _classification(title: str, raw_text: str) -> dict[str, Any]:
    return classify_saved_post(title=title, raw_text=raw_text)


def reclassify_all(force: bool = True) -> int:
    count = 0
    with connect() as conn:
        if force:
            rows = conn.execute(
                "SELECT id, import_title, raw_text FROM competitions"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, import_title, raw_text
                FROM competitions
                WHERE local_classifier_version IS NULL
                   OR local_classifier_version < ?
                """,
                (CLASSIFIER_VERSION,),
            ).fetchall()

        for row in rows:
            c = _classification(row["import_title"] or "", row["raw_text"] or "")
            conn.execute(
                """
                UPDATE competitions
                SET local_kind = ?,
                    local_score = ?,
                    local_reason = ?,
                    local_is_opportunity = ?,
                    local_classifier_version = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    c["local_kind"],
                    c["local_score"],
                    c["local_reason"],
                    c["local_is_opportunity"],
                    c["local_classifier_version"],
                    row["id"],
                ),
            )
            count += 1
    return count


def insert_imported(rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with connect() as conn:
        for row in rows:
            raw_text = (row.get("raw_text") or "").strip()
            title = (row.get("import_title") or "").strip()
            instagram_url = (row.get("instagram_url") or "").strip()
            official_url = (row.get("imported_official_url") or "").strip()
            if not any([raw_text, title, instagram_url, official_url]):
                continue

            if instagram_url:
                exists = conn.execute(
                    "SELECT id FROM competitions WHERE instagram_url = ? LIMIT 1",
                    (instagram_url,),
                ).fetchone()
                if exists:
                    continue

            c = _classification(title, raw_text)
            conn.execute(
                """
                INSERT INTO competitions
                    (import_title, raw_text, instagram_url, imported_official_url,
                     competition_name, official_url, local_kind, local_score,
                     local_reason, local_is_opportunity, local_classifier_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    raw_text,
                    instagram_url,
                    official_url,
                    title or None,
                    official_url or None,
                    c["local_kind"],
                    c["local_score"],
                    c["local_reason"],
                    c["local_is_opportunity"],
                    c["local_classifier_version"],
                ),
            )
            count += 1
    return count


def get_competition(comp_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM competitions WHERE id = ?", (comp_id,)).fetchone()
    return dict(row) if row else None


def list_competitions(
    q: str = "",
    category: str = "",
    verified: str = "",
    status: str = "",
    view: str = "opportunities",
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM competitions WHERE 1=1"
    params: list[Any] = []

    if view == "opportunities":
        sql += " AND local_is_opportunity = 1"
    elif view == "review":
        sql += " AND local_kind = 'needs_review'"
    elif view == "other":
        sql += " AND local_is_opportunity = 0 AND COALESCE(local_kind, '') != 'needs_review'"
    elif view == "all":
        pass
    else:
        sql += " AND local_is_opportunity = 1"

    if q:
        sql += " AND (competition_name LIKE ? OR organizer LIKE ? OR raw_text LIKE ? OR prize LIKE ? OR local_reason LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like, like, like])
    if category:
        sql += " AND category = ?"
        params.append(category)
    if verified in {"0", "1"}:
        sql += " AND verified = ?"
        params.append(int(verified))
    if status:
        sql += " AND status = ?"
        params.append(status)

    sql += " ORDER BY local_score DESC, CASE WHEN deadline IS NULL OR deadline = '' THEN 1 ELSE 0 END, deadline ASC, id DESC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    result = []
    for row in rows:
        item = dict(row)
        try:
            item["sources"] = json.loads(item.get("sources_json") or "[]")
        except json.JSONDecodeError:
            item["sources"] = []
        result.append(item)
    return result


def categories() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM competitions WHERE category IS NOT NULL AND category != '' ORDER BY category"
        ).fetchall()
    return [r[0] for r in rows]


def update_extraction(comp_id: int, values: dict[str, Any]) -> None:
    allowed = {
        "competition_name", "organizer", "category", "deadline", "deadline_text",
        "entry_fee", "prize", "eligibility", "requirements", "official_url",
        "ai_confidence", "status",
    }
    clean = {k: v for k, v in values.items() if k in allowed}
    if not clean:
        return
    assignments = ", ".join(f"{k} = ?" for k in clean)
    params = list(clean.values()) + [comp_id]
    with connect() as conn:
        conn.execute(
            f"UPDATE competitions SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            params,
        )


def update_verification(comp_id: int, values: dict[str, Any], sources: list[dict[str, str]]) -> None:
    allowed = {
        "competition_name", "organizer", "category", "deadline", "deadline_text",
        "entry_fee", "prize", "eligibility", "requirements", "official_url",
        "ai_confidence", "status", "verification_notes",
    }
    clean = {k: v for k, v in values.items() if k in allowed}
    clean["verified"] = 1
    clean["sources_json"] = json.dumps(sources, ensure_ascii=False)
    assignments = ", ".join(f"{k} = ?" for k in clean)
    params = list(clean.values()) + [comp_id]
    with connect() as conn:
        conn.execute(
            f"UPDATE competitions SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            params,
        )


def delete_competition(comp_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM competitions WHERE id = ?", (comp_id,))


def stats() -> dict[str, int]:
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM competitions").fetchone()[0]
        opportunities = conn.execute(
            "SELECT COUNT(*) FROM competitions WHERE local_is_opportunity = 1"
        ).fetchone()[0]
        review = conn.execute(
            "SELECT COUNT(*) FROM competitions WHERE local_kind = 'needs_review'"
        ).fetchone()[0]
        other = conn.execute(
            "SELECT COUNT(*) FROM competitions WHERE local_is_opportunity = 0 AND COALESCE(local_kind, '') != 'needs_review'"
        ).fetchone()[0]
        verified = conn.execute("SELECT COUNT(*) FROM competitions WHERE verified = 1").fetchone()[0]
    return {
        "total": total,
        "opportunities": opportunities,
        "review": review,
        "other": other,
        "verified": verified,
    }
