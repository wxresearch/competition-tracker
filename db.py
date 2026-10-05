from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from classifier import CLASSIFIER_VERSION, classify_saved_post
from splitter import SPLITTER_VERSION, clean_source_text, split_source_post

DB_PATH = Path(os.getenv("DATABASE_PATH", "data/competitions.db"))
VERIFICATION_CACHE_DAYS = int(os.getenv("VERIFICATION_CACHE_DAYS", "7"))


def ensure_parent() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect():
    ensure_parent()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(competitions)").fetchall()}
    if name not in columns:
        conn.execute(f"ALTER TABLE competitions ADD COLUMN {name} {definition}")


def _canonical_key(name: str | None) -> str:
    value = (name or "").lower()
    value = re.sub(r"\b(the|annual)\b", " ", value)
    value = re.sub(r"\b(20\d{2}(?:[-–/]\d{2,4})?)\b", " ", value)
    value = re.sub(r"[^a-z0-9]+", "", value)
    return value


def _cycle_year(deadline: str | None, source_timestamp: int | None = None) -> int | None:
    # Only assign a cycle year when the record itself supports it. The date a
    # user saved an Instagram post is not reliable evidence of the competition
    # cycle, so unknown cycles remain unknown until edited or verified.
    if deadline:
        m = re.match(r"^(20\d{2})-", deadline)
        if m:
            return int(m.group(1))
    return None


def _cycle_label(year: int | None) -> str | None:
    return str(year) if year else None


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_name TEXT NOT NULL,
                opportunity_key TEXT NOT NULL UNIQUE,
                organizer TEXT,
                category TEXT,
                official_url TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

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

                opportunity_id INTEGER,
                opportunity_key TEXT,
                cycle_year INTEGER,
                cycle_label TEXT,
                review_state TEXT DEFAULT 'active',
                manually_edited INTEGER DEFAULT 0,
                merged_into_id INTEGER,
                verification_level TEXT DEFAULT 'unverified',
                last_verified_at TEXT,

                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS opportunity_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                competition_id INTEGER NOT NULL,
                source_post_id INTEGER,
                instagram_url TEXT,
                source_excerpt TEXT,
                owner_name TEXT,
                owner_username TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(competition_id, source_post_id),
                FOREIGN KEY(competition_id) REFERENCES competitions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS verification_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                competition_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY(competition_id) REFERENCES competitions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_comp_deadline ON competitions(deadline);
            CREATE INDEX IF NOT EXISTS idx_comp_category ON competitions(category);
            CREATE INDEX IF NOT EXISTS idx_comp_verified ON competitions(verified);
            CREATE INDEX IF NOT EXISTS idx_comp_origin ON competitions(record_origin);
            CREATE INDEX IF NOT EXISTS idx_comp_parent ON competitions(parent_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_comp_status ON verification_jobs(competition_id, status);
            """
        )

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
            ("opportunity_id", "INTEGER"),
            ("opportunity_key", "TEXT"),
            ("cycle_year", "INTEGER"),
            ("cycle_label", "TEXT"),
            ("review_state", "TEXT DEFAULT 'active'"),
            ("manually_edited", "INTEGER DEFAULT 0"),
            ("merged_into_id", "INTEGER"),
            ("verification_level", "TEXT DEFAULT 'unverified'"),
            ("last_verified_at", "TEXT"),
        ):
            _ensure_column(conn, name, definition)

        conn.execute(
            "UPDATE competitions SET record_origin = 'source_post' "
            "WHERE record_origin IS NULL OR record_origin = ''"
        )
        # Indexes that depend on migrated columns must be created only after
        # _ensure_column has added those columns to older databases.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comp_opportunity ON competitions(opportunity_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comp_key_cycle ON competitions(opportunity_key, cycle_year)"
        )

        conn.execute(
            "UPDATE competitions SET review_state = 'active' "
            "WHERE review_state IS NULL OR review_state = ''"
        )

        # Background jobs cannot survive a local app/process restart. Clean up
        # stale queued/running rows so the dashboard does not refresh forever.
        conn.execute(
            """
            UPDATE verification_jobs
            SET status='error',
                error='Verification was interrupted by an app restart.',
                finished_at=CURRENT_TIMESTAMP
            WHERE status IN ('queued','running')
            """
        )

        _migrate_existing_children(conn)

    analyze_all_source_posts(force=False)


def _get_or_create_opportunity(
    conn: sqlite3.Connection,
    name: str,
    organizer: str | None = None,
    category: str | None = None,
    official_url: str | None = None,
) -> int:
    key = _canonical_key(name)
    if not key:
        raise ValueError("Opportunity name is too generic to create a canonical record.")

    row = conn.execute(
        "SELECT id FROM opportunities WHERE opportunity_key = ?",
        (key,),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE opportunities
            SET canonical_name = COALESCE(NULLIF(?, ''), canonical_name),
                organizer = COALESCE(NULLIF(?, ''), organizer),
                category = COALESCE(NULLIF(?, ''), category),
                official_url = COALESCE(NULLIF(?, ''), official_url),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (name, organizer, category, official_url, row["id"]),
        )
        return int(row["id"])

    cur = conn.execute(
        """
        INSERT INTO opportunities
            (canonical_name, opportunity_key, organizer, category, official_url)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, key, organizer, category, official_url),
    )
    return int(cur.lastrowid)


