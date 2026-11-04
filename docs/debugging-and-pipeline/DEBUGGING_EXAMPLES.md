# LinkedIn Activity Analyzer – Practical Debugging Scenarios

This guide details five real-world production incident scenarios with authentic log excerpts, diagnostic traces, root cause explanations, code resolutions, and verification commands.

---

## Scenario 1: Videos Display as Text-Only Cards Without Players or Thumbnails

### Symptom
A user analyzes a LinkedIn creator known for posting videos. The timeline cards display post text and metrics, but show no video player, no thumbnail preview, and no video indicator badge.

### Diagnostic Log Output
```text
[2026-11-03 14:10:02] [INFO] [services.pipeline_service] [RunID: run_20261103_141002_a1b2c3] [Stage 6: DOM Extraction] Started - Parsing activity cards
[2026-11-03 14:10:03] [DEBUG] [linkedin.playwright_service] [RunID: run_20261103_141002_a1b2c3] Post 1 element found: type=post, video=None, thumb=None
[2026-11-03 14:10:04] [INFO] [services.pipeline_service] [RunID: run_20261103_141002_a1b2c3] [Stage 6: DOM Extraction] Completed in 2011ms (items_count: 8)
[2026-11-03 14:10:05] [INFO] [services.pipeline_service] [RunID: run_20261103_141002_a1b2c3] Pipeline run run_20261103_141002_a1b2c3 finished: summary={"cards_found": 8, "videos_detected": 0}
```

### Root Cause Analysis
1. LinkedIn rendered the video player inside a VideoJS container (`class="video-js"`) without a direct `<video src="...">` attribute. Instead, the video stream was enclosed inside a JSON-encoded `data-sources` attribute, which was ignored by basic regex matching.
2. In addition, the poster image was hosted on a lazy-loaded `img.video-thumbnail` tag that had not yet triggered its `src` attribute.

### Code Fix Applied
In `backend/linkedin/playwright_service.py` inside `_extract_video_info()`:
```python
# 1. Parse LinkedIn VideoJS data-sources JSON
ds_m = re.search(r'data-sources="([^"]+)"', html_chunk)
if ds_m:
    try:
        import json as _json
        srcs = _json.loads(html.unescape(ds_m.group(1)))
        if isinstance(srcs, list) and srcs and srcs[0].get("src"):
            video_url = srcs[0]["src"]
            has_video = True
    except Exception:
        pass

# 2. Extract poster thumbnail as visual fallback
if not video_thumbnail:
    poster_m = re.search(r'poster="([^"]+)"', html_chunk, re.IGNORECASE)
    if poster_m:
        video_thumbnail = html.unescape(poster_m.group(1)).strip()
```

### Verification
Run the unit test suite verifying VideoJS data-sources extraction:
```bash
python -m unittest tests/test_video_and_pipeline.py -k test_video_tag_with_data_sources_json
```

---

## Scenario 2: LinkedIn Session Expired Mid-Scrape

### Symptom
Scraping initiates normally, but after scrolling 2 pages, the scraper crashes or returns zero activities, and the browser is redirected to `/checkpoint/lg/login`.

### Diagnostic Log Output
```text
[2026-11-03 15:22:10] [INFO] [services.pipeline_service] [RunID: run_20261103_152210_d4e5f6] [Stage 4: Profile Navigation] Started - Navigating to target feed
[2026-11-03 15:22:12] [DEBUG] [linkedin.playwright_service] [RunID: run_20261103_152210_d4e5f6] Navigated to URL: https://www.linkedin.com/checkpoint/lg/login?session_redirect=...
[2026-11-03 15:22:13] [WARNING] [linkedin.playwright_service] [RunID: run_20261103_152210_d4e5f6] Session invalid: redirected to LinkedIn login/checkpoint page
[2026-11-03 15:22:13] [ERROR] [services.pipeline_service] [RunID: run_20261103_152210_d4e5f6] [Stage 4: Profile Navigation] Failed: LinkedIn session expired or requires re-authentication
```

### Root Cause Analysis
The session cookie `li_at` stored in `.env` expired or was invalidated when the user logged in from another browser or IP address. Playwright detected a URL change redirecting to `/checkpoint/` or `/login`.

