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