def _migrate_existing_children(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, competition_name, import_title, organizer, category, official_url,
               deadline, source_timestamp, parent_id, instagram_url, source_excerpt,
               owner_name, owner_username, opportunity_id
        FROM competitions
        WHERE record_origin = 'split_child'
          AND COALESCE(merged_into_id, 0) = 0
        """
    ).fetchall()

    for row in rows:
        name = row["competition_name"] or row["import_title"] or ""
        key = _canonical_key(name)
        if not key:
            continue
        year = _cycle_year(row["deadline"], row["source_timestamp"])
        opp_id = row["opportunity_id"] or _get_or_create_opportunity(
            conn,
            name,
            row["organizer"],
            row["category"],
            row["official_url"],
        )
        conn.execute(
            """
            UPDATE competitions
            SET opportunity_id = ?,
                opportunity_key = ?,
                cycle_year = COALESCE(cycle_year, ?),
                cycle_label = COALESCE(cycle_label, ?)
            WHERE id = ?
            """,
            (opp_id, key, year, _cycle_label(year), row["id"]),
        )
        if row["parent_id"]:
            conn.execute(
                """
                INSERT OR IGNORE INTO opportunity_sources
                    (competition_id, source_post_id, instagram_url, source_excerpt, owner_name, owner_username)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["parent_id"],
                    row["instagram_url"],
                    row["source_excerpt"],
                    row["owner_name"],
                    row["owner_username"],
                ),
            )

    _deduplicate_children(conn)


def _classification(title: str, raw_text: str) -> dict[str, Any]:
    return classify_saved_post(title=title, raw_text=raw_text)


def _source_row(conn: sqlite3.Connection, comp_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM competitions WHERE id = ? AND record_origin = 'source_post'",
        (comp_id,),
    ).fetchone()


def _child_raw_text(parent: sqlite3.Row, item: dict[str, Any]) -> str:
    parts = [f"Locally extracted opportunity: {item['name']}"]
    if item.get("source_timing"):
        parts.append(f"Timing in source: {item['source_timing']}")
    if item.get("source_excerpt"):
        parts.append(f"Source excerpt: {item['source_excerpt']}")
    caption = clean_source_text(parent["raw_text"] or "")
    if caption:
        parts.append("Full Instagram caption:\n" + caption)
    return "\n\n".join(parts)


