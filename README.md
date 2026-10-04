# Competition Tracker

A small personal web app for turning saved Instagram competition posts into a searchable opportunity database.

It is designed for competitions, essay contests, olympiads, scholarships, research challenges, hackathons, art contests, and similar opportunities.

## What it does

- Imports your own **CSV or JSON** file.
- Keeps the original Instagram link and imported caption/notes.
- Uses AI to extract:
  - competition name
  - organizer
  - category
  - deadline
  - entry fee / cost
  - prize
  - eligibility
  - requirements
- Can **verify a competition on the live web** and save cited sources.
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

## 2. Configure AI

Copy the example environment file:

### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

### macOS / Linux

```bash
cp .env.example .env
```

Then edit `.env`:

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_EXTRACT_MODEL=gpt-6-luna
OPENAI_VERIFY_MODEL=gpt-6.1-sol
```

**Do not commit `.env` to GitHub.** It is already included in `.gitignore`.

The extraction model is intentionally cheaper because you may have hundreds of saved posts. Web verification is a separate action, so you can verify only opportunities you care about.

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

1. Export or manually compile your saved competition posts into CSV/JSON.
2. Import them.
3. Click **Extract** to cheaply structure the information already in the post/caption.
4. For promising competitions, click **Verify online**.
5. The verifier searches the live web, prioritizes official sources, and tries to determine the current or next cycle rather than blindly trusting an old Instagram post.

This separation is intentional: verifying every saved item can cost more than extracting it, and many saved posts may already be expired or irrelevant.

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