### Resolution Steps
1. The scraper detects the checkpoint URL and exits gracefully, setting `status_type = "AUTH_EXPIRED"` and `retrieval_completeness = "UNAVAILABLE"`.
2. The pipeline preserves previously cached historical analysis in MongoDB/JSON so the dashboard continues serving prior data.
3. Update `.env` with a fresh `li_at` cookie from an active LinkedIn browser session.

---

## Scenario 3: MongoDB Connection Dropped (JSON Storage Fallback)

### Symptom
MongoDB Community Server is stopped for maintenance. Requests continue to execute without 500 errors, but the team lead needs to know if data is safely persisted.

### Diagnostic Log Output
```text
[2026-11-03 16:45:00] [WARNING] [db.mongo] [RunID: system] Primary MongoDB ping failed: ServerSelectionTimeoutError('localhost:27017: [WinError 10061] No connection could be made')
[2026-11-03 16:45:00] [WARNING] [services.analysis_storage] [RunID: run_20261103_164500_789abc] MongoDB unavailable. Persisting latest analysis directly to JSON fallback: data/latest_analysis.json
[2026-11-03 16:45:01] [INFO] [services.analysis_storage] [RunID: run_20261103_164500_789abc] Analysis successfully saved to JSON fallback storage.
[2026-11-03 16:45:01] [INFO] [app] [RunID: system] POST /api/analyze -> 200 (45.2ms)
```

### Root Cause Analysis
The application implements an **adaptive dual-store pattern**:
- When MongoDB Local is accessible, it acts as the primary datastore and writes to JSON files simultaneously.
- If PyMongo throws a `ServerSelectionTimeoutError`, the storage adapter catches the exception, logs a warning, and saves directly to `backend/data/latest_analysis.json` and `activities.json`.

### Verification & Recovery
1. When MongoDB is restarted, run the idempotent migration tool:
   ```bash
   python backend/scripts/migrate_json_to_mongodb.py --dry-run
   ```
2. Verify live counts match and zero data was lost.

---

## Scenario 4: Date Filter Returns Zero Activities Unexpectedly

### Symptom
User selects "Last 7 Days" filter for an active profile, but the timeline says "No activity found for this date range" even though posts exist.

### Diagnostic Log Output
```text
[2026-11-03 17:02:11] [INFO] [services.pipeline_service] [RunID: run_20261103_170211_334455] [Stage 6: DOM Extraction] Found 14 raw post cards
[2026-11-03 17:02:12] [DEBUG] [services.analysis_service] [RunID: run_20261103_170211_334455] Post date '2026-10-15' excluded by filter window: 2026-10-27 -> 2026-11-03
[2026-11-03 17:02:12] [INFO] [services.pipeline_service] [RunID: run_20261103_170211_334455] [Stage 7: Data Normalization] Completed (items_count: 0)
[2026-11-03 17:02:12] [INFO] [services.pipeline_service] [RunID: run_20261103_170211_334455] Pipeline completed with 0 filtered items.
```

### Root Cause Analysis
1. LinkedIn posts relative dates like "2w" (2 weeks ago). The parser accurately calculated `2026-10-15`, which falls outside the 7-day window.
2. The user interface was functioning accurately, but lacked clear visual feedback distinguishing between an empty scrape versus a filtered scrape.

### Code Fix Applied
The UI was updated in `dashboard.js` to render an informative state:
```javascript
// Display date filter range alongside historical period
historicalRangeEl.textContent = `Historical Period: ${formatDateRangeDDMMYY(start, end)}`;
```
This reassures the user that the system queried the profile correctly, and allows them to click "All Dates" to reveal the full history.

---

## Scenario 5: Rate Limited by LinkedIn (HTTP 999 Request Denied)

### Symptom
After analyzing several profiles in succession, the analysis returns an empty state or reports automation error.

### Diagnostic Log Output
```text
[2026-11-03 18:30:45] [INFO] [services.pipeline_service] [RunID: run_20261103_183045_998877] [Stage 4: Profile Navigation] Started - Navigating to feed
[2026-11-03 18:30:46] [WARNING] [linkedin.playwright_service] [RunID: run_20261103_183045_998877] LinkedIn returned HTTP 999: Request Denied (Anti-scraping rate limit)
[2026-11-03 21:47:18] [WARNING] [services.analysis_service] [RunID: run_20261103_183045_998877] Preserving prior historical analysis for https://www.linkedin.com/in/... (15 activities preserved)
[2026-11-03 21:47:18] [INFO] [services.pipeline_service] [RunID: run_20261103_183045_998877] Pipeline run finished with status: completed (historical fallback engaged)
```