def _remove_source_links(conn: sqlite3.Connection, source_id: int) -> None:
    linked = conn.execute(
        """
        SELECT os.competition_id, c.manually_edited
        FROM opportunity_sources os
        JOIN competitions c ON c.id = os.competition_id
        WHERE os.source_post_id = ?
        """,
        (source_id,),
    ).fetchall()

    # Parser-generated links are rebuilt. Manually reviewed/corrected links stay
    # attached so a future rebuild cannot resurrect the old parser value beside
    # the user's correction.
    for row in linked:
        if row["manually_edited"]:
            continue

        comp_id = row["competition_id"]
        conn.execute(
            "DELETE FROM opportunity_sources WHERE source_post_id=? AND competition_id=?",
            (source_id, comp_id),
        )
        remaining = conn.execute(
            "SELECT COUNT(*) FROM opportunity_sources WHERE competition_id = ?",
            (comp_id,),
        ).fetchone()[0]
        if remaining == 0:
            conn.execute("DELETE FROM competitions WHERE id = ?", (comp_id,))

    # Legacy pre-source-table children.
    legacy = conn.execute(
        """
        SELECT id FROM competitions
        WHERE record_origin = 'split_child' AND parent_id = ?
          AND id NOT IN (SELECT competition_id FROM opportunity_sources)
          AND COALESCE(manually_edited, 0) = 0
        """,
        (source_id,),
    ).fetchall()
    for row in legacy:
        conn.execute("DELETE FROM competitions WHERE id = ?", (row["id"],))


def _find_existing_cycle(
    conn: sqlite3.Connection,
    opportunity_key: str,
    cycle_year: int | None,
) -> sqlite3.Row | None:
    if cycle_year is not None:
        return conn.execute(
            """
            SELECT * FROM competitions
            WHERE record_origin = 'split_child'
              AND opportunity_key = ?
              AND cycle_year = ?
              AND COALESCE(merged_into_id, 0) = 0
              AND review_state != 'irrelevant'
            ORDER BY manually_edited DESC, verified DESC, id ASC
            LIMIT 1
            """,
            (opportunity_key, cycle_year),
        ).fetchone()

    # Unknown cycles are only flagged as possible duplicates. They are not
    # auto-merged because identical names can represent different annual cycles.
    return None


def _attach_source(
    conn: sqlite3.Connection,
    comp_id: int,
    parent: sqlite3.Row,
    item: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO opportunity_sources
            (competition_id, source_post_id, instagram_url, source_excerpt, owner_name, owner_username)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            comp_id,
            parent["id"],
            parent["instagram_url"],
            item.get("source_excerpt"),
            parent["owner_name"],
            parent["owner_username"],
        ),
    )


def _analyze_source(conn: sqlite3.Connection, source_id: int) -> int:
    parent = _source_row(conn, source_id)
    if not parent:
        return 0

    classification = _classification(parent["import_title"] or "", parent["raw_text"] or "")
    split = split_source_post(
        parent["raw_text"] or "",
        source_timestamp=parent["source_timestamp"],
    )

    _remove_source_links(conn, source_id)

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
        SET local_kind = ?, local_score = ?, local_reason = ?,
            local_is_opportunity = ?, local_classifier_version = ?,
            split_status = ?, split_count = ?, local_splitter_version = ?,
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

    created_or_attached = 0
    for item in items:
        manual_match = conn.execute(
            """
            SELECT c.id
            FROM opportunity_sources os
            JOIN competitions c ON c.id = os.competition_id
            WHERE os.source_post_id = ?
              AND c.manually_edited = 1
              AND COALESCE(os.source_excerpt, '') = COALESCE(?, '')
            LIMIT 1
            """,
            (source_id, item.get("source_excerpt")),
        ).fetchone()
        if manual_match:
            created_or_attached += 1
            continue

        name = item["name"]
        key = _canonical_key(name)
        if not key:
            continue
        year = _cycle_year(item.get("deadline"), parent["source_timestamp"])
        opp_id = _get_or_create_opportunity(conn, name, category=item.get("kind"))
        existing = _find_existing_cycle(conn, key, year)

        if existing:
            comp_id = existing["id"]
            if not existing["deadline"] and item.get("deadline"):
                conn.execute(
                    """
                    UPDATE competitions
                    SET deadline = ?, deadline_text = ?, source_timing = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        item.get("deadline"),
                        item.get("deadline_text"),
                        item.get("source_timing"),
                        comp_id,
                    ),
                )
        else:
            child_reason = "Named opportunity extracted locally from the Instagram caption."
            if split["is_source_list"]:
                child_reason = "Named item split locally from an Instagram list/roundup post."
            cur = conn.execute(
                """
                INSERT INTO competitions (
                    import_title, raw_text, instagram_url, imported_official_url,
                    competition_name, deadline, deadline_text,
                    local_kind, local_score, local_reason, local_is_opportunity,
                    local_classifier_version, record_origin, parent_id,
                    source_timestamp, owner_name, owner_username, owner_url,
                    split_status, split_count, local_splitter_version,
                    source_timing, source_excerpt, opportunity_id, opportunity_key,
                    cycle_year, cycle_label
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'split_child', ?, ?, ?, ?, ?,
                        'child', 0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    _child_raw_text(parent, item),
                    parent["instagram_url"],
                    parent["imported_official_url"],
                    name,
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
                    opp_id,
                    key,
                    year,
                    _cycle_label(year),
                ),
            )
            comp_id = int(cur.lastrowid)

        _attach_source(conn, comp_id, parent, item)
        created_or_attached += 1

    return created_or_attached


