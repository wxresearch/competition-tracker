from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from classifier import CLASSIFIER_VERSION, classify_saved_post
from splitter import SPLITTER_VERSION, clean_source_text, split_source_post

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

                record_origin TEXT DEFAULT 'source_post',
                parent_id INTEGER,
                source_timestamp INTEGER,
                owner_name TEXT,
                owner_username TEXT,
                owner_url TEXT,
                split_status TEXT DEFAULT 'not_analyzed',
                split_count INTEGER DEFAULT 0,
                local_splitter_version INTEGER DEFAULT 0,
                source_timing TEXT,
                source_excerpt TEXT,

                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_comp_deadline ON competitions(deadline);
            CREATE INDEX IF NOT EXISTS idx_comp_category ON competitions(category);
            CREATE INDEX IF NOT EXISTS idx_comp_verified ON competitions(verified);
            """
        )

        # Lightweight migrations for databases created by earlier versions.
        for name, definition in (
            ("local_kind", "TEXT"),
            ("local_score", "INTEGER DEFAULT 0"),
            ("local_reason", "TEXT"),
            ("local_is_opportunity", "INTEGER DEFAULT 0"),
            ("local_classifier_version", "INTEGER DEFAULT 0"),
            ("record_origin", "TEXT DEFAULT 'source_post'"),
            ("parent_id", "INTEGER"),
            ("source_timestamp", "INTEGER"),
            ("owner_name", "TEXT"),
            ("owner_username", "TEXT"),
            ("owner_url", "TEXT"),
            ("split_status", "TEXT DEFAULT 'not_analyzed'"),
            ("split_count", "INTEGER DEFAULT 0"),
            ("local_splitter_version", "INTEGER DEFAULT 0"),
            ("source_timing", "TEXT"),
            ("source_excerpt", "TEXT"),
        ):
            _ensure_column(conn, name, definition)

        # All rows from versions before source/child splitting were imported posts.
        conn.execute(
            "UPDATE competitions SET record_origin = 'source_post' "
            "WHERE record_origin IS NULL OR record_origin = ''"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comp_origin ON competitions(record_origin)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comp_parent ON competitions(parent_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comp_local_kind ON competitions(local_kind)"
        )

    analyze_all_source_posts(force=False)


def _classification(title: str, raw_text: str) -> dict[str, Any]:
    return classify_saved_post(title=title, raw_text=raw_text)


def _source_row(conn: sqlite3.Connection, comp_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM competitions WHERE id = ? AND record_origin = 'source_post'",
        (comp_id,),
    ).fetchone()


def _child_raw_text(parent: sqlite3.Row, item: dict[str, Any]) -> str:
    parts = [
        f"Locally extracted opportunity: {item['name']}",
    ]
    if item.get("source_timing"):
        parts.append(f"Timing in source: {item['source_timing']}")
    if item.get("source_excerpt"):
        parts.append(f"Source excerpt: {item['source_excerpt']}")
    caption = clean_source_text(parent["raw_text"] or "")
    if caption:
        parts.append("Full Instagram caption:\n" + caption)
    return "\n\n".join(parts)


def _analyze_source(conn: sqlite3.Connection, source_id: int) -> int:
    parent = _source_row(conn, source_id)
    if not parent:
        return 0

    classification = _classification(parent["import_title"] or "", parent["raw_text"] or "")
    split = split_source_post(
        parent["raw_text"] or "",
        source_timestamp=parent["source_timestamp"],
    )

    # Rebuild local children deterministically so rule changes are reflected.
    conn.execute(
        "DELETE FROM competitions WHERE record_origin = 'split_child' AND parent_id = ?",
        (source_id,),
    )

    items = split["items"]
    if items:
        split_status = "split" if split["is_source_list"] or len(items) > 1 else "split_single"
    elif split["is_source_list"]:
        split_status = "list_unresolved"
    elif classification["local_is_opportunity"] or classification["local_kind"] == "needs_review":
        split_status = "unresolved_single"
    else:
        split_status = "not_opportunity"

    conn.execute(
        """
        UPDATE competitions
        SET local_kind = ?,
            local_score = ?,
            local_reason = ?,
            local_is_opportunity = ?,
            local_classifier_version = ?,
            split_status = ?,
            split_count = ?,
            local_splitter_version = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            classification["local_kind"],
            classification["local_score"],
            classification["local_reason"],
            classification["local_is_opportunity"],
            classification["local_classifier_version"],
            split_status,
            len(items),
            split["splitter_version"],
            source_id,
        ),
    )

    for item in items:
        child_reason = "Named opportunity extracted locally from the Instagram caption."
        if split["is_source_list"]:
            child_reason = "Named item split locally from an Instagram list/roundup post."

        conn.execute(
            """
            INSERT INTO competitions (
                import_title, raw_text, instagram_url, imported_official_url,
                competition_name, deadline, deadline_text,
                local_kind, local_score, local_reason, local_is_opportunity,
                local_classifier_version, record_origin, parent_id,
                source_timestamp, owner_name, owner_username, owner_url,
                split_status, split_count, local_splitter_version,
                source_timing, source_excerpt
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'split_child', ?, ?, ?, ?, ?, 'child', 0, ?, ?, ?)
            """,
            (
                item["name"],
                _child_raw_text(parent, item),
                parent["instagram_url"],
                parent["imported_official_url"],
                item["name"],
                item.get("deadline"),
                item.get("deadline_text"),
                item.get("kind"),
                96 if item.get("deadline_text") else 90,
                child_reason,
                CLASSIFIER_VERSION,
                source_id,
                parent["source_timestamp"],
                parent["owner_name"],
                parent["owner_username"],
                parent["owner_url"],
                SPLITTER_VERSION,
                item.get("source_timing"),
                item.get("source_excerpt"),
            ),
        )

    return len(items)


