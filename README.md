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
- Can **verify an opportunity on the live web** with fast Tavily search and save the retrieved sources.
- Runs verification in the background so the dashboard stays responsive.
- Caches verification for 7 days by default to avoid repeated web requests.
- Distinguishes verification levels: official-source confirmed, web-sourced, conflicting, or unclear.
- Lets you manually edit/correct opportunity data, move items to Needs Review, or mark them irrelevant.
- Detects duplicate opportunities, preserves multiple Instagram source posts, and supports manual merging.
- Separates the recurring **opportunity** from its **annual cycle** (for example, Conrad Challenge vs. the 2026 cycle).
- Adds deadline intelligence: days remaining, due-this-week, due-this-month, expired, and unknown-deadline states.
- Sorts active opportunities by urgency.
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

## 2. Configure free web verification and optional AI

The tracker no longer requires OpenAI credits.

### Fast web verification

**Verify Fast** uses Tavily web search. A Tavily key is optional because the tracker can use Tavily's keyless mode.

Optional:

```env
TAVILY_API_KEY=
VERIFICATION_CACHE_DAYS=7
```

Verification runs in the background and is cached for 7 days by default. Use **Refresh verification** when you explicitly want a new web check.

### Optional AI detail extraction

Gemini or Groq are optional and are used only for the separate **AI extract details** action.

```env
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.8-flash
GEMINI_FALLBACK_MODELS=gemini-3.7-flash,gemini-3.6-flash

GROQ_API_KEY=
GROQ_MODEL=qwen/qwen3.8-27b
```

You may configure either Gemini, Groq, both, or neither.

**Never commit `.env` to GitHub.** It is already ignored by `.gitignore`.

## 3. Run it

```bash
uvicorn app:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

## Restart after closing Command Prompt

If you accidentally close Command Prompt while the app is running, **your project and saved data are not lost**. Closing the window only stops the local web server.

Your project files, `.env`, imported Instagram data, and SQLite database remain on your computer.

### Normal restart routine on Windows Command Prompt

Open a new **Command Prompt** window and run:

```cmd
cd competition-tracker
.venv\Scripts\activate
git pull
python -m uvicorn app:app --reload
```

Then open this address in your browser:

```text
http://127.0.0.1:8000
```

If everything starts correctly, Command Prompt should show something similar to:

```text
Uvicorn running on http://127.0.0.1:8000
```

### If `cd competition-tracker` does not work

Check the folders in your current location:

```cmd
dir
```

If you see `competition-tracker`, run:

```cmd
cd competition-tracker
```

If the project is in your Windows user folder, try:

```cmd
cd %USERPROFILE%\competition-tracker
```

### If the virtual environment is not active

Run:

```cmd
.venv\Scripts\activate
```

After activation, the prompt should begin with something like:

```text
(.venv) C:\Users\YourName\competition-tracker>
```

### If `uvicorn` is not recognized

Use:

```cmd
python -m uvicorn app:app --reload
```

This is also the recommended startup command on Windows.

### If port 8000 is already in use

Start the app on port 8001 instead:

```cmd
python -m uvicorn app:app --reload --port 8001
```

Then open:

```text
http://127.0.0.1:8001
```

### If `git pull` reports local changes

Do **not** delete or overwrite files immediately.

First run:

```cmd
git status
```

Review the changed files before deciding what to do.

### When to reinstall packages

You normally do **not** need to reinstall packages every time you start the app.

Only run this when `requirements.txt` has changed or you are told to update dependencies:

```cmd
python -m pip install -r requirements.txt
```

### When to edit `.env`

Only edit `.env` when changing API keys or configuration:

```cmd
notepad .env
```

Never post or commit the contents of `.env`, because it may contain private API keys.

### Things you do NOT need to redo

After the project has already been set up, you normally do **not** need to run these again:

```cmd
git clone https://github.com/wxresearch/competition-tracker.git
python -m venv .venv
copy .env.example .env
```

You also do **not** need to re-import `saved_posts.json` just because Command Prompt was closed.

Your saved tracker database is normally stored at:

```text
data\competitions.db
```

### How to stop the app normally

In the Command Prompt window running Uvicorn, press:

```text
Ctrl+C
```

Then you can safely close the window.

### Quick cheat sheet

For normal use, these are the commands to remember:

```cmd
cd competition-tracker
.venv\Scripts\activate
git pull
python -m uvicorn app:app --reload
```

Then visit:

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

1. Import or refresh your Instagram `saved_posts.json`.
2. Review **Actual opportunities** first. They are sorted by deadline urgency.
3. Use **Edit / correct** whenever a parser-generated name, deadline, category, fee, prize, or eligibility field is wrong.
4. Move uncertain items to **Needs Review** instead of deleting them.
5. Use **Not an opportunity** for false positives.
6. Watch for **possible duplicate** badges. Known same-cycle duplicates can merge automatically; unknown-cycle duplicates are left separate for manual review.
7. Use the edit page's **Merge duplicate** control when two records are clearly the same opportunity/cycle. Their Instagram source links are preserved.
8. Use **Verify Fast** for current web information. The request runs in the background and the page refreshes automatically.
9. Verification is reused for 7 days unless you press **Refresh verification**.
10. Prefer records marked **official source confirmed** over weaker web-only verification.
11. Use **AI extract details** only when you want extra structured fields from the saved caption.
12. Treat the permanent opportunity and its annual cycle separately; edit the cycle year/label when needed.

Re-importing the same Instagram export refreshes existing source records without wiping manual corrections.

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
│   ├── index.html
│   └── edit.html
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
- Personal ranking: prize vs. effort vs. eligibility vs. time left.
- Multi-stage deadlines (registration, abstract, final submission, finals).
- Eligibility matching against a personal profile.
- User accounts and cloud deployment.
- Postgres/Supabase instead of SQLite.

## License

MIT