def _deduplicate_children(conn: sqlite3.Connection) -> int:
    groups = conn.execute(
        """
        SELECT opportunity_key, cycle_year, COUNT(*) AS n
        FROM competitions
        WHERE record_origin = 'split_child'
          AND COALESCE(merged_into_id, 0) = 0
          AND review_state != 'irrelevant'
          AND opportunity_key IS NOT NULL AND opportunity_key != ''
          AND cycle_year IS NOT NULL
        GROUP BY opportunity_key, cycle_year
        HAVING COUNT(*) > 1
        """
    ).fetchall()

    merged = 0
    for group in groups:
        rows = conn.execute(
            """
            SELECT * FROM competitions
            WHERE record_origin = 'split_child'
              AND opportunity_key = ?
              AND ((cycle_year = ?) OR (cycle_year IS NULL AND ? IS NULL))
              AND COALESCE(merged_into_id, 0) = 0
            ORDER BY manually_edited DESC, verified DESC,
                     CASE WHEN deadline IS NOT NULL AND deadline != '' THEN 0 ELSE 1 END,
                     id ASC
            """,
            (group["opportunity_key"], group["cycle_year"], group["cycle_year"]),
        ).fetchall()
        if len(rows) < 2:
            continue

        keeper = rows[0]
        for dup in rows[1:]:
            for src in conn.execute(
                "SELECT * FROM opportunity_sources WHERE competition_id = ?",
                (dup["id"],),
            ).fetchall():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO opportunity_sources
                        (competition_id, source_post_id, instagram_url, source_excerpt, owner_name, owner_username)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        keeper["id"], src["source_post_id"], src["instagram_url"],
                        src["source_excerpt"], src["owner_name"], src["owner_username"],
                    ),
                )

            updates: dict[str, Any] = {}
            for field in (
                "deadline", "deadline_text", "entry_fee", "prize", "eligibility",
                "requirements", "official_url", "organizer", "category",
                "verification_notes", "last_verified_at",
            ):
                if not keeper[field] and dup[field]:
                    updates[field] = dup[field]
            if updates:
                assignments = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE competitions SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    [*updates.values(), keeper["id"]],
                )

            conn.execute(
                "UPDATE competitions SET merged_into_id = ?, review_state='merged' WHERE id = ?",
                (keeper["id"], dup["id"]),
            )
            merged += 1
    return merged


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
                SELECT id FROM competitions
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

        merged = _deduplicate_children(conn)

    return {"analyzed": analyzed, "children": children, "merged": merged}


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
                    SELECT id FROM competitions
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
                    SET import_title = ?, raw_text = ?, imported_official_url = ?,
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
                        title, raw_text, official_url, title or None, official_url,
                        source_timestamp, owner_name, owner_username, owner_url, source_id,
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
                        title, raw_text, instagram_url, official_url,
                        title or None, official_url or None, source_timestamp,
                        owner_name, owner_username, owner_url,
                    ),
                )
                source_id = int(cur.lastrowid)
                new_count += 1

            children += _analyze_source(conn, source_id)

        merged = _deduplicate_children(conn)

    return {
        "new": new_count,
        "refreshed": refreshed,
        "children": children,
        "merged": merged,
    }


