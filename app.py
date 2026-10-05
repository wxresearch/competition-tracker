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
    view: str = "opportunities",
    message: str = "",
    error: str = "",
):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "competitions": db.list_competitions(q=q, category=category, verified=verified, status=status, view=view),
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
            "free_verify_ready": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GROQ_API_KEY")),
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
                f"{result['refreshed']} refreshed, and {result['children']} named opportunities extracted locally."
            )
        )
    except Exception as exc:
        return go(error=str(exc))


@app.post("/competitions/{comp_id}/extract")
def extract(comp_id: int):
    record = db.get_competition(comp_id)
    if not record:
        return go(error="Opportunity not found.")
    if record.get("record_origin") != "split_child":
        return go(error="This is a source post, not an individual opportunity. Open its extracted items instead.")
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
        message = str(exc)
        lower = message.lower()
        if "all configured free ai providers" in lower:
            return go(error="All configured free AI providers are temporarily unavailable or rate-limited. Add GROQ_API_KEY for a second-provider fallback, or try again later.")
        if "groq_api_key is not configured" in lower and not os.getenv("GEMINI_API_KEY"):
            return go(error="No free AI provider is configured. Add GEMINI_API_KEY or GROQ_API_KEY to your .env file.")
        if "503" in message or "unavailable" in lower or "high demand" in lower:
            return go(error="The current free AI provider is temporarily unavailable. Configure GROQ_API_KEY for provider failover, or try again later.")
        if "429" in message or "quota" in lower or "rate limit" in lower:
            return go(error="The free AI service hit a temporary quota/rate limit. Try again later; your local data is safe.")
        return go(error=message)


@app.post("/competitions/{comp_id}/verify")
def verify(comp_id: int):
    record = db.get_competition(comp_id)
    if not record:
        return go(error="Opportunity not found.")
    if record.get("record_origin") != "split_child":
        return go(error="Verify individual extracted opportunities, not the source-list post.")
    try:
        result, sources = verify_competition(record)
        values = result.model_dump()
        values["ai_confidence"] = values.pop("confidence")
        values["verification_notes"] = values.pop("notes")
        db.update_verification(comp_id, values, sources)
        return go(message="Verified against current web sources.")
    except Exception as exc:
        message = str(exc)
        lower = message.lower()
        if "all configured free ai providers" in lower:
            return go(error="All configured free AI providers are temporarily unavailable or rate-limited. Add GROQ_API_KEY for a second-provider fallback, or try again later.")
        if "groq_api_key is not configured" in lower and not os.getenv("GEMINI_API_KEY"):
            return go(error="No free AI provider is configured. Add GEMINI_API_KEY or GROQ_API_KEY to your .env file.")
        if "does not look like a tavily api key" in lower:
            return go(error="Your Tavily key looks invalid. Tavily keys begin with 'tvly-'. You can fix/remove TAVILY_API_KEY; keyless verification is also supported.")
        if "503" in message or "unavailable" in lower or "high demand" in lower:
            return go(error="The configured free AI providers are temporarily unavailable. Add GROQ_API_KEY for cross-provider failover, or try again later.")
        if "429" in message or "quota" in lower or "rate limit" in lower or "credits" in lower:
            return go(error="The free verification service hit its current usage limit. Try again after the service resets, or check your Gemini/Tavily free-tier usage.")
        return go(error=message)


@app.post("/extract-next")
def extract_next(batch_size: int = Form(default=10)):
    batch_size = max(1, min(batch_size, 25))
    records = [r for r in db.list_competitions(status="unreviewed", view="opportunities")][:batch_size]
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
            f"Rebuilt local analysis for {result['analyzed']} source posts and "
            f"extracted {result['children']} named opportunities. No API credits were used."
        )
    )


@app.post("/competitions/{comp_id}/delete")
def delete(comp_id: int):
    db.delete_competition(comp_id)
    return go(message="Deleted record.")
