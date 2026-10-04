from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Request, UploadFile
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


def go(message: str = "", error: str = "") -> RedirectResponse:
    query = []
    if message:
        query.append(f"message={quote(message)}")
    if error:
        query.append(f"error={quote(error)}")
    suffix = "?" + "&".join(query) if query else ""
    return RedirectResponse(url="/" + suffix, status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: str = "",
    category: str = "",
    verified: str = "",
    status: str = "",
    message: str = "",
    error: str = "",
):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "competitions": db.list_competitions(q=q, category=category, verified=verified, status=status),
            "categories": db.categories(),
            "stats": db.stats(),
            "q": q,
            "category": category,
            "verified": verified,
            "status": status,
            "message": message,
            "error": error,
            "has_api_key": bool(os.getenv("OPENAI_API_KEY")),
        },
    )


@app.post("/import")
async def import_file(file: UploadFile = File(...)):
    try:
        data = await file.read()
        rows = parse_upload(file.filename or "", data)
        count = db.insert_imported(rows)
        return go(message=f"Imported {count} new competition records.")
    except Exception as exc:
        return go(error=str(exc))


@app.post("/competitions/{comp_id}/extract")
def extract(comp_id: int):
    record = db.get_competition(comp_id)
    if not record:
        return go(error="Competition not found.")
    try:
        result = extract_competition(record)
        if not result.is_competition:
            db.update_extraction(comp_id, {"status": "not_competition", "ai_confidence": result.confidence})
            return go(message="AI marked that record as not a competition.")
        values = result.model_dump(exclude={"is_competition"})
        values["ai_confidence"] = values.pop("confidence")
        if not values.get("official_url"):
            values["official_url"] = record.get("imported_official_url")
        if record.get("status") == "unreviewed":
            values["status"] = "extracted"
        db.update_extraction(comp_id, values)
        return go(message="Extracted competition details.")
    except Exception as exc:
        return go(error=str(exc))


@app.post("/competitions/{comp_id}/verify")
def verify(comp_id: int):
    record = db.get_competition(comp_id)
    if not record:
        return go(error="Competition not found.")
    try:
        result, sources = verify_competition(record)
        values = result.model_dump()
        values["ai_confidence"] = values.pop("confidence")
        values["verification_notes"] = values.pop("notes")
        db.update_verification(comp_id, values, sources)
        return go(message="Verified against current web sources.")
    except Exception as exc:
        return go(error=str(exc))


@app.post("/extract-next")
def extract_next(batch_size: int = Form(default=10)):
    batch_size = max(1, min(batch_size, 25))
    records = [r for r in db.list_competitions(status="unreviewed")][:batch_size]
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


@app.post("/competitions/{comp_id}/delete")
def delete(comp_id: int):
    db.delete_competition(comp_id)
    return go(message="Deleted record.")