def get_competition(comp_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM competitions WHERE id = ?", (comp_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["source_links"] = [
            dict(x)
            for x in conn.execute(
                "SELECT * FROM opportunity_sources WHERE competition_id = ? ORDER BY id",
                (comp_id,),
            ).fetchall()
        ]
    return item


def find_merge_candidates(comp_id: int, limit: int = 25) -> list[dict[str, Any]]:
    with connect() as conn:
        current = conn.execute(
            "SELECT * FROM competitions WHERE id=?",
            (comp_id,),
        ).fetchone()
        if not current:
            return []

        rows = conn.execute(
            """
            SELECT id, competition_name, organizer, category, deadline, cycle_year,
                   verification_level, verified
            FROM competitions
            WHERE record_origin='split_child'
              AND id != ?
              AND review_state='active'
              AND COALESCE(merged_into_id,0)=0
              AND (
                    category = ?
                    OR organizer = ?
                    OR competition_name LIKE ?
                  )
            ORDER BY verified DESC,
                     CASE WHEN cycle_year = ? THEN 0 ELSE 1 END,
                     competition_name COLLATE NOCASE
            LIMIT ?
            """,
            (
                comp_id,
                current["category"],
                current["organizer"],
                f"%{(current['competition_name'] or '').split(' ')[0]}%",
                current["cycle_year"],
                limit,
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def _decorate_deadline(item: dict[str, Any]) -> dict[str, Any]:
    item["days_remaining"] = None
    item["urgency_bucket"] = "unknown"
    item["urgency_text"] = "Deadline unknown"

    raw = item.get("deadline")
    if not raw:
        return item
    try:
        deadline = date.fromisoformat(raw)
    except ValueError:
        return item

    days = (deadline - date.today()).days
    item["days_remaining"] = days
    if days < 0:
        item["urgency_bucket"] = "expired"
        item["urgency_text"] = f"Expired {abs(days)} day{'s' if abs(days) != 1 else ''} ago"
    elif days == 0:
        item["urgency_bucket"] = "today"
        item["urgency_text"] = "Due today"
    elif days <= 7:
        item["urgency_bucket"] = "week"
        item["urgency_text"] = f"{days} day{'s' if days != 1 else ''} left"
    elif days <= 30:
        item["urgency_bucket"] = "month"
        item["urgency_text"] = f"{days} days left"
    else:
        item["urgency_bucket"] = "later"
        item["urgency_text"] = f"{days} days left"
    return item


def _urgency_sort(item: dict[str, Any]) -> tuple[int, int, str]:
    days = item.get("days_remaining")
    if days is None:
        return (4, 999999, (item.get("competition_name") or "").lower())
    if days < 0:
        return (3, abs(days), (item.get("competition_name") or "").lower())
    if days <= 7:
        return (0, days, (item.get("competition_name") or "").lower())
    if days <= 30:
        return (1, days, (item.get("competition_name") or "").lower())
    return (2, days, (item.get("competition_name") or "").lower())


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
        sql += (
            " AND record_origin = 'split_child'"
            " AND review_state = 'active'"
            " AND COALESCE(merged_into_id, 0) = 0"
        )
    elif view == "sources":
        sql += " AND record_origin = 'source_post' AND split_status IN ('split','split_single','list_unresolved')"
    elif view == "review":
        sql += (
            " AND ((record_origin = 'source_post' AND split_status = 'unresolved_single')"
            " OR (record_origin = 'split_child' AND review_state = 'needs_review'))"
        )
    elif view == "other":
        sql += (
            " AND ((record_origin = 'source_post' AND split_status = 'not_opportunity')"
            " OR (record_origin = 'split_child' AND review_state = 'irrelevant'))"
        )
    elif view == "all":
        sql += " AND review_state != 'merged'"
    else:
        sql += " AND record_origin = 'split_child' AND review_state='active' AND COALESCE(merged_into_id,0)=0"

    if q:
        sql += (
            " AND (competition_name LIKE ? OR organizer LIKE ? OR raw_text LIKE ? "
            "OR prize LIKE ? OR local_reason LIKE ? OR owner_name LIKE ? OR owner_username LIKE ?)"
        )
        like = f"%{q}%"
        params.extend([like] * 7)
    if category:
        sql += " AND category = ?"
        params.append(category)
    if verified in {"0", "1"}:
        sql += " AND verified = ?"
        params.append(int(verified))
    if status:
        sql += " AND status = ?"
        params.append(status)

    sql += " ORDER BY id DESC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        active_jobs = {
            row["competition_id"]: row["status"]
            for row in conn.execute(
                """
                SELECT v.competition_id, v.status
                FROM verification_jobs v
                JOIN (
                    SELECT competition_id, MAX(id) AS max_id
                    FROM verification_jobs GROUP BY competition_id
                ) latest ON latest.max_id = v.id
                WHERE v.status IN ('queued','running')
                """
            ).fetchall()
        }

        result = []
        for row in rows:
            item = dict(row)
            try:
                item["sources"] = json.loads(item.get("sources_json") or "[]")
            except json.JSONDecodeError:
                item["sources"] = []
            item["source_links"] = [
                dict(x)
                for x in conn.execute(
                    "SELECT * FROM opportunity_sources WHERE competition_id = ? ORDER BY id",
                    (item["id"],),
                ).fetchall()
            ]
            item["source_count"] = len(item["source_links"])
            if item.get("record_origin") == "split_child" and item.get("opportunity_key"):
                item["possible_duplicates"] = conn.execute(
                    """
                    SELECT COUNT(*) FROM competitions
                    WHERE record_origin='split_child'
                      AND id != ?
                      AND opportunity_key = ?
                      AND review_state='active'
                      AND COALESCE(merged_into_id,0)=0
                    """,
                    (item["id"], item["opportunity_key"]),
                ).fetchone()[0]
            else:
                item["possible_duplicates"] = 0
            item["verification_job_status"] = active_jobs.get(item["id"])
            _decorate_deadline(item)
            result.append(item)

    if view == "opportunities":
        result.sort(key=_urgency_sort)
    return result


def categories() -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT category FROM competitions
            WHERE record_origin = 'split_child'
              AND review_state = 'active'
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

    with connect() as conn:
        if "competition_name" in clean and clean["competition_name"]:
            key = _canonical_key(clean["competition_name"])
            clean["opportunity_key"] = key
            opp_id = _get_or_create_opportunity(
                conn,
                clean["competition_name"],
                clean.get("organizer"),
                clean.get("category"),
                clean.get("official_url"),
            )
            clean["opportunity_id"] = opp_id
        if "deadline" in clean:
            year = _cycle_year(clean.get("deadline"))
            clean["cycle_year"] = year
            clean["cycle_label"] = _cycle_label(year)

        assignments = ", ".join(f"{k} = ?" for k in clean)
        conn.execute(
            f"UPDATE competitions SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            [*clean.values(), comp_id],
        )


def update_manual(comp_id: int, values: dict[str, Any]) -> None:
    allowed = {
        "competition_name", "organizer", "category", "deadline", "deadline_text",
        "entry_fee", "prize", "eligibility", "requirements", "official_url",
        "status", "cycle_year", "cycle_label",
    }
    clean = {k: (v.strip() if isinstance(v, str) else v) for k, v in values.items() if k in allowed}
    clean = {k: (v if v not in {"", None} else None) for k, v in clean.items()}

    with connect() as conn:
        if clean.get("competition_name"):
            key = _canonical_key(clean["competition_name"])
            opp_id = _get_or_create_opportunity(
                conn,
                clean["competition_name"],
                clean.get("organizer"),
                clean.get("category"),
                clean.get("official_url"),
            )
            clean["opportunity_key"] = key
            clean["opportunity_id"] = opp_id

        if clean.get("deadline") and not clean.get("cycle_year"):
            clean["cycle_year"] = _cycle_year(clean["deadline"])
        if clean.get("cycle_year") and not clean.get("cycle_label"):
            clean["cycle_label"] = _cycle_label(int(clean["cycle_year"]))

        clean["manually_edited"] = 1
        assignments = ", ".join(f"{k} = ?" for k in clean)
        conn.execute(
            f"UPDATE competitions SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            [*clean.values(), comp_id],
        )
        _deduplicate_children(conn)


def set_review_state(comp_id: int, state: str) -> None:
    if state not in {"active", "needs_review", "irrelevant"}:
        raise ValueError("Invalid review state")
    with connect() as conn:
        conn.execute(
            """
            UPDATE competitions
            SET review_state=?,
                manually_edited=CASE WHEN record_origin='split_child' THEN 1 ELSE manually_edited END,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (state, comp_id),
        )


def merge_competitions(target_id: int, duplicate_id: int) -> None:
    if target_id == duplicate_id:
        return
    with connect() as conn:
        target = conn.execute("SELECT * FROM competitions WHERE id=?", (target_id,)).fetchone()
        dup = conn.execute("SELECT * FROM competitions WHERE id=?", (duplicate_id,)).fetchone()
        if not target or not dup:
            raise ValueError("Opportunity not found")

        for src in conn.execute(
            "SELECT * FROM opportunity_sources WHERE competition_id=?",
            (duplicate_id,),
        ).fetchall():
            conn.execute(
                """
                INSERT OR IGNORE INTO opportunity_sources
                    (competition_id, source_post_id, instagram_url, source_excerpt, owner_name, owner_username)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    target_id, src["source_post_id"], src["instagram_url"],
                    src["source_excerpt"], src["owner_name"], src["owner_username"],
                ),
            )

        updates: dict[str, Any] = {}
        for field in (
            "deadline", "deadline_text", "entry_fee", "prize", "eligibility",
            "requirements", "official_url", "organizer", "category",
            "verification_notes", "last_verified_at",
        ):
            if not target[field] and dup[field]:
                updates[field] = dup[field]
        if updates:
            assignments = ", ".join(f"{k}=?" for k in updates)
            conn.execute(
                f"UPDATE competitions SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                [*updates.values(), target_id],
            )

        conn.execute(
            "UPDATE competitions SET merged_into_id=?, review_state='merged' WHERE id=?",
            (target_id, duplicate_id),
        )