def analyze_all_source_posts(force: bool = True) -> dict[str, int]:
    analyzed = 0
    children = 0
    with connect() as conn:
        if force:
            rows = conn.execute(
                "SELECT id FROM competitions WHERE record_origin = 'source_post'"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id
                FROM competitions
                WHERE record_origin = 'source_post'
                  AND (
                    COALESCE(local_classifier_version, 0) < ?
                    OR COALESCE(local_splitter_version, 0) < ?
                  )
                """,
                (CLASSIFIER_VERSION, SPLITTER_VERSION),
            ).fetchall()

        for row in rows:
            children += _analyze_source(conn, row["id"])
            analyzed += 1

    return {"analyzed": analyzed, "children": children}


def insert_imported(rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    new_count = 0
    refreshed = 0
    children = 0

    with connect() as conn:
        for row in rows:
            raw_text = (row.get("raw_text") or "").strip()
            title = (row.get("import_title") or "").strip()
            instagram_url = (row.get("instagram_url") or "").strip()
            official_url = (row.get("imported_official_url") or "").strip()
            source_timestamp = row.get("source_timestamp")
            owner_name = (row.get("owner_name") or "").strip()
            owner_username = (row.get("owner_username") or "").strip()
            owner_url = (row.get("owner_url") or "").strip()

            if not any([raw_text, title, instagram_url, official_url]):
                continue

            existing = None
            if instagram_url:
                existing = conn.execute(
                    """
                    SELECT id
                    FROM competitions
                    WHERE record_origin = 'source_post' AND instagram_url = ?
                    LIMIT 1
                    """,
                    (instagram_url,),
                ).fetchone()

            if existing:
                source_id = existing["id"]
                conn.execute(
                    """
                    UPDATE competitions
                    SET import_title = ?,
                        raw_text = ?,
                        imported_official_url = ?,
                        competition_name = ?,
                        official_url = COALESCE(NULLIF(?, ''), official_url),
                        source_timestamp = COALESCE(?, source_timestamp),
                        owner_name = COALESCE(NULLIF(?, ''), owner_name),
                        owner_username = COALESCE(NULLIF(?, ''), owner_username),
                        owner_url = COALESCE(NULLIF(?, ''), owner_url),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        title,
                        raw_text,
                        official_url,
                        title or None,
                        official_url,
                        source_timestamp,
                        owner_name,
                        owner_username,
                        owner_url,
                        source_id,
                    ),
                )
                refreshed += 1
            else:
                cur = conn.execute(
                    """
                    INSERT INTO competitions (
                        import_title, raw_text, instagram_url, imported_official_url,
                        competition_name, official_url, record_origin,
                        source_timestamp, owner_name, owner_username, owner_url
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 'source_post', ?, ?, ?, ?)
                    """,
                    (
                        title,
                        raw_text,
                        instagram_url,
                        official_url,
                        title or None,
                        official_url or None,
                        source_timestamp,
                        owner_name,
                        owner_username,
                        owner_url,
                    ),
                )
                source_id = int(cur.lastrowid)
                new_count += 1

            children += _analyze_source(conn, source_id)

    return {
        "new": new_count,
        "refreshed": refreshed,
        "children": children,
    }


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
        sql += " AND record_origin = 'split_child'"
    elif view == "sources":
        sql += " AND record_origin = 'source_post' AND split_status IN ('split','split_single','list_unresolved')"
    elif view == "review":
        sql += " AND record_origin = 'source_post' AND split_status = 'unresolved_single'"
    elif view == "other":
        sql += " AND record_origin = 'source_post' AND split_status = 'not_opportunity'"
    elif view == "all":
        pass
    else:
        sql += " AND record_origin = 'split_child'"

    if q:
        sql += (
            " AND (competition_name LIKE ? OR organizer LIKE ? OR raw_text LIKE ? "
            "OR prize LIKE ? OR local_reason LIKE ? OR owner_name LIKE ? OR owner_username LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like, like, like, like, like, like, like])
    if category:
        sql += " AND category = ?"
        params.append(category)
    if verified in {"0", "1"}:
        sql += " AND verified = ?"
        params.append(int(verified))
    if status:
        sql += " AND status = ?"
        params.append(status)

    if view == "opportunities":
        sql += (
            " ORDER BY CASE WHEN deadline IS NULL OR deadline = '' THEN 1 ELSE 0 END, "
            "deadline ASC, competition_name COLLATE NOCASE ASC"
        )
    else:
        sql += " ORDER BY split_count DESC, local_score DESC, id DESC"

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
            """
            SELECT DISTINCT category
            FROM competitions
            WHERE record_origin = 'split_child'
              AND category IS NOT NULL AND category != ''
            ORDER BY category
            """
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
        row = conn.execute(
            "SELECT record_origin FROM competitions WHERE id = ?",
            (comp_id,),
        ).fetchone()
        if not row:
            return
        if row["record_origin"] == "source_post":
            conn.execute("DELETE FROM competitions WHERE parent_id = ?", (comp_id,))
        conn.execute("DELETE FROM competitions WHERE id = ?", (comp_id,))


def stats() -> dict[str, int]:
    with connect() as conn:
        total_sources = conn.execute(
            "SELECT COUNT(*) FROM competitions WHERE record_origin = 'source_post'"
        ).fetchone()[0]
        opportunities = conn.execute(
            "SELECT COUNT(*) FROM competitions WHERE record_origin = 'split_child'"
        ).fetchone()[0]
        source_lists = conn.execute(
            """
            SELECT COUNT(*) FROM competitions
            WHERE record_origin = 'source_post'
              AND split_status IN ('split','split_single','list_unresolved')
            """
        ).fetchone()[0]
        review = conn.execute(
            """
            SELECT COUNT(*) FROM competitions
            WHERE record_origin = 'source_post' AND split_status = 'unresolved_single'
            """
        ).fetchone()[0]
        other = conn.execute(
            """
            SELECT COUNT(*) FROM competitions
            WHERE record_origin = 'source_post' AND split_status = 'not_opportunity'
            """
        ).fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM competitions WHERE record_origin = 'split_child' AND verified = 1"
        ).fetchone()[0]

    return {
        "total_sources": total_sources,
        "opportunities": opportunities,
        "source_lists": source_lists,
        "review": review,
        "other": other,
        "verified": verified,
    }
