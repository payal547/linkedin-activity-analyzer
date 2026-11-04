# LinkedIn Activity Analyzer – Project File Guide

## 1. Directory Tree & Architecture Mapping

```text
linkedin-activity-analyzer/
├── backend/
│   ├── app.py                     # Flask application entry point & lifecycle hooks
│   ├── data/                      # JSON data store files (dual-write / fallback)
│   ├── db/
│   │   └── mongo.py               # MongoDB client, connections, and index initialization
│   ├── linkedin/
│   │   ├── auth.py                # LinkedIn OAuth 2.0 URL generators & token exchange
│   │   ├── data_parser.py         # CSV & JSON data archive parsers (Shares.csv, etc.)
│   │   └── playwright_service.py  # Playwright browser automation, DOM scraper & video extractor
│   ├── models/
│   │   └── activity.py            # Typed dataclasses (Activity, etc.)
│   ├── routes/
│   │   ├── activity_routes.py     # Endpoints for profile validation, analysis, and pipeline
│   │   ├── auth_routes.py         # OAuth callbacks and session management
│   │   ├── connection_routes.py   # Connection management and CSV import endpoints
│   │   └── dashboard_routes.py    # Dashboard data hydration endpoints
│   ├── scripts/
│   │   └── migrate_json_to_mongodb.py # Safe JSON-to-MongoDB migration utility
│   ├── services/
│   │   ├── activities_storage.py  # Storage adapter for activities collection
│   │   ├── activity_service.py    # Orchestration service for activity retrieval
│   │   ├── analysis_service.py    # Analysis engine, metrics calculation, and aggregation
│   │   ├── analysis_storage.py    # Storage adapter for latest analysis & history
│   │   ├── connections_storage.py # Storage adapter for connections collection
│   │   ├── imported_activities_storage.py # Storage adapter for imported activities
│   │   ├── pipeline_service.py    # 10-stage execution tracker & timing metrics
│   │   ├── saved_profiles_storage.py # Storage adapter for target profiles
│   │   └── sentiment_service.py   # NLP sentiment scoring & tone analysis
│   └── utils/
│       ├── __init__.py            # Package initializer
│       └── logger.py              # Centralized rotating logger, RunID filter & credential scrubber
├── frontend/
│   ├── index.html                 # Landing page & profile URL entry form
│   ├── js/
│   │   ├── activity.js            # Filter criteria, date range presets, & analysis trigger
│   │   ├── connections.js         # Connections list, search, pagination, & CSV upload
│   │   └── dashboard.js           # Dashboard metrics, video player, pipeline monitor, log viewer
│   └── pages/
│       ├── activity-filter.html   # Step 3: Date & criteria selection page
│       ├── connections.html       # Step 2: Friends & connections selection page
│       ├── dashboard.html         # Step 4: Observability, metrics, and activities timeline
│       └── login.html             # LinkedIn OAuth login gateway
├── logs/
│   └── activity_analyzer.log      # Persistent rotating application log file
├── tests/
│   ├── test_video_and_pipeline.py # Unit tests for video extraction, logger, and pipeline
│   ├── test_workflow_playwright.py# End-to-end Playwright workflow verification
│   └── ...                        # Component-specific test suites
└── docs/
    └── debugging-and-pipeline/    # Comprehensive technical and debugging guides
```

---

## 2. File-by-File Technical Reference

### Backend Core & Infrastructure

#### `backend/app.py`
- **Purpose**: Initializes the Flask WSGI application, registers API blueprints, binds request timing hooks, and performs MongoDB startup health verification.
- **Key Functions/Classes**: `app`, `log_request_start()`, `log_request_end()`.
- **Dependencies**: Imports blueprints from `routes/`, logging from `utils/logger.py`, and DB health checks from `db/mongo.py`.
- **When to Modify**: Adding global middleware, new blueprint registrations, or application-level configuration flags.

#### `backend/db/mongo.py`
- **Purpose**: Provides thread-safe PyMongo connection pooling, client singletons, index creation, and live connectivity health checks.
- **Key Functions/Classes**: `get_db()`, `get_client()`, `check_connection()`, `init_indexes()`.
- **Dependencies**: PyMongo library, environment variables (`MONGODB_URI`, `MONGODB_DATABASE`).
- **When to Modify**: Adding new MongoDB collections, tuning connection timeouts, or adding compound database indexes.

#### `backend/utils/logger.py`
- **Purpose**: Manages application-wide logging with 5MB rotating files, Run ID thread-local context injection, and strict credential redaction.
- **Key Functions/Classes**: `setup_logging()`, `get_logger()`, `RunIdContextFilter`, `redact_sensitive_info()`, `get_recent_logs()`, `get_known_runs()`.
- **Dependencies**: Standard library `logging.handlers.RotatingFileHandler`, `threading.local`.
- **When to Modify**: Adjusting file rotation size, adding new credential redaction patterns, or extending log query filters.

---

### LinkedIn Scraper & Data Parsers

