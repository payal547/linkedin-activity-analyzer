# LinkedIn Activity Analyzer

A Flask web app for connecting a LinkedIn account, viewing the profile fields authorized by LinkedIn, and analyzing an activity CSV export with date and activity type filters.

## Run locally

Install dependencies and configure the LinkedIn OAuth application:

```powershell
pip install -r requirements.txt
$env:LINKEDIN_CLIENT_ID = "your-client-id"
$env:LINKEDIN_CLIENT_SECRET = "your-client-secret"
$env:LINKEDIN_REDIRECT_URI = "http://127.0.0.1:5000/auth/linkedin/callback"
$env:SECRET_KEY = "a-long-random-secret"
python backend/app.py
```

Open `http://127.0.0.1:5000`. The redirect URI must also be registered in the LinkedIn developer app. LinkedIn controls which OAuth products and APIs an app can access; this app requests the OpenID Connect `openid profile email` scopes. For this single-process local app, access tokens stay in server memory and are cleared by restarting the server; production multi-process use should replace this store with a shared server-side session/token store.

## Paste any LinkedIn link

The landing page works out which profile a pasted link belongs to:

| You paste | What happens |
| --- | --- |
| Profile link (`/in/username`, with tracking parameters, country subdomain or `/details/...`), mobile link (`/mwlite/in/...`), or a bare username | Used directly |
| Post link (`/posts/username_...`) | The author is read from the link |
| Feed/activity link (`/feed/update/urn:li:activity:...`), article (`/pulse/...`) or `lnkd.in` short link | Opened with your LinkedIn session to find the author |
| Company, school, job, group, search or home-feed link | Rejected with an explanation |

For a profile that is not already in your connections or saved profiles, the app opens it with your logged-in Chrome session (CDP on port 9222, or the saved session) and shows the person's real name, headline and photo before analysis. Without a session it falls back to the name in the URL and labels it "Name from URL (not verified)". Guest mode is never used for this lookup. Results are cached for 30 minutes.

## Analyze an activity CSV

LinkedIn does not make every member's posts, comments, or connections available through OAuth. If activity API access is unavailable, go through the dashboard and upload a CSV with these columns:

```csv
date,type,text,likes,comments,shares
2026-10-01,post,Example post,12,3,2
2026-10-04,comment,Example comment,0,0,0
```

`date` must use `YYYY-MM-DD`; `type` identifies the activity. The engagement columns and `text` are optional. The dashboard filters the uploaded rows by date and type and reports activity totals, likes, comments, shares, total engagement, average engagement, and matching entries. Uploads are read for the analysis request and are not stored. CSV uploads are limited to 5 MB and 10,000 rows.

## Project structure

- `backend/app.py` – Flask application and page routes
- `backend/auth/` – LinkedIn OAuth and profile retrieval
- `backend/routes/` – API endpoints
- `backend/services/` – Activity analysis logic
- `frontend/` – HTML, CSS, and JavaScript pages