def _host(url: str | None) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.lower().removeprefix("www.")


def infer_verification_level(
    values: dict[str, Any],
    sources: list[dict[str, str]],
) -> str:
    notes = str(values.get("verification_notes") or values.get("notes") or "").lower()
    if any(word in notes for word in ("conflict", "disagree", "inconsistent")):
        return "conflicting"
    official = values.get("official_url")
    if official:
        official_host = _host(str(official))
        if official_host and any(_host(s.get("url")) == official_host for s in sources):
            return "official"
    if sources:
        return "web"
    return "unclear"


def update_verification(comp_id: int, values: dict[str, Any], sources: list[dict[str, str]]) -> None:
    allowed = {
        "competition_name", "organizer", "category", "deadline", "deadline_text",
        "entry_fee", "prize", "eligibility", "requirements", "official_url",
        "ai_confidence", "status", "verification_notes",
    }
    clean = {k: v for k, v in values.items() if k in allowed}
    clean["verified"] = 1
    clean["sources_json"] = json.dumps(sources, ensure_ascii=False)
    clean["verification_level"] = infer_verification_level(clean, sources)
    clean["last_verified_at"] = datetime.now(timezone.utc).isoformat()

    with connect() as conn:
        if clean.get("competition_name"):
            clean["opportunity_key"] = _canonical_key(clean["competition_name"])
            clean["opportunity_id"] = _get_or_create_opportunity(
                conn,
                clean["competition_name"],
                clean.get("organizer"),
                clean.get("category"),
                clean.get("official_url"),
            )
        if clean.get("deadline"):
            year = _cycle_year(clean["deadline"])
            clean["cycle_year"] = year
            clean["cycle_label"] = _cycle_label(year)

        assignments = ", ".join(f"{k} = ?" for k in clean)
        conn.execute(
            f"UPDATE competitions SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            [*clean.values(), comp_id],
        )


