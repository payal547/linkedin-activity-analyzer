# Bug Report — LinkedIn Activity Analyzer

**Date:** 2026-11-25
**Commit reviewed:** `8848ced`
**Method:** I ran the full test suite, ran pyflakes on `backend/`, reproduced each crash with the Flask test client, and read the analysis, storage and scraper code.

## Test suite result

`python -m pytest`: **267 passed, 33 failed**.

| Cause | Failures |
| --- | --- |
| No LinkedIn session on the test machine (`AUTH_REQUIRED`) | 19 |
| Real code bugs (bugs 2 and 5 below) | 4 |
| Test expectations that no longer match the code | 9 |
| `.env.example` missing from the local checkout | 1 |

To separate the first group I re-ran the tests with `validate_session_state` patched to `VALID`. That left 14 failures, and every one of them is accounted for below.

## Fix status (same day)

After the fixes, `python -m pytest` gives **307 passed, 0 failed**. The run was repeated twice with the same result. pyflakes reports no undefined names in `backend/`.

| # | Bug | Status |
| --- | --- | --- |
| 1 | CSV/ZIP upload 500 | Fixed: `import os` |
| 2 | `/api/analysis/latest` 500 | Fixed: `req_subject_name` read from the query, falling back to the session profile |
| 3 | Imported rows leak to other profiles | Fixed: rows are tagged on upload and matched by canonical slug. Untagged rows are used only for a "self" analysis |
| 4 | Video filter `NameError` | Fixed |
| 5 | Slug functions accept junk | Fixed: both functions accept a raw slug only if the input already is one (`[a-z0-9_-]+`) |
| 6 | Reversed DD/MM range reinterpreted | Not changed: this is intended behaviour, covered by `test_slash_date_inversion_auto_resolution` |
| 7 | Batch history limits use observed dates | Fixed, including the `analysis_storage.py:346` fallback (see bug 7) |
| 8 | Pipeline runs left `running` | Fixed: the `after_request` hook marks the still-running run `failed` on any 4xx/5xx response |

New regression tests are in `tests/test_bug_report_fixes.py`. `tests/conftest.py` now keeps every test away from linkedin.com and the developer's Chrome.

---

## Bugs

### 1. High: every CSV/ZIP upload to `/api/analyze` returns HTTP 500

`backend/routes/activity_routes.py:576` calls `os.getenv(...)`, but the module never imports `os`. The upload branch fails before it reads the file, so the offline-import workflow (Reactions.csv, Comments.csv, Shares.csv, export ZIP) cannot be used at all.

**Repro:** POST any CSV to `/api/analyze` as multipart `file`.
**Result:** `NameError: name 'os' is not defined`, followed by a 500 response. The pipeline run is also left in the `running` state.
**Fix:** add `import os` at the top of `activity_routes.py`.

### 2. High: `/api/analysis/latest` crashes with HTTP 500 when no analysis matches the requested profile

The route reads `req_subject_name` at `activity_routes.py:1339`, `1362` and `1366`, but that variable is never assigned (only `req_subject_id` is). Two paths reach it:

- the dashboard opens for a profile that has never been analyzed (line 1362), so it gets a 500 instead of the intended 404 "No analysis has been run yet";
- the dashboard requests saved history with a date or activity-type filter (line 1339), so the preserved-history fallback crashes instead of returning filtered history.

**Repro:** `GET /api/analysis/latest?profile_url=https://www.linkedin.com/in/nobody-xyz-123`. This returns 500.
**Fix:** define it near the other request args, e.g. `req_subject_name = (request.args.get("subject_name") or "").strip()`.
**Tests that cover it:** `test_latest_analysis_is_profile_specific`, `test_sneha_patel_never_receives_pragyanand_sahoo_activities`, `test_6_dashboard_latest_endpoint_profile_verification`, `test_analysis_latest_date_range_isolation`.

### 3. High: uploaded activities are shown as the activity of every profile analyzed afterwards

`backend/services/activity_service.py:54` accepts any imported row with no subject name, id or URL:

```python
elif not a_name and not a_id and not a_url:
    matching.append(a)
```

The CSV parsers in `linkedin/data_parser.py` never set `subject_*`, and the upload branch of `/api/analyze` does not tag the rows either, so every imported row matches. `session["imported_activity_id"]` also stays set after the upload. As a result, every later single or batch analysis of any profile returns the user's own uploaded rows, labelled as that profile's activity, and skips the scraper.

**Repro (verified):** calling `get_activities_for_subject(subject_url="https://www.linkedin.com/in/satyanadella/", session_activities=<rows parsed from a one-line CSV>)` returns the CSV row with the status "Loaded 1 real activities from uploaded LinkedIn archive."

This breaks the profile-isolation guarantee the project documents. Bug 1 currently hides it; fixing bug 1 alone will expose it.

**Fix:**

- Tag imported rows with the `subject_url` / `subject_id` from the upload form.
- Drop the "no identity matches everything" branch.
- Compare canonical slugs instead of URL substrings. Line 48 uses `s_url in a_url`, so `/in/sneha` matches `/in/sneha-patel` whenever a trailing slash is missing.

