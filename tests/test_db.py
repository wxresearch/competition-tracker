from datetime import date, datetime, timedelta, timezone

import db


def setup_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "tracker.db")
    db.init_db()


def source_row(url: str, caption: str, timestamp: int):
    return {
        "import_title": "Example Source",
        "raw_text": caption,
        "instagram_url": url,
        "imported_official_url": "",
        "source_timestamp": timestamp,
        "owner_name": "Example Source",
        "owner_username": "example",
        "owner_url": "",
    }


def test_duplicate_sources_merge_into_one_cycle(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    ts = int(datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp())
    caption = "Oct 29: Conrad Challenge"

    db.insert_imported(
        [
            source_row("https://www.instagram.com/p/source1/", caption, ts),
            source_row("https://www.instagram.com/p/source2/", caption, ts),
        ]
    )

    items = db.list_competitions(view="opportunities")
    assert len(items) == 1
    assert items[0]["competition_name"] == "Conrad Challenge"
    assert items[0]["source_count"] == 2
    assert items[0]["cycle_year"] == 2026


def test_manual_correction_survives_rebuild(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    ts = int(datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp())

    db.insert_imported(
        [source_row(
            "https://www.instagram.com/p/source1/",
            "Oct 2: Harvard Moot Court Legal Essay Contest",
            ts,
        )]
    )
    item = db.list_competitions(view="opportunities")[0]

    db.update_manual(
        item["id"],
        {
            "competition_name": "Harvard Moot Court Essay Contest",
            "deadline": "2026-10-02",
            "cycle_year": 2026,
            "cycle_label": "2026",
        },
    )

    db.analyze_all_source_posts(force=True)
    items = db.list_competitions(view="opportunities")

    assert len(items) == 1
    assert items[0]["competition_name"] == "Harvard Moot Court Essay Contest"
    assert items[0]["manually_edited"] == 1


def test_verification_levels_and_cache(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    ts = int(datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp())
    db.insert_imported(
        [source_row(
            "https://www.instagram.com/p/source1/",
            "Oct 29: Conrad Challenge",
            ts,
        )]
    )
    item = db.list_competitions(view="opportunities")[0]

    official = "https://www.conradchallenge.org/rules"
    db.update_verification(
        item["id"],
        {
            "competition_name": "Conrad Challenge",
            "official_url": official,
            "status": "open",
            "verification_notes": "Current official rules checked.",
            "ai_confidence": 0.8,
        },
        [{"title": "Official rules", "url": official}],
    )

    refreshed = db.get_competition(item["id"])
    assert refreshed["verification_level"] == "official"
    assert db.verification_is_fresh(item["id"]) is True


def test_deadline_intelligence(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    ts = int(datetime.now(timezone.utc).timestamp())
    db.insert_imported(
        [source_row(
            "https://www.instagram.com/p/source1/",
            "Oct 29: Conrad Challenge",
            ts,
        )]
    )
    item = db.list_competitions(view="opportunities")[0]
    due = (date.today() + timedelta(days=3)).isoformat()
    db.update_manual(item["id"], {"deadline": due})

    refreshed = db.list_competitions(view="opportunities")[0]
    assert refreshed["days_remaining"] == 3
    assert refreshed["urgency_bucket"] == "week"
    assert refreshed["urgency_text"] == "3 days left"


def test_verification_job_lifecycle(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    ts = int(datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp())
    db.insert_imported(
        [source_row(
            "https://www.instagram.com/p/source1/",
            "Oct 29: Conrad Challenge",
            ts,
        )]
    )
    item = db.list_competitions(view="opportunities")[0]

    job_id = db.create_verification_job(item["id"])
    assert db.create_verification_job(item["id"]) == job_id
    db.set_verification_job(job_id, "running")
    db.set_verification_job(job_id, "done")

    with db.connect() as conn:
        row = conn.execute(
            "SELECT status, started_at, finished_at FROM verification_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    assert row["status"] == "done"
    assert row["started_at"] is not None
    assert row["finished_at"] is not None


def test_unknown_cycle_duplicates_are_flagged_not_auto_merged(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    ts = int(datetime(2026, 9, 20, tzinfo=timezone.utc).timestamp())

    db.insert_imported(
        [
            source_row(
                "https://www.instagram.com/p/unknown1/",
                "Conrad Challenge",
                ts,
            ),
            source_row(
                "https://www.instagram.com/p/unknown2/",
                "Conrad Challenge",
                ts,
            ),
        ]
    )

    items = [
        item
        for item in db.list_competitions(view="opportunities")
        if item["competition_name"] == "Conrad Challenge"
    ]
    assert len(items) == 2
    assert all(item["cycle_year"] is None for item in items)
    assert all(item["possible_duplicates"] == 1 for item in items)
