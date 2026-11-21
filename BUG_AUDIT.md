# Bug Audit — LinkedIn Activity Analyzer

**Review date:** 2026-11-19
**Method:** Static review of the analysis, saved-profile, date-filter, and dashboard flows. Tests were not run.

## Findings

### 1. High — Batch history fallback ignores each profile's selected date range

When a batch profile has no activities in the live retrieval result, the saved-history fallback attaches the profile's full preserved history. `_filter_historical_if_needed()` filters by activity type only and does not pass the profile's requested start or end date to `analyze_activities()`. The dashboard then selects that history for profiles with no live activities and renders its full date range and activities.

**Reproduction:** Save a profile with activities spanning multiple years, select it in the batch section, choose a narrow date range with no live results, and analyze. The dashboard can show preserved activities outside the selected range.

**References:** `backend/services/analysis_storage.py:343-364, 370-382`; `backend/routes/activity_routes.py:408-412, 543`; `frontend/js/dashboard.js:1171-1182`.

**Status:** Addressed in the current working tree by filtering preserved history with each profile's requested date range before saving the batch result.

### 2. High — Batch analysis can inherit the previous analysis's date filter on the dashboard

Batch completion stores the new result and a batch profile identity in `sessionStorage`, but does not replace or clear `localStorage.activityFilter`. The dashboard reads that key and uses its dates to validate the new cached result and query `/api/analysis/latest`. If those dates came from an earlier single-profile analysis and differ from the batch ranges, the new result is rejected. For a batch identity there is no profile slug for the history fallback, so the dashboard can show its empty state after the batch POST succeeded.

**Reproduction:** Run a single-profile analysis with one date range, then run a batch analysis with a different range. The batch succeeds, but the dashboard can apply the earlier single-profile dates and fail to load the batch result.

**References:** `frontend/index.html:1680-1695, 1880-1897`; `frontend/js/dashboard.js:177-183, 221-232, 260-273`; `backend/routes/activity_routes.py:831-930`.

**Status:** Addressed in the current working tree by recording batch mode in the active filter and skipping stale single-profile dates on the dashboard.

### 3. Medium — Saved-profile DD/MM/YYYY parser accepts invalid months and silently shifts the date

`_parseDMY()` verifies that the constructed date keeps the requested day, but it does not verify the month or year. JavaScript normalizes out-of-range months into another year, so an invalid month can be accepted as a different valid date.

**Reproduction:** Enter `01/13/2026` into a saved-profile date field. It is interpreted as `01/01/2027` instead of being rejected as an invalid month.

**References:** `frontend/index.html:2012-2020`; the parsed value is converted and submitted in `frontend/index.html:1698-1718`.

**Status:** Addressed in the current working tree by validating the day, month, and year after parsing.

### 4. Medium — Batch API compares slash-formatted dates as strings

The batch endpoint checks `p_start > p_end` before normalizing dates. That is a lexicographic string comparison, so valid DD/MM/YYYY ranges can be rejected. The single-profile path normalizes date strings before comparing them, but the batch path does not.

**Reproduction:** Submit a batch profile with `startDate: "31/10/2026"` and `endDate: "01/11/2026"`. The batch route compares the strings and returns “Start Date must be on or before End Date,” even though the range is valid.

**References:** `backend/routes/activity_routes.py:408-412` (batch comparison); `backend/routes/activity_routes.py:622-665` (single-profile normalization).

**Status:** Addressed in the current working tree by normalizing both supported input formats to ISO dates before comparing.

### 5. Medium — Dashboard treats the observed activity dates as the requested date range

The analysis result's `date_range` is the minimum and maximum dates among matching activities. The dashboard and `/api/analysis/latest` compare those observed dates for exact equality with the requested start and end bounds. A successful analysis is therefore rejected whenever no activity falls exactly on either boundary; the page then falls back to preserved history or reports no matching analysis.

**Reproduction:** Request `2026-10-01` through `2026-10-31` for a profile whose matching activity dates run from `2026-10-04` through `2026-10-29`. The result covers the requested range, but its observed bounds differ, so the cache checks mark it as a different filter.

**References:** `backend/services/analysis_service.py:309-312`; `backend/routes/activity_routes.py:871-880`; `frontend/js/dashboard.js:221-232`.

**Status:** Addressed in the current working tree by comparing exact requested bounds. Legacy records without those bounds no longer satisfy explicit date-filter requests; the route filters saved history for the requested range instead.

## Review notes

- All five findings are addressed in the current working tree.
- Findings 1 and 2 affect batch-analysis results and are the highest impact because they can show records outside the chosen period or hide a successful batch result.
- Finding 3 affects the saved-profile date inputs on the landing page. The activity-filter page uses a stricter parser.
- Findings describe code paths identified by inspection; they were not confirmed by running automated tests.
- The current working tree also adds server-side activity pagination (20 per page by default) and avoids copying full activity histories when preparing page responses.
