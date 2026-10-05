from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv()

import db
from ai import extract_competition, verify_competition
from importer import parse_upload

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Competition Tracker")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.on_event("startup")
def startup() -> None:
    db.init_db()


def go(message: str = "", error: str = "", view: str = "") -> RedirectResponse:
    query = []
    if view:
        query.append(f"view={quote(view)}")
    if message:
        query.append(f"message={quote(message)}")
    if error:
        query.append(f"error={quote(error)}")
    suffix = "?" + "&".join(query) if query else ""
    return RedirectResponse(url="/" + suffix, status_code=303)


def _run_verification_job(comp_id: int, job_id: int) -> None:
    db.set_verification_job(job_id, "running")
    try:
        record = db.get_competition(comp_id)
        if not record:
            raise RuntimeError("Opportunity not found.")
        result, sources = verify_competition(record)
        values = result.model_dump()
        values["ai_confidence"] = values.pop("confidence")
        values["verification_notes"] = values.pop("notes")
        db.update_verification(comp_id, values, sources)
        db.set_verification_job(job_id, "done")
    except Exception as exc:
        db.set_verification_job(job_id, "error", str(exc)[:1000])


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: str = "",
    category: str = "",
    verified: str = "",
    status: str = "",
    view: str = "opportunities",
    message: str = "",
    error: str = "",
):
    competitions = db.list_competitions(
        q=q,
        category=category,
        verified=verified,
        status=status,
        view=view,
    )
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "competitions": competitions,
            "categories": db.categories(),
            "stats": db.stats(),
            "q": q,
            "category": category,
            "verified": verified,
            "status": status,
            "view": view,
            "message": message,
            "error": error,
            "has_gemini_key": bool(os.getenv("GEMINI_API_KEY")),
            "has_tavily_key": bool(os.getenv("TAVILY_API_KEY")),
            "has_groq_key": bool(os.getenv("GROQ_API_KEY")),
            "ai_extract_ready": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GROQ_API_KEY")),
            "free_verify_ready": True,
            "has_active_jobs": any(c.get("verification_job_status") for c in competitions),
            "cache_days": db.VERIFICATION_CACHE_DAYS,
        },
    )


@app.post("/import")
async def import_file(file: UploadFile = File(...)):
    try:
        data = await file.read()
        rows = parse_upload(file.filename or "", data)
        result = db.insert_imported(rows)
        return go(
            message=(
                f"Import complete: {result['new']} new source posts, "
                f"{result['refreshed']} refreshed, {result['children']} opportunity links processed, "
                f"and {result.get('merged', 0)} duplicate cycles merged."
            )
        )
    except Exception as exc:
        return go(error=str(exc))


@app.get("/competitions/{comp_id}/edit", response_class=HTMLResponse)
def edit_page(request: Request, comp_id: int):
    record = db.get_competition(comp_id)
    if not record:
        return go(error="Opportunity not found.")
    return templates.TemplateResponse(
        request=request,
        name="edit.html",
        context={
            "c": record,
            "merge_candidates": db.find_merge_candidates(comp_id),
        },
    )


@app.post("/competitions/{comp_id}/edit")
def edit_save(
    comp_id: int,
    competition_name: str = Form(default=""),
    organizer: str = Form(default=""),
    category: str = Form(default=""),
    deadline: str = Form(default=""),
    deadline_text: str = Form(default=""),
    entry_fee: str = Form(default=""),
    prize: str = Form(default=""),
    eligibility: str = Form(default=""),
    requirements: str = Form(default=""),
    official_url: str = Form(default=""),
    status: str = Form(default="unreviewed"),
    cycle_year: str = Form(default=""),
    cycle_label: str = Form(default=""),
):
    try:
        year = int(cycle_year) if cycle_year.strip() else None
        db.update_manual(
            comp_id,
            {
                "competition_name": competition_name,
                "organizer": organizer,
                "category": category,
                "deadline": deadline,
                "deadline_text": deadline_text,
                "entry_fee": entry_fee,
                "prize": prize,
                "eligibility": eligibility,
                "requirements": requirements,
                "official_url": official_url,
                "status": status,
                "cycle_year": year,
                "cycle_label": cycle_label,
            },
        )
        return go(message="Saved manual corrections.", view="opportunities")
    except Exception as exc:
        return go(error=str(exc), view="opportunities")


@app.post("/competitions/{comp_id}/review-state")
def review_state(comp_id: int, state: str = Form(...)):
    try:
        db.set_review_state(comp_id, state)
        labels = {
            "active": "Returned to Actual opportunities.",
            "needs_review": "Moved to Needs review.",
            "irrelevant": "Marked irrelevant.",
        }
        return go(message=labels.get(state, "Updated review state."))
    except Exception as exc:
        return go(error=str(exc))