### 4. Medium: the "Videos only" filter fails with an automation error when a profile has no videos

`backend/linkedin/playwright_service.py:2019` uses `display_name`, which is a local variable of `_extract_activities_from_public_html` (line 767) and does not exist in `scrape_linkedin_profile_activities`.

**Trigger:** an authenticated session, activity type = Videos only, and a profile that has activities but none of them videos.
**Result:** `NameError`. `activity_service.py:138` catches it, so the user sees "Activity could not be retrieved from LinkedIn. (name 'display_name' is not defined)" with `AUTOMATION_ERROR` / `PARTIAL_CEILING_REACHED`. The intended message was "No video activities found for … in the selected period."
**Fix:** use `target_name or slug.replace('-', ' ').title()` on that line.

### 5. Low: the two `extract_slug` functions disagree, and the saved-profile one accepts junk

`saved_profiles_storage.extract_slug` → `parse_linkedin_url` (line 137) treats any non-URL word as a valid raw slug, including `httpswwwlinkedincomin` and `not-a-linkedin-url`. `analysis_storage.extract_slug` rejects those values. A stripped root URL can therefore become a saved profile, and `/api/profile/validate-url` returns 200 for `"not-a-linkedin-url"`.
**Tests:** `test_3_canonical_slug_matching_is_strict`, `test_validate_profile_url_invalid`.
**Fix:** use one shared slug function and apply the same reject list in `parse_linkedin_url`.

### 6. Low: a reversed DD/MM/YYYY range is silently analyzed as a different period

`activity_routes.py:1031-1041`: if a DD/MM range has start > end, the code re-reads both dates as MM/DD and continues. For example, `10/02/2026 → 12/01/2026` (10 Feb → 12 Jan, a user mistake) becomes 2 Oct → 1 Dec 2026, and the analysis runs on that period without warning. It should return the "Starting date must be on or before ending date" error.

The activity-filter page sends ISO dates, so this only affects API callers and slash-formatted input.

### 7. Low: batch-level history limits are built from the observed activity dates, not the requested range

`activity_routes.py:870-871` passes each profile's `date_range` (the min and max of the activities found) as `requested_start` / `requested_end` to `_build_history_limits_and_coverage`. This is the same class of bug as BUG_AUDIT.md #5, which was fixed for the single-profile path only. Use each profile's `requested_date_range` instead.

`analysis_storage.py:346` has the same fallback (`requested_date_range or date_range`). When a result has no `requested_date_range`, the single-day observed range of a failed attempt is used to filter preserved history, which hides all of it. This caused three test failures, including the dashboard HTTP 999 banner test. History is now filtered only by `requested_date_range`.

### 8. Low: validation errors in `/api/analyze` leave pipeline runs in the `running` state

The early `return jsonify(...), 400` paths do not call `complete_pipeline_run`:

- batch invalid slug or dates: lines 719, 750, 753;
- single-profile invalid URL or dates: lines 950, 957, 1049;
- the crash in bug 1.

`/api/pipeline/latest` and `/api/pipeline/runs` then report those runs as still in progress. A batch validation failure at profile *N* also returns after profiles 1…N-1 were already scraped and saved.

---

## Test-suite problems (not product bugs)

1. **Tests depend on a real LinkedIn session.** About 19 scraper and workflow tests mock `sync_playwright` but not `validate_session_state`. With no session they stop at `AUTH_REQUIRED`. With a session present, for example Chrome running on port 9222, they would drive the developer's real, logged-in Chrome.
2. **Some tests make live requests to LinkedIn.** These tests seed `_PROFILE_CACHE` without `reached_end_of_feed`:
   - `test_restricted_profile_activity_feed_status`
   - `test_no_public_activities_when_activity_section_explicitly_empty`
   - `test_cached_profile_nav_status_no_unbound_error`
   - `test_cached_profile_prevents_playwright_launch`

   Since the cache-validity change, that cache entry is ignored, and the scraper launches a real browser against linkedin.com. During this review that produced a real HTTP 999 response. Patch `validate_session_state` in a shared fixture, and add `reached_end_of_feed: True` to the seeded cache entries.
3. **Stale expectations:**
   - `test_validate_profile_url_generic` expects no trailing slash, but canonical URLs now end with `/`.
   - `test_failed_attempt_preserves_historical_metrics_and_records` hardcodes `2026-10-31` as the date of the failed attempt.
4. **`.env.example` is missing locally.** It was renamed to `.env`, so `test_issue4_env_example_exists_and_clean` fails. Restore it with `git checkout -- .env.example`.

## Other notes

- The local `.env` sets `LINKEDIN_PASSWORD`, but no code reads it. Remove it so a plaintext password is not stored on disk.
- The frontend escapes scraped text consistently (`escapeHTML` / `escapeAttr`), and engagement counts are coerced to integers. No XSS was found in the rendering paths I checked.