### Root Cause Analysis
HTTP 999 is LinkedIn's proprietary rate-limiting response code. When automated requests exceed threshold frequency, LinkedIn temporarily rejects connections from the scraper's IP address.

### Resilience Strategy
1. **Preserved History Protection**: `analysis_service.py` intercepts HTTP 999, queries `profile_analysis_history` in MongoDB, and serves the last verified successful analysis.
2. **Exponential Jitter**: Playwright adds randomized delays (`time.sleep(random.uniform(1.5, 3.2))`) between scroll actions to mimic natural user browsing.

---

## Scenario 6: Profile Data Mismatch (Selecting Sneha Patel Displays Pragyanand Sahoo)

### Symptom
A user clicks on "Sneha Patel" from the connections list and initiates an activity analysis. When the dashboard loads, the activity feed displays posts, comments, and engagement metrics belonging to an entirely different user ("Pragyanand Sahoo").

### Diagnostic Log Output
```text
[2026-11-03 22:15:10] [INFO] [activity_routes] [RunID: run_20261103_221510_abc123] Received analysis request for profile: https://www.linkedin.com/in/
[2026-11-03 22:15:10] [DEBUG] [activities_storage] [RunID: run_20261103_221510_abc123] Querying MongoDB activities with regex: 'https://www.linkedin.com/in/'
[2026-11-03 22:15:10] [DEBUG] [activities_storage] [RunID: run_20261103_221510_abc123] Matched 19 documents (matched profile: https://www.linkedin.com/in/pragyanandsaho)
[2026-11-03 22:15:11] [INFO] [analysis_routes] [RunID: run_20261103_221510_abc123] GET /api/analysis/latest returned latest analysis without subject validation
```

### Root Cause Analysis
1. **Frontend Omission**: When clicking a connection in `frontend/js/connections.js`, `selectConnection()` saved only the connection name to storage, omitting `profileUrl`, `headline`, and `picture`.
2. **Incomplete Default URL**: `frontend/js/activity.js` initialized its default profile URL to `"https://www.linkedin.com/in/"` (without a trailing user slug).
3. **Unanchored MongoDB Query**: `backend/services/activities_storage.py` searched MongoDB using `re.compile(re.escape(url))` on `"https://www.linkedin.com/in/"`. Because the query was an unanchored substring regex, it matched every profile URL in the database, returning Pragyanand Sahoo's 19 records.
4. **Fallback Slug Leak**: `backend/services/analysis_storage.py` attempted to strip slashes on the URL, resulting in an invalid slug `"httpswwwlinkedincomin"`.
5. **Unvalidated Latest Analysis**: `frontend/js/dashboard.js` fetched `GET /api/analysis/latest` without passing identity parameters, blindly displaying whichever analysis was completed last.

### Code Fix Applied
1. **Frontend Parameter Passing**: In `frontend/js/connections.js`, `selectConnection()` now explicitly receives and stores `profileUrl`, `headline`, and `picture`, while clearing stale session storage keys.
2. **Strict URL Validation**: Both `frontend/js/activity.js` and `backend/routes/activity_routes.py` validate URLs to reject incomplete URLs like `"https://www.linkedin.com/in/"` with an explicit error.
3. **Bounded Slug Regex in MongoDB**: In `backend/services/activities_storage.py`, canonical slugs are extracted and queries are strictly bounded:
   ```python
   target_slug = _extract_canonical_slug(url)
   query = {"profile_url": {"$regex": rf"/in/{re.escape(target_slug)}(?:/|\?|$)", "$options": "i"}}
   ```
4. **Identity Verification on Latest Analysis**: In `backend/routes/activity_routes.py`, `GET /api/analysis/latest` now verifies that the requested `subject_id` matches the document. If mismatched, it loads historical records for that specific slug or returns 404 with `has_data: False`.
5. **Dashboard Empty State**: `frontend/js/dashboard.js` sends identity query params and renders a clean empty state if no verified analysis exists for the selected profile.