def verification_is_fresh(comp_id: int, max_age_days: int = VERIFICATION_CACHE_DAYS) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT last_verified_at FROM competitions WHERE id=?",
            (comp_id,),
        ).fetchone()
    if not row or not row["last_verified_at"]:
        return False
    try:
        checked = datetime.fromisoformat(row["last_verified_at"])
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - checked < timedelta(days=max_age_days)


def create_verification_job(comp_id: int) -> int:
    with connect() as conn:
        active = conn.execute(
            """
            SELECT id FROM verification_jobs
            WHERE competition_id=? AND status IN ('queued','running')
            ORDER BY id DESC LIMIT 1
            """,
            (comp_id,),
        ).fetchone()
        if active:
            return int(active["id"])
        cur = conn.execute(
            "INSERT INTO verification_jobs (competition_id, status) VALUES (?, 'queued')",
            (comp_id,),
        )
        return int(cur.lastrowid)


def set_verification_job(job_id: int, status: str, error: str | None = None) -> None:
    with connect() as conn:
        if status == "running":
            conn.execute(
                """
                UPDATE verification_jobs
                SET status='running', started_at=CURRENT_TIMESTAMP, error=NULL
                WHERE id=?
                """,
                (job_id,),
            )
        elif status in {"done", "error"}:
            conn.execute(
                """
                UPDATE verification_jobs
                SET status=?, error=?, finished_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, error, job_id),
            )
        else:
            conn.execute(
                "UPDATE verification_jobs SET status=?, error=? WHERE id=?",
                (status, error, job_id),
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
            linked = conn.execute(
                "SELECT competition_id FROM opportunity_sources WHERE source_post_id=?",
                (comp_id,),
            ).fetchall()
            conn.execute("DELETE FROM opportunity_sources WHERE source_post_id=?", (comp_id,))
            for link in linked:
                count = conn.execute(
                    "SELECT COUNT(*) FROM opportunity_sources WHERE competition_id=?",
                    (link["competition_id"],),
                ).fetchone()[0]
                if count == 0:
                    conn.execute("DELETE FROM competitions WHERE id=?", (link["competition_id"],))
        conn.execute("DELETE FROM competitions WHERE id = ?", (comp_id,))


def stats() -> dict[str, int]:
    with connect() as conn:
        total_sources = conn.execute(
            "SELECT COUNT(*) FROM competitions WHERE record_origin='source_post'"
        ).fetchone()[0]
        opportunities = conn.execute(
            """
            SELECT COUNT(*) FROM competitions
            WHERE record_origin='split_child' AND review_state='active'
              AND COALESCE(merged_into_id,0)=0
            """
        ).fetchone()[0]
        source_lists = conn.execute(
            """
            SELECT COUNT(*) FROM competitions
            WHERE record_origin='source_post'
              AND split_status IN ('split','split_single','list_unresolved')
            """
        ).fetchone()[0]
        review = conn.execute(
            """
            SELECT COUNT(*) FROM competitions
            WHERE (record_origin='source_post' AND split_status='unresolved_single')
               OR (record_origin='split_child' AND review_state='needs_review')
            """
        ).fetchone()[0]
        other = conn.execute(
            """
            SELECT COUNT(*) FROM competitions
            WHERE (record_origin='source_post' AND split_status='not_opportunity')
               OR (record_origin='split_child' AND review_state='irrelevant')
            """
        ).fetchone()[0]
        verified = conn.execute(
            """
            SELECT COUNT(*) FROM competitions
            WHERE record_origin='split_child' AND review_state='active'
              AND verified=1 AND COALESCE(merged_into_id,0)=0
            """
        ).fetchone()[0]
        due_week = conn.execute(
            """
            SELECT COUNT(*) FROM competitions
            WHERE record_origin='split_child' AND review_state='active'
              AND COALESCE(merged_into_id,0)=0
              AND deadline >= date('now') AND deadline <= date('now','+7 day')
            """
        ).fetchone()[0]

    return {
        "total_sources": total_sources,
        "opportunities": opportunities,
        "source_lists": source_lists,
        "review": review,
        "other": other,
        "verified": verified,
        "due_week": due_week,
    }
