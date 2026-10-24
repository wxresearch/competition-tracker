# Competition Tracker

A personal web app for organizing competitions, scholarships, contests, and other opportunities saved from Instagram.

## Features

- Import saved Instagram posts from JSON or CSV.
- Find opportunities, review uncertain posts, and remove irrelevant ones.
- Track deadlines, eligibility, prizes, and entry requirements.
- Verify opportunities online and optionally extract details with AI.
- Edit entries, merge duplicates, and filter your opportunities.

## Getting started

Using **Windows Command Prompt** with Python installed:

```cmd
git clone https://github.com/wxresearch/competition-tracker.git
cd competition-tracker
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload
```

Open http://127.0.0.1:8000 in your browser.

Import your Instagram `saved_posts.json` export or a CSV file to get started. Web verification works without a paid OpenAI account. Optional API keys for Tavily, Gemini, or Groq can be added to a local `.env` file.

## Running it again

From the project folder in CMD:

```cmd
git pull
.venv\Scripts\activate.bat
python -m uvicorn app:app --reload
```

Your saved data remains on your computer when you close the app.

## License

MIT