#### `backend/linkedin/playwright_service.py`
- **Purpose**: Automates headless browser navigation on LinkedIn to scroll feeds, locate activity cards, and extract text, timestamps, and video streams.
- **Key Functions/Classes**: `scrape_linkedin_profile_activities()`, `_extract_video_info()`, `_parse_post_card()`.
- **Dependencies**: Playwright Sync API, `utils/logger.py`, `models/activity.py`.
- **When to Modify**: Updating selectors when LinkedIn changes DOM classes, enhancing video extraction heuristics, or tuning scrolling bounds.

#### `backend/linkedin/data_parser.py`
- **Purpose**: Parses official LinkedIn exported data archives (e.g. `Shares.csv`, `Connections.csv`) into structured internal models.
- **Key Functions/Classes**: `parse_shares_csv()`, `parse_connections_csv()`.
- **Dependencies**: Python standard library `csv`, `re`.
- **When to Modify**: Supporting new LinkedIn export archive schemas or additional CSV columns.

#### `backend/linkedin/auth.py`
- **Purpose**: Generates LinkedIn OAuth 2.0 authorization URLs and handles secure code-for-token exchanges.
- **Key Functions/Classes**: `get_linkedin_authorization_url()`, `exchange_code_for_token()`, `get_authenticated_user_profile()`.
- **Dependencies**: `urllib`, `requests`, environment variables (`LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`).
- **When to Modify**: Modifying OAuth scopes, redirect URIs, or token refreshing mechanisms.

---

### Domain Models & Storage Adapters

#### `backend/models/activity.py`
- **Purpose**: Defines typed dataclasses representing activity records, post metadata, engagement counters, and video stream attributes.
- **Key Functions/Classes**: `Activity`.
- **Dependencies**: `dataclasses.dataclass`.
- **When to Modify**: Adding new attributes to activity cards (e.g. polls, reactions, video fields).

#### `backend/services/analysis_storage.py`
- **Purpose**: Dual-store persistence engine for latest analysis results and profile history in MongoDB Local with JSON fallback.
- **Key Functions/Classes**: `save_latest_analysis()`, `load_latest_analysis()`, `save_profile_analysis_history()`, `get_preserved_profile_history()`.
- **Dependencies**: `db/mongo.py`, JSON files in `backend/data/`.
- **When to Modify**: Changing persistence schemas, history retention policies, or fallback behavior.

#### `backend/services/pipeline_service.py`
- **Purpose**: Real-time state manager tracking the 10 execution stages, durations, metrics, and error diagnostics for every request.
- **Key Functions/Classes**: `start_pipeline_run()`, `start_stage()`, `complete_stage()`, `fail_stage()`, `skip_stage()`, `complete_pipeline_run()`, `get_latest_pipeline_run()`.
- **Dependencies**: `utils/logger.py`, `threading.Lock`.
- **When to Modify**: Adding new execution stages, adjusting max kept runs, or adding new stage metrics.

---

### Frontend Presentation & UI Controllers

#### `frontend/js/dashboard.js`
- **Purpose**: Master controller for the Dashboard; renders metrics, timeline cards with video players, and handles profile identity verification.
- **Key Functions/Classes**: `initDashboard()`, `createActivityPostCard()`, `formatDateDDMMYY()`.
- **Dependencies**: Consumes `/api/analysis/latest`.
- **When to Modify**: Enhancing dashboard UI components, video player controls, or metric displays.

#### `frontend/pages/dashboard.html`
- **Purpose**: HTML page structure and styling for the primary analysis dashboard (profile summary, metrics, sentiment, activity feed, and video preview).
- **Dependencies**: `frontend/js/dashboard.js`.
- **When to Modify**: Adjusting dashboard layouts, card styles, or responsive CSS grids.

#### `frontend/js/activity.js`
- **Purpose**: Controller for Step 3: date range selection, activity checkbox filters, strict profile URL validation, and initiating `/api/analyze` requests.
- **Key Functions/Classes**: `applyDatePreset()`, `analyzeActivity()`, `formatDateDDMMYY()`.
- **Dependencies**: Consumes `/api/analyze`.
- **When to Modify**: Adding new date range presets or filter criteria.

#### `frontend/js/connections.js`
- **Purpose**: Controller for Step 2: displaying imported LinkedIn connections, search filtering, pagination, file uploads, and profile selection with parameter persistence.
- **Key Functions/Classes**: `handleConnectionsImport()`, `searchFriends()`, `renderPage()`, `selectConnection()`.
- **Dependencies**: Consumes `/api/connections`, `/api/connections/import`.
- **When to Modify**: Updating connection card layouts, search indexing, or pagination size.

---

### Automated Test Suites

#### `tests/test_profile_isolation.py`
- **Purpose**: Strict automated test suite verifying cross-profile data isolation, rejection of root URLs (`https://www.linkedin.com/in/`), canonical slug extraction, and historical analysis separation.
- **Key Tests**: `test_reject_empty_slug_root_url_in_analyze`, `test_extract_slug_rejects_root_and_corrupt_urls`, `test_activities_storage_strict_slug_query_prevents_bleed`, `test_historical_analysis_isolation_between_profiles`.

#### `tests/test_workflow_playwright.py`
- **Purpose**: End-to-end integration and workflow verification using Playwright automation.

#### `tests/test_video_and_pipeline.py`
- **Purpose**: Automated tests for honest video status taxonomy, stream/thumbnail extraction, and internal pipeline telemetry state transitions.