@app.post("/competitions/{target_id}/merge")
def merge(target_id: int, duplicate_id: int = Form(...)):
    try:
        db.merge_competitions(target_id, duplicate_id)
        return go(message="Merged duplicate opportunity and preserved its source links.")
    except Exception as exc:
        return go(error=str(exc))


@app.post("/competitions/{comp_id}/extract")
def extract(comp_id: int):
    record = db.get_competition(comp_id)
    if not record:
        return go(error="Opportunity not found.")
    if record.get("record_origin") != "split_child":
        return go(error="This is a source post, not an individual opportunity.")
    try:
        result = extract_competition(record)
        if not result.is_competition:
            db.set_review_state(comp_id, "irrelevant")
            db.update_extraction(
                comp_id,
                {"status": "not_competition", "ai_confidence": result.confidence},
            )
            return go(message="AI marked that record as not an opportunity.")
        values = result.model_dump(exclude={"is_competition"})
        values["ai_confidence"] = values.pop("confidence")
        if not values.get("official_url"):
            values["official_url"] = record.get("imported_official_url")
        if record.get("status") == "unreviewed":
            values["status"] = "extracted"
        db.update_extraction(comp_id, values)
        return go(message="Extracted opportunity details.")
    except Exception as exc:
        return go(error=str(exc))


@app.post("/competitions/{comp_id}/verify")
def verify(
    comp_id: int,
    background_tasks: BackgroundTasks,
    force: int = Form(default=0),
):
    record = db.get_competition(comp_id)
    if not record:
        return go(error="Opportunity not found.")
    if record.get("record_origin") != "split_child":
        return go(error="Verify individual opportunities, not source-list posts.")

    if not force and db.verification_is_fresh(comp_id):
        return go(
            message=(
                f"Using cached verification from the last {db.VERIFICATION_CACHE_DAYS} days. "
                "Use Refresh verification if you want to search again."
            )
        )

    job_id = db.create_verification_job(comp_id)
    background_tasks.add_task(_run_verification_job, comp_id, job_id)
    return go(message="Verification queued. The page will refresh automatically when it finishes.")


@app.post("/verify-all")
def verify_all(background_tasks: BackgroundTasks):
    records = db.list_competitions(view="opportunities")
    queued = 0
    cached = 0
    already_running = 0

    for record in records:
        comp_id = int(record["id"])

        if record.get("verification_job_status"):
            already_running += 1
            continue

        if db.verification_is_fresh(comp_id):
            cached += 1
            continue

        job_id = db.create_verification_job(comp_id)
        background_tasks.add_task(_run_verification_job, comp_id, job_id)
        queued += 1

    if queued == 0:
        return go(
            message=(
                "Nothing new to verify. "
                f"{cached} opportunities are still within the {db.VERIFICATION_CACHE_DAYS}-day cache"
                + (f" and {already_running} are already being verified." if already_running else ".")
            ),
            view="opportunities",
        )

    return go(
        message=(
            f"Queued {queued} opportunities for verification. "
            f"Skipped {cached} cached and {already_running} already running. "
            "They will process in the background and the page will refresh automatically."
        ),
        view="opportunities",
    )


@app.post("/extract-next")
def extract_next(batch_size: int = Form(default=10)):
    batch_size = max(1, min(batch_size, 25))
    records = [
        r
        for r in db.list_competitions(status="unreviewed", view="opportunities")
    ][:batch_size]
    done = 0
    errors = 0
    for record in records:
        try:
            result = extract_competition(record)
            values = result.model_dump(exclude={"is_competition"})
            values["ai_confidence"] = values.pop("confidence")
            values["status"] = "extracted" if result.is_competition else "not_competition"
            if not values.get("official_url"):
                values["official_url"] = record.get("imported_official_url")
            db.update_extraction(record["id"], values)
            done += 1
        except Exception:
            errors += 1
    return go(message=f"Processed {done} records; {errors} failed.")


@app.post("/reclassify")
def reclassify():
    result = db.analyze_all_source_posts(force=True)
    return go(
        message=(
            f"Rebuilt {result['analyzed']} source posts, processed "
            f"{result['children']} opportunity links, and merged "
            f"{result.get('merged', 0)} duplicate cycles."
        )
    )


@app.post("/delete-unresolved-sources")
def delete_unresolved_sources():
    count = db.delete_unresolved_sources()
    if count == 0:
        return go(message="No needs-exact-name source posts to delete.", view="review")
    return go(
        message=f"Deleted {count} needs-exact-name source post{'s' if count != 1 else ''}.",
        view="review",
    )


@app.post("/competitions/{comp_id}/delete")
def delete(comp_id: int):
    db.delete_competition(comp_id)
    return go(message="Deleted record.")
