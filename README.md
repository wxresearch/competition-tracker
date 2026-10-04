# Competition Tracker

A small personal web app for turning saved Instagram competition posts into a searchable opportunity database.

It is designed for competitions, essay contests, olympiads, scholarships, research challenges, hackathons, art contests, and similar opportunities.

## What it does

- Imports your own **CSV or JSON** file, including the current Meta/Instagram `saved_posts.json` format.
- Keeps the original Instagram link and imported caption/notes.
- Runs a **free local classifier** first to separate likely competitions, scholarships, awards, programs, and internships from college advice/resources.
- Keeps borderline posts in a **Needs review** view instead of discarding them.
- Uses AI only when you choose to extract:
  - competition name
  - organizer
  - category
  - deadline
  - entry fee / cost
  - prize
  - eligibility
  - requirements
- Can **verify an opportunity on the live web for free-tier usage** with Tavily + Gemini and save the retrieved sources.
- Sorts records by deadline.
- Filters by category, status, and verification state.
- Uses a local **SQLite** database, so you do not need to set up Postgres for the MVP.

## 1. Clone and set up

```bash
git clone https://github.com/wxresearch/competition-tracker.git
cd competition-tracker

python -m venv .venv
```

Activate the environment:

### Windows PowerShell

```powershell
.venv\Scripts\Activate.ps1
```

### macOS / Linux

```bash
source .venv/bin/activate
```

Install packages:

```bash
pip install -r requirements.txt
```

## 2. Configure free AI + web verification

This project no longer requires OpenAI credits.

It uses:

- **Google Gemini** for structured extraction and reasoning. The default is `gemini-3.8-flash`.
- **Tavily** for live web search and page extraction.

Create the two API keys:

1. Gemini: https://aistudio.google.com/apikey
2. Tavily: https://app.tavily.com/

Copy the example environment file.

### Windows Command Prompt

```cmd
copy .env.example .env
notepad .env
```

### macOS / Linux

```bash
cp .env.example .env
```

Set:

```env
GEMINI_API_KEY=your_gemini_key_here
GEMINI_MODEL=gemini-3.8-flash
TAVILY_API_KEY=your_tavily_key_here
```

**Never commit `.env` to GitHub.** It is already ignored by `.gitignore`.

### What uses credits?

- Importing Instagram JSON: **local/free**
- Splitting roundup captions into named opportunities: **local/free**
- Local filtering: **local/free**
- Gemini detail extraction: uses Gemini's API free tier when available
- **Verify Free**: uses Tavily web search/extraction plus Gemini

The verifier performs an advanced Tavily search, selects the strongest sources, extracts up to five pages, and asks Gemini to structure only the evidence returned by Tavily. Official organizer/rules/application pages are prioritized over social posts and aggregators.

## 3. Run it

```bash
uvicorn app:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

## 4. Import format

The easiest format is CSV:

```csv
title,caption,instagram_url,official_url
John Doe Essay Prize,"Essay competition. Deadline May 31. Prize mentioned in post.",https://instagram.com/p/abc123/,
Science Challenge,"High school competition; I need to check the fee and prize.",https://instagram.com/p/xyz456/,
```

Recognized aliases include:

- title: `competition_name`, `name`, `title`, `competition`, `event`
- text: `caption`, `description`, `text`, `raw_text`, `notes`, `content`
- Instagram URL: `instagram_url`, `instagram`, `post_url`, `permalink`, `url`, `link`
- official URL: `official_url`, `website`, `source_url`, `competition_url`

A plain JSON array using the same field names also works.

## Recommended workflow

1. Import your Instagram `saved_posts.json`.
2. The app locally separates source posts from actual named opportunities and splits list captions into child records.
3. Review **Actual opportunities**, **Source posts**, and **Needs review**.
4. Use **Gemini extract details** when a caption contains useful details that are not yet structured.
5. Use **Verify Free** on opportunities you care about. Tavily searches the live web and Gemini evaluates the retrieved evidence.
6. Prefer verified current-cycle data over dates copied from old Instagram posts.

Re-importing the same Instagram export refreshes existing source records rather than duplicating them.

## Important limitation

This app does **not scrape Instagram**. Instagram links are stored as references. For best results, include the post caption, copied text, OCR text, or your own notes in the `caption`/`text` column.

If your import contains only Instagram URLs, the verifier may still identify some public competitions through web search, but results will be much less reliable than when you provide the saved post's text.

## Tests

```bash
pytest
```

## Project structure

```text
competition-tracker/
├── app.py
├── ai.py
├── classifier.py
├── splitter.py
├── db.py
├── importer.py
├── models.py
├── templates/
│   └── index.html
├── static/
│   └── style.css
├── tests/
│   └── test_importer.py
├── sample_import.csv
├── requirements.txt
└── .env.example
```

## Possible next upgrades

- Image/screenshot upload with vision extraction.
- Calendar view and `.ics` export.
- Reminder emails before deadlines.
- Duplicate detection.
- Personal ranking: prize vs. effort vs. eligibility vs. time left.
- Multi-stage deadlines (registration, abstract, final submission, finals).
- User accounts and cloud deployment.
- Postgres/Supabase instead of SQLite.

## License

MIT
