# Competition Tracker — Competition, Scholarship, and Contest Opportunity Organizer

Competition Tracker is a Python web app that turns saved Instagram posts and imported files into a **searchable database of competitions, scholarships, contests, and other opportunities**. It helps users find relevant opportunities, review requirements, track deadlines, and keep application information organized in one place.

Instead of scrolling through saved social media posts to remember an opportunity, users can import posts, sort out irrelevant or uncertain results, and maintain structured records with information such as eligibility, prizes, deadlines, and entry requirements.

## Features

- **Instagram saved-post import:** Import opportunity information from JSON or CSV files.
- **Opportunity database:** Organize competitions, scholarships, contests, and similar opportunities in one searchable location.
- **Deadline tracking:** Record deadlines, eligibility rules, prizes, and entry requirements.
- **Search and filtering:** Find opportunities that match your interests or application needs.
- **Online verification:** Check opportunity details against online sources.
- **Optional AI extraction:** Assist with extracting information from posts or sources when configured.
- **Review workflow:** Inspect uncertain results and remove irrelevant entries.
- **Data cleanup:** Edit opportunities and merge duplicates.
- **Local persistence:** Keep saved entries on your computer between sessions.

## Getting Started

Requires Python. On Windows, run:

```bat
git clone https://github.com/wxresearch/competition-tracker.git
cd competition-tracker
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload
```

Open `http://127.0.0.1:8000` in your browser.

Import an Instagram `saved_posts.json` export or a CSV file to begin organizing opportunities.

Online verification can work without a paid OpenAI account. For optional integrations, use the settings described in `.env.example`; supported API-key options include Tavily, Gemini, and Groq.

Keep your `.env` file private and out of version control.

## Who Is It For?

Competition Tracker is useful for:

- Students searching for academic competitions and scholarships.
- Scholarship applicants managing multiple deadlines.
- Artists, designers, and creators looking for contests.
- Researchers exploring academic opportunities.
- Anyone who saves competition announcements online and wants a more reliable way to organize them.

## Technologies and Topics

**Python · Web application · FastAPI · Competition tracker · Scholarship tracker · Contest organizer · Instagram saved posts · Deadline management · Opportunity discovery · Searchable database · Data import · Duplicate detection · AI-assisted information extraction**

## Project Repository

https://github.com/wxresearch/competition-tracker

## License

MIT. See the repository's `LICENSE` file.
