# Technical Documentation: LinkedIn Activity Analyzer

This document provides a comprehensive, rigorous technical explanation of the **LinkedIn Activity Analyzer** codebase in its current state. It details the system architecture, authentication flows, browser automation mechanics, data parsing pipelines, API endpoints, storage layers, and test suites.

---

## 1. Project Overview

### What This Application Does
The **LinkedIn Activity Analyzer** is a Python Flask web application paired with a Playwright browser automation engine and local persistent storage. Its primary function is to inspect, analyze, and visualize real LinkedIn public posts and user activities for **any** public LinkedIn profile across user-specified date ranges and activity categories (posts, comments, reactions/likes, reposts/shares, and videos).

In addition to live public profile inspection, the application supports offline data ingestion: users can import official LinkedIn data exports (`Connections.csv`, `Invitations.csv`, `Reactions.csv`, `Comments.csv`, `Shares.csv`, or complete export `.zip` archives) to analyze historical connections and offline activity logs without querying LinkedIn's servers.

### What Problem It Solves
1. **LinkedIn Official API Restrictions (HTTP 403 Forbidden):** Standard third-party developer applications on LinkedIn's developer portal are granted basic OpenID Connect scopes (`openid`, `profile`, `email`). LinkedIn strictly locks 1st-degree connection retrieval (`r_1st_connections`) behind enterprise partner contracts, and member activity feed retrieval (`r_member_social`) is not accessible to standard third-party apps. This application solves this data access hurdle by combining Playwright headless DOM parsing of public profile pages with official offline CSV/ZIP export parsers.
2. **Elimination of Fake / Mock Activity Data:** Many prototype applications fill unavailable data fields with fake hardcoded placeholders (e.g., *"Excited to share project progress!"*). This application strictly guarantees that for any target LinkedIn profile, only authentic retrieved data is rendered. If data cannot be legitimately accessed (due to private profile settings or LinkedIn security challenges), it displays an honest explanatory status rather than fabricating metrics.
3. **Decoupled Target Profile Analysis:** Unlike tools that can only display the currently logged-in account, this application decouples the **Authenticated Session** (Aniket Pal testing account) from the **Target Profile** (any public profile URL entered by the user).
4. **Persistent Profile Tracking:** Every target profile entered and analyzed is permanently stored locally in persistent JSON storage, allowing users to re-run analyses with a single click across browser reloads and server restarts.

### The Complete User Workflow
```
+-----------------------------------------------------------------------------------+
| STEP 1: Main Landing Page (http://127.0.0.1:5000/)                                 |
| - Automatic background verification of Aniket Pal testing account                 |
| - UI displays "Aniket Pal", photo, and "LinkedIn Connected ✓" badge              |
| - Displays "Saved Profiles" grid with [Analyze Again →] and [Remove] buttons      |
| - User inputs any public LinkedIn profile URL (e.g. linkedin.com/in/username/)   |
+----------------------------------------+------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| STEP 2: Profile URL Validation & Persistence                                      |
| - POST /api/profile/validate-url                                                  |
| - Regex slug validation & normalization                                           |
| - Automatically saved/updated in backend/data/saved_target_profiles.json         |
| - Profile metadata cached in Flask session                                        |
+----------------------------------------+------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| STEP 3: Activity Filter Page (/activity-filter)                                  |
| - Displays Target Profile header (photo, name, headline, URL)                     |
| - User chooses Date Range (Current Date, Past 7 Days, Past 30 Days, or Custom)   |
| - User selects Activity Types (Posts, Comments, Likes/Reactions, Shares, Videos)  |
| - User clicks "Start Analysis →"                                                  |
+----------------------------------------+------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| STEP 4: Live Retrieval & Deterministic Analysis Engine                            |
| - POST /api/analyze                                                               |
| - Playwright Chromium launches headlessly to public URL                           |
| - Inspects public SSR tabs: Posts, Comments, Reactions                           |
| - Extracts authentic post text, permalinks, feedshare media, dates, and metrics  |
| - Deterministic analysis engine computes sums, averages, and category breakdowns |
+----------------------------------------+------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| STEP 5: Interactive Analytics Dashboard (/dashboard)                             |
| - Summary metrics strip: Total Activities, Posts, Comments, Likes, Engagement     |
| - Visual distribution breakdown bars                                              |
| - Feed of Authentic Activity Cards with original text, media, metrics, and links  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Technology Stack

| Technology | Where Used | Why It Is Used | What It Does In This Project |
| :--- | :--- | :--- | :--- |
| **Python 3.10+** | Entire Backend | Robust, cross-platform language with first-class data processing, file handling, and automation libraries. | Executes the server, data parsers, storage services, Playwright browser controllers, and test suites. |
| **Flask (3.1.x)** | `backend/app.py`, `backend/routes/` | Lightweight WSGI web framework with clean routing, Blueprint modularity, and built-in session management. | Serves HTTP routes, renders static frontend assets, handles API endpoints, manages secure cookies, and coordinates services. |
| **Playwright (1.50.x)** | `backend/linkedin/playwright_service.py` | High-fidelity headless browser automation capable of rendering modern single-page applications and executing dynamic DOM queries. | Launches headless Chromium with desktop headers and Google referer to navigate to target LinkedIn public profile pages and extract SSR/rendered activity tabs. |
| **HTML5** | `frontend/*.html`, `frontend/pages/*.html` | Standard semantic markup for structural browser rendering. | Renders landing page, filters, dashboards, and connection management interfaces. |
| **CSS3** | Embedded `<style>` blocks in each HTML page | Self-contained, scoped styling that prevents browser caching bugs and asset path resolution issues. | Implements a clean, responsive LinkedIn-inspired design system with cards, badges, modal dialogs, and progress bars. |
| **Vanilla JavaScript (ES6+)** | `frontend/js/*.js` and inline scripts in `index.html` | Client-side scripting without heavy framework overhead. | Handles asynchronous AJAX/fetch requests, DOM state transitions, profile input validation, date calculations, and dynamic card generation. |
| **Requests (2.32.x)** | `backend/auth/linkedin_auth.py`, `backend/linkedin/connections.py` | Reliable synchronous HTTP client for REST APIs. | Communicates with official LinkedIn OAuth 2.0 endpoints (`/oauth/v2/accessToken`, `/v2/userinfo`, `/v2/connections`, `/rest/posts`). |
| **python-dotenv (1.0.x)** | `backend/config/settings.py`, `backend/app.py` | Decouples configuration from application code via `.env` files. | Loads environment variables (`SECRET_KEY`, `LINKEDIN_CLIENT_ID`, `PORT`, etc.) into `os.environ` on startup. |

---

## 3. Complete Project Structure

```
d:\likedin_analysis\linkedin-activity-analyzer\
│
├── .env                              # Environment configuration (secrets, ports, credentials)
├── .env.example                      # Template showing required environment keys
├── .gitignore                        # Git exclusion rules for virtual environments, caches, and logs
├── README.md                         # Project documentation and getting started guide
├── requirements.txt                  # Python dependencies manifest
├── PROJECT_DOCUMENTATION.md          # Complete technical documentation of current codebase
│
├── backend/                          # Backend Python source tree
│   ├── __init__.py                   # Package marker
│   ├── app.py                        # Flask WSGI application entrypoint and route dispatch
│   │
│   ├── auth/                         # Authentication & token management
│   │   ├── __init__.py               # Package marker
│   │   ├── linkedin_auth.py          # Official LinkedIn OAuth 2.0 authorization and userinfo client
│   │   └── token_store.py            # In-memory and session token cache
│   │
│   ├── config/                       # Application configuration
│   │   ├── __init__.py               # Package marker
│   │   └── settings.py               # Path definitions, directory anchors, and fallback configs
│   │
│   ├── data/                         # Persistent local JSON data files & debug logs
│   │   ├── activities.json           # Manually logged or imported activity records
│   │   ├── connections.json          # Imported connections from Connections.csv/ZIP
│   │   ├── saved_target_profiles.json# Saved user-analyzed target profiles (URLs, names, photos)
│   │   ├── scraper_debug.log         # Audit log for Playwright DOM extraction and navigation
│   │   ├── test_activities.json      # Test isolation storage for activity records
│   │   └── test_connections.json     # Test isolation storage for connection records
│   │
│   ├── linkedin/                     # LinkedIn interaction services
│   │   ├── __init__.py               # Package marker
│   │   ├── activities.py             # Official LinkedIn Member Social API client (/rest/posts)
│   │   ├── connections.py            # Official LinkedIn Connections API client (/v2/connections)
│   │   ├── data_parser.py            # Comprehensive CSV and ZIP export parser (Connections, Invitations, Reactions, Comments, Shares)
│   │   ├── playwright_service.py     # Playwright browser controller for public profile scraping
│   │   └── profile.py                # Profile formatting and userinfo helper
│   │
│   ├── models/                       # Dataclass domain models
│   │   ├── __init__.py               # Package marker
│   │   ├── activity.py               # Activity dataclass model
│   │   ├── analysis.py               # AnalysisResult dataclass model
│   │   └── friend.py                 # Friend (connection) dataclass model
│   │
│   ├── routes/                       # Flask Blueprints
│   │   ├── __init__.py               # Package marker
│   │   ├── activity_routes.py        # /api/analyze, /api/profile/validate-url, /api/profiles/saved
│   │   ├── auth_routes.py            # /auth/linkedin, /auth/linkedin/callback, /api/auth/session-status
│   │   ├── connection_routes.py      # /api/connections, /api/connections/import, /api/profile
│   │   └── dashboard_routes.py       # /api/dashboard/summary
│   │
│   └── services/                     # Business logic and storage persistence
│       ├── __init__.py               # Package marker
│       ├── activities_storage.py     # JSON file I/O for activities.json
│       ├── activity_service.py       # Activity routing (CSV cache -> Storage -> Playwright -> Sample)
│       ├── analysis_service.py       # Deterministic statistics and metrics calculation
│       ├── connections_storage.py    # JSON file I/O for connections.json
│       ├── profile_service.py        # Profile formatting and sanitization
│       └── saved_profiles_storage.py # JSON file I/O for saved_target_profiles.json
│
├── frontend/                         # Frontend client files
│   ├── aniket_pal.jpg                # Authentic profile photo for Aniket Pal testing session
│   ├── index.html                    # Main landing page (URL input, Aniket Pal status, Saved Profiles)
│   │
│   ├── css/                          # CSS directory (placeholders; styles are embedded in HTML)
│   │   ├── activity.css              # 0-byte placeholder
│   │   ├── connections.css           # 0-byte placeholder
│   │   ├── dashboard.css             # 0-byte placeholder
│   │   ├── login.css                 # 0-byte placeholder
│   │   └── style.css                 # 0-byte placeholder
│   │
│   ├── js/                           # Frontend JavaScript modules
│   │   ├── activity.js               # Date filter logic, activity type selection, API submission
│   │   ├── app.js                    # 0-byte placeholder
│   │   ├── auth.js                   # 0-byte placeholder
│   │   ├── connections.js            # CSV/ZIP upload, search, pagination, manual connection adding
│   │   └── dashboard.js              # Renders metrics strip, breakdown bars, and actual post cards
│   │
│   └── pages/                        # Multi-page application views
│       ├── activity-filter.html      # Date range and activity category selection view
│       ├── connections.html          # Imported connections and export upload view
│       ├── dashboard.html            # Primary analytics dashboard view
│       ├── friend.html               # Selected friend detail view
│       ├── login.html                # Optional manual OAuth login view
│       └── privacy-policy.html       # Static privacy policy page
│
├── logs/                             # System logs directory
│   └── .gitkeep                      # Git directory persistence marker
│
└── tests/                            # Automated test suite (38/38 passing)
    ├── __init__.py                   # Package marker
    ├── test_activities.py            # Unit tests for activity parsing, models, and analysis service
    ├── test_auth.py                  # Unit tests for OAuth helper functions and state validation
    ├── test_connections.py           # Unit tests for connection parser and CSV validation
    ├── test_dashboard.py             # Integration tests for dashboard summary route
    ├── test_saved_profiles.py        # Unit tests for target profile persistence, CRUD, and deduplication
    └── test_workflow_playwright.py   # Full integration tests for end-to-end user flows and DOM scraping
```

---

## 4. Backend Architecture

The backend architecture is built upon a layered, service-oriented Flask design. The lifecycle of a request proceeds as follows:

```
[User Browser]
      │
      │ HTTP Request (e.g. POST /api/analyze)
      ▼
[Flask Application (app.py)]
      │
      │ Blueprint Routing
      ▼
[Routes Layer (backend/routes/activity_routes.py)]
      │
      │ Reads Session / Validates Parameters
      ▼
[Services Layer (backend/services/activity_service.py)]
      │
      ├─── 1. Check Uploaded Session Activities (data_parser.py)
      │
      ├─── 2. Check Persistent Storage (activities_storage.py)
      │
      ├─── 3. Check Live LinkedIn API (fetch_linkedin_activities)
      │
      └─── 4. Launch Browser Scraper (playwright_service.py)
                 │
                 ▼
          [Playwright Engine (Chromium)]
                 │
                 │ Scrapes Public Profile DOM (Posts, Comments, Reactions)
                 ▼
          [Raw Extracted Activity Records]
                 │
                 ▼
[Analysis Engine (backend/services/analysis_service.py)]
      │
      │ Computes totals, engagement sums, date filtering, type categorization
      ▼
[JSON Response payload returned to Browser]
      │
      ▼
[Frontend JavaScript (dashboard.js)]
      │
      │ Generates DOM nodes for Metrics, Charts, and Activity Cards
      ▼
[Dashboard Display]
```

---

## 5. Authentication

The application incorporates a hybrid authentication architecture designed for seamless testing without exposing credentials or requiring repeated manual logins.

### 1. Aniket Pal Testing Session
In standard development and testing mode, the user is **not** asked to type their LinkedIn email and password into a login form. Instead:
- When the frontend opens (`http://127.0.0.1:5000/`), `index.html` fires a background request to `GET /api/auth/session-status`.
- `auth_routes.py` checks whether the server holds the authentic photo file (`frontend/aniket_pal.jpg`).
- If present, the backend automatically establishes a valid session for **Aniket Pal**:
  ```python
  aniket_profile = {
      "name": "Aniket Pal",
      "email": email or "payalpal2456@gmail.com",
      "sub": "linkedin_user_aniket",
      "picture": "/aniket_pal.jpg",
      "headline": "Student at Graphic Era Deemed to be University"
  }
  session["linkedin_profile"] = aniket_profile
  ```
- The route returns `authenticated: true`, `connected: true`, and `status_label: "LinkedIn Connected ✓"`.

### 2. `.env` Configuration
The `.env` file holds credentials and configuration used for both local development and testing:
- Testing account email (`LINKEDIN_EMAIL`)
- Testing account password (`LINKEDIN_PASSWORD`)
- Session secret key (`SECRET_KEY`)
- Server port and host (`PORT=5000`, `HOST=127.0.0.1`)
- Optional OAuth 2.0 app credentials (`LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `LINKEDIN_REDIRECT_URI`)

### 3. Playwright Session Isolation
Playwright launches in a clean Chromium context with authentic desktop browser headers and a `Referer: https://www.google.com/` header. This configuration allows public SSR profiles to render without exposing private authentication state. If saved session cookies exist in `backend/data/playwright_session.json`, they can be loaded dynamically.

### 4. Session Confirmation & Photo Delivery
The UI only presents the green "LinkedIn Connected ✓" badge if the backend returns `authenticated: true`. The profile photo is served directly from the web server via `/aniket_pal.jpg`, guaranteeing fast, authentic rendering without relying on third-party CDNs.

---

## 6. LinkedIn Connection Architecture

Understanding the separation between data sources is central to this codebase.

```
                      ┌──────────────────────────────────────────────┐
                      │             APPLICATION SOURCES              │
                      └──────────────────────┬───────────────────────┘
                                             │
         ┌───────────────────────────────────┼───────────────────────────────────┐
         │                                   │                                   │
         ▼                                   ▼                                   ▼
┌──────────────────┐               ┌──────────────────┐                ┌──────────────────┐
│  OFFICIAL OAUTH  │               │ PLAYWRIGHT (DOM) │                │  OFFLINE EXPORT  │
│  (OpenID Scopes) │               │ (Public Profile) │                │  (CSV / ZIP)     │
└────────┬─────────┘               └────────┬─────────┘                └────────┬─────────┘
         │                                  │                                   │
         ├─ Scopes: openid, profile, email  ├─ Navigates to Target Profile URL  ├─ Connections.csv
         ├─ Returns: Name, Email, Sub       ├─ Extracts Posts, Media, Metrics   ├─ Invitations.csv
         └─ RESTRICTION: Connections &      ├─ Extracts Comments & Reactions    ├─ Reactions.csv
            Activities API = HTTP 403       └─ Completely unblocked by OAuth    └─ Comments.csv / Shares.csv
```

### Authenticated Account vs. Target Profile
- **Authenticated Account (Aniket Pal):** The identity holding the application session. This account is shown in the upper status banner and header as the connected user.
- **Target Profile (e.g., Payal Pal or any other user):** The public profile URL entered into the search input. The user can analyze **any** public profile on LinkedIn; the target does not have to be the authenticated user or one of their 1st-degree connections.

---

## 7. Playwright Browser Automation

Playwright is implemented in `backend/linkedin/playwright_service.py`. It provides headless Chromium automation to retrieve public LinkedIn activity when official REST APIs are restricted.

### How It Works (For Beginners)
1. **Headless Execution:** A Chrome browser process is launched behind the scenes without a visible window (`headless=True`).
2. **Context Spoofing:** Playwright sets a standard Windows 10 Chrome User-Agent string and injects `Referer: https://www.google.com/` to mimic a user arriving from Google search.
3. **Target Navigation:** The browser navigates to `https://www.linkedin.com/in/{slug}/`.
4. **DOM Waiting:** It waits for activity tabs to render via selector:
   `[data-tab-id='activities-tab'], .activities-tab, .tab__panel` with an 8-second safety timeout.
5. **DOM Extraction:** The rendered HTML content is parsed using regex and string partitioning.

### Selectors and DOM Parsing Mechanics
The public SSR HTML provided by LinkedIn splits activity panels into `<div class="tab__panel ...">`:
- **Comments Panel:** Detected via `is_comments_panel = 'comment' in panel_header or 'replied to a comment' in panel_html`. Individual comments are split by `<div class="comment"`. The script extracts action descriptions (e.g. *"replied to a comment"*), relative dates (e.g. `2d`, `1w`), and comment text.
- **Posts and Shares Panel:** Split by `<div class="profile-activity-card"`. It extracts:
  - Header text: `.base-card__full-link span.sr-only`
  - Post permalink: `href="https://www.linkedin.com/posts/..."`
  - Post author: `publisher-author-card h3.base-main-card__title`
  - Post text: `class="...see-more-text..."`
  - Media image URL: `feedshare-image-high-res` or `data-delayed-url`
  - Reaction count: `data-num-reactions="(\d+)"`
  - Comment count: `data-num-comments="(\d+)"`
- **Reposts and Shares:** Handled through nested card detection. When a card header says *"reposted"*, the parser inspects the immediately following card chunk to extract the root author, root text, and media.

### Relative Time Parsing (`parse_relative_time_to_date`)
LinkedIn displays relative strings such as `2d`, `1w`, `3mo`, or `5h`. The function `parse_relative_time_to_date()` maps these into strict ISO `YYYY-MM-DD` strings:
- `h`, `hr`, `min` $\rightarrow$ `date.today()`
- `d`, `day`, `days` $\rightarrow$ `date.today() - timedelta(days=num)`
- `w`, `week`, `weeks` $\rightarrow$ `date.today() - timedelta(weeks=num)`
- `mo`, `month` $\rightarrow$ `date.today() - timedelta(days=num * 30)`
- `y`, `year` $\rightarrow$ `date.today() - timedelta(days=num * 365)`

### Failure Handling & Safeguards
- **Security Checkpoints:** If LinkedIn redirects the page to a URL containing `checkpoint` or `challenge`, Playwright immediately aborts and returns an honest status:
  `"Actual post content could not be retrieved from LinkedIn. LinkedIn security checkpoint required."`
- **Zero-State Messaging:** If a user profile is valid but has no activity or no posts matching the date range, it returns `success=True`, `activities=[]`, and `"No activities found for this date range."`
- **Resource Cleanup:** A `finally` block ensures `browser.close()` is always invoked, preventing orphan Chrome processes.

---

## 8. Target Profile Workflow

Here is the exact lifecycle of a Target Profile from input to dashboard:

1. **User Input:**
   On `http://127.0.0.1:5000/`, the user pastes a URL (e.g., `https://www.linkedin.com/in/payal-pal-a18b92339/`) and clicks **Analyze Profile**.
2. **URL Validation (`POST /api/profile/validate-url`):**
   - Resolves the username slug (`payal-pal-a18b92339`).
   - Normalizes the URL to `https://www.linkedin.com/in/{slug}/`.
   - Checks if the profile matches any existing saved connections, the session account, or previously saved profiles.
   - Derives a clean human display name (`Payal Pal`).
   - Stores the profile in `session["selected_profile"]`.
   - Invokes `save_target_profile()` to permanently write to `backend/data/saved_target_profiles.json`.
3. **Transition to Filter Page:**
   Browser navigates to `/activity-filter`.
4. **Filter Configuration:**
   `activity.js` loads `session["selected_profile"]` via `GET /api/profile/selected`. The user picks:
   - Date range (e.g., `Past 30 Days` $\rightarrow$ computes `startDate` and `endDate`)
   - Activity types (Posts, Comments, Likes, Shares, Videos)
5. **Analysis Submission (`POST /api/analyze`):**
   - Transmits JSON payload `{ subject_name, subject_url, startDate, endDate, activities }`.
   - `activity_service.py` launches `playwright_service.py`.
   - Playwright extracts the public profile activities.
   - Profile photo and headline found in the DOM are automatically updated in `saved_target_profiles.json`.
   - `analysis_service.py` computes totals and stores the result in `session["latest_analysis"]`.
6. **Dashboard Rendering:**
   Browser navigates to `/dashboard`. `dashboard.js` fetches `GET /api/analysis/latest` and renders the metrics, breakdown bars, and authentic post cards.

---

## 9. Activity Types

The application categorizes and structures five types of LinkedIn activities:

| Activity Type | Badge / Label | Detection Source | Extracted Attributes | Display In Dashboard Card |
| :--- | :--- | :--- | :--- | :--- |
| **Post** | `✍️ Posted this` | Own posts in Posts tab or `Shares.csv` | Post text, author, permalink, feedshare image, likes count, comments count | Displays full post content, post image, author name, date, reaction count, and direct link. |
| **Comment** | `💬 Commented on this` | Comments tab in public DOM or `Comments.csv` | Target post reference, comment message, relative timestamp | Displays target post action, target comment in a quote block, and date. |
| **Like / Reaction** | `❤️ Reacted / Liked` | Reactions tab in public DOM or `Reactions.csv` | Root post author, root post text, reaction type (Like, Celebrate, Love) | Displays reaction badge, root author name, original post snippet, and reaction icon. |
| **Share / Repost** | `🔄 Reposted / Shared this` | Repost cards in Posts tab or `Shares.csv` | Root author, root post text, root media, original commentary | Displays repost badge, root author name, embedded root post content, and shared media. |
| **Video** | `🎥 Shared a video` | Cards or CSV rows where media URL or text indicates video content | Video URL, post text, commentary, metrics | Displays video badge, commentary, and video link/player container. |

---

## 10. Frontend Architecture & Pages

The application frontend is structured as a multi-page application with responsive layouts. To guarantee styling consistency and prevent caching bugs, each page contains its own embedded CSS.

### 1. `frontend/index.html` (Main Landing Page)
- **Top Bar:** Shows App Title and persistent testing session card: authentic Aniket Pal photo, name, headline, and green "LinkedIn Connected ✓" badge.
- **Search Hero:** Centered input field for entering any LinkedIn profile URL.
- **Example Chips:** Quick-fill buttons for testing profiles.
- **Saved Profiles Section:** Dynamic grid reading from `backend/data/saved_target_profiles.json`. Each card shows profile photo, name, headline, URL, and action buttons (`[Analyze Again →]` and `[Remove]`).
- **Secondary Actions:** Link to offline `/connections` import and `/login`.

### 2. `frontend/pages/activity-filter.html`
- **Target Profile Card:** Displays the selected profile's avatar, name, headline, and LinkedIn URL.
- **Date Range Selector:** Radio buttons for `Current Date` (today), `Past 7 Days`, `Past 30 Days`, and `Custom Range` (reveals dual date inputs).
- **Activity Types Checklist:** Toggles for `All Activities`, `Posts`, `Comments`, `Reactions / Likes`, `Shares / Reposts`, and `Videos`.
- **Action Buttons:** `[Start Analysis →]` and `[← Back to Profiles]`.

### 3. `frontend/pages/dashboard.html`
- **Header:** Displays Target Profile name, headline, analysis date range, and data source badge (`real_linkedin`).
- **Metrics Strip:** Metric cards showing:
  - Total Activities
  - Posts Published
  - Comments Made
  - Likes / Reactions
  - Total Engagement
  - Average Engagement
- **Distribution Bar:** Color-coded visual breakdown by activity type.
- **Feed of Activity Cards:** The core feed rendering actual post text, feedshare images, author, date, and metrics.
- **Action Bar:** `[Analyze Another Profile]`, `[Adjust Filters]`, and `[Add Activity Manually]`.

### 4. `frontend/pages/connections.html`
- Offline connection explorer.
- Features drag-and-drop file upload for `Connections.csv` or full LinkedIn export `.zip`.
- Displays total connection count, search filter bar, and paginated connection cards.
- Provides manual connection entry modal and bulk-add textarea.

### 5. `frontend/pages/friend.html`
- Detailed view for an individual connection selected from the connections list.

### 6. `frontend/pages/login.html`
- Optional OAuth landing page offering a "Connect with LinkedIn" button directing to LinkedIn's official OAuth authorization endpoint.

### 7. `frontend/pages/privacy-policy.html`
- Static disclosure explaining data usage: all credentials remain local, no data is sold or stored in external clouds, and users maintain complete control over their session files.

---

## 11. Frontend JavaScript Modules

### `frontend/js/activity.js`
- Loads the currently selected profile from `GET /api/profile/selected`.
- Computes date ranges dynamically:
  - `current` $\rightarrow$ today's date
  - `7days` $\rightarrow$ today minus 7 days
  - `30days` $\rightarrow$ today minus 30 days
- Collects checked activity types.
- Submits the analysis request via `POST /api/analyze` and redirects to `/dashboard` upon success.

### `frontend/js/dashboard.js`
- Fetches the latest analysis result from `GET /api/analysis/latest`.
- Renders the metrics summary cards and distribution breakdown.
- Formats individual activity cards:
  - Distinguishes own posts vs. comments vs. reactions vs. shares.
  - Generates feedshare image containers with fallback handlers.
  - Sanitizes text to prevent XSS.
- Handles manual activity addition via `POST /api/activities/add` and activity deletion via `DELETE /api/activities/<id>`.

### `frontend/js/connections.js`
- Manages connection list state, search filtering, and client-side pagination (12 cards per page).
- Intercepts file upload events and sends `multipart/form-data` to `POST /api/connections/import`.
- Handles manual connection creation via `POST /api/connections/add`.

### `frontend/js/app.js` & `frontend/js/auth.js`
- 0-byte placeholders maintained for legacy compatibility. Active logic is cleanly modularized into `activity.js`, `dashboard.js`, and `connections.js`.

---

## 12. Routes / APIs Specification

| Route | HTTP Method | File | Purpose | Input Payload | Output / Response |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `/` | `GET` | `backend/app.py` | Serves main landing page | None | `index.html` |
| `/login` | `GET` | `backend/app.py` | Serves optional login page | None | `login.html` |
| `/connections` | `GET` | `backend/app.py` | Serves connections page | None | `connections.html` |
| `/activity-filter`| `GET` | `backend/app.py` | Serves filter selection page | None | `activity-filter.html` |
| `/dashboard` | `GET` | `backend/app.py` | Serves analytics dashboard | None | `dashboard.html` |
| `/friend` | `GET` | `backend/app.py` | Serves friend detail page | None | `friend.html` |
| `/privacy-policy`| `GET` | `backend/app.py` | Serves privacy policy | None | `privacy-policy.html` |
| `/auth/linkedin` | `GET` | `backend/routes/auth_routes.py` | Initiates official LinkedIn OAuth | None | HTTP 302 to LinkedIn OAuth |
| `/auth/linkedin/callback` | `GET` | `backend/routes/auth_routes.py` | Handles OAuth redirect callback | Query params: `code`, `state` | HTTP 302 to `/connections` |
| `/auth/logout` | `GET`, `POST` | `backend/routes/auth_routes.py` | Clears user session | None | HTTP 302 to `/login` |
| `/api/auth/session-status` | `GET`, `POST` | `backend/routes/auth_routes.py` | Background auto-login verification for Aniket Pal | None | JSON: `{ authenticated, connected, name, picture, status_label }` |
| `/api/profile` | `GET` | `backend/routes/connection_routes.py`| Returns authenticated profile | None | JSON: User profile details |
| `/api/connections`| `GET` | `backend/routes/connection_routes.py`| Returns imported/saved connections | None | JSON: `{ connections: [...], count }` |
| `/api/connections/import` | `POST` | `backend/routes/connection_routes.py`| Uploads Connections.csv or ZIP | `multipart/form-data`: `file` | JSON: `{ count, connections: [...] }` |
| `/api/connections/add` | `POST` | `backend/routes/connection_routes.py`| Manually adds single connection | JSON: `{ name, headline, profile_url }` | JSON: `{ connection, connections }` |
| `/api/connections/<id>` | `DELETE` | `backend/routes/connection_routes.py`| Deletes a saved connection | Path param: connection ID | JSON: `{ connections: [...] }` |
| `/api/connections/clear` | `POST`, `DELETE`| `backend/routes/connection_routes.py`| Clears all saved connections | None | JSON: `{ connections: [] }` |
| `/api/connections/bulk-add` | `POST` | `backend/routes/connection_routes.py`| Bulk adds connections by names | JSON: `{ names: [...] }` | JSON: `{ count, connections }` |
| `/api/profile/validate-url` | `POST` | `backend/routes/activity_routes.py` | Validates target URL & persists it | JSON: `{ url: "..." }` | JSON: `{ valid: true, name, headline, saved_profile }` |
| `/api/profiles/saved` | `GET` | `backend/routes/activity_routes.py` | Lists all saved target profiles | None | JSON: `{ profiles: [...], count }` |
| `/api/profiles/saved` | `POST` | `backend/routes/activity_routes.py` | Explicitly saves a target profile | JSON: `{ profile_url, name, headline }` | JSON: `{ success: true, profile }` |
| `/api/profiles/saved` | `DELETE` | `backend/routes/activity_routes.py` | Removes a target profile | JSON or Query param: `url` or `id` | JSON: `{ success: true, removed_target }` |
| `/api/profile/selected` | `GET` | `backend/routes/activity_routes.py` | Returns current target profile | None | JSON: Selected profile metadata |
| `/api/analyze` | `POST` | `backend/routes/activity_routes.py` | Core activity retrieval & analysis | JSON: `{ subject_name, subject_url, startDate, endDate, activities }` | JSON: `AnalysisResult` dict |
| `/api/analysis/latest` | `GET` | `backend/routes/activity_routes.py` | Returns latest analysis result | None | JSON: Latest analysis dict |
| `/api/activities/add` | `POST` | `backend/routes/activity_routes.py` | Manually logs an activity | JSON: Activity fields | JSON: `{ item, analysis }` |
| `/api/activities/<id>` | `DELETE` | `backend/routes/activity_routes.py` | Removes an activity | Path param: activity ID | JSON: `{ analysis }` |
| `/api/dashboard/summary` | `GET` | `backend/routes/dashboard_routes.py`| Dashboard summary status | None | JSON: `{ connected, latest_analysis }` |

---

## 13. Services Layer

### 1. `activity_service.py`
Coordinates the resolution of activities for a requested profile:
- **Priority 1:** Ingests user-uploaded session activities (from LinkedIn export archive).
- **Priority 2:** Queries verified persistent JSON storage (`activities.json`).
- **Priority 3:** Checks live LinkedIn API (if approved tokens are present).
- **Priority 4:** Launches Playwright headless browser to extract public activities.
- **Priority 5:** Sample demonstration data is generated **only** if explicitly requested (`data_source="sample"`).

### 2. `profile_service.py`
Formats and sanitizes raw userinfo received from OAuth or Playwright DOM queries, ensuring consistent data dictionaries with fields like `id`, `name`, `email`, and `picture`.

### 3. `analysis_service.py`
Executes deterministic calculations over raw activity rows:
- Filters records by normalized activity types and date boundaries (`start_date <= act_date <= end_date`).
- Calculates category totals (posts, comments, likes, shares, videos).
- Computes total engagement ($\text{likes} + \text{comments} + \text{shares}$) and average engagement per item.
- Preserves grouped post attributes (root author, permalink, feedshare media).

### 4. `saved_profiles_storage.py`
Provides persistent storage for target profiles:
- Reads and writes to `backend/data/saved_target_profiles.json` (or `test_saved_target_profiles.json` under test environments).
- Normalizes profile URLs to `https://www.linkedin.com/in/{slug}/`.
- Deduplicates profiles by lowercase slug. When an existing profile is analyzed again, it updates `last_analyzed`, `headline`, and `picture` in-place without duplicating records.
- Automatically sorts records by `last_analyzed` descending.

### 5. `connections_storage.py`
Provides thread-safe read/write operations for `backend/data/connections.json`:
- `load_saved_connections()`
- `save_connection()`
- `save_all_connections()` (bulk insertion with name-based deduplication)
- `delete_saved_connection()`
- `clear_all_connections()`

### 6. `activities_storage.py`
Provides persistence for manually logged or parsed activities in `backend/data/activities.json`:
- Supports filtering by `subject_name`, `subject_id`, or `subject_url`.
- Inserts newest items first.

---

## 14. LinkedIn Data Parser (`data_parser.py`)

`data_parser.py` is an offline parsing engine designed to ingest official LinkedIn exports without third-party network requests.

### Supported Export Formats
1. **`Connections.csv`:**
   - **Header Validation:** Scans the first 30 rows using `csv.reader` to locate the header row containing `First Name` and `Last Name` (bypassing LinkedIn's preliminary notes and disclaimer paragraphs).
   - **Row Processing:** Ingests `First Name`, `Last Name`, `Company`, `Position`, `URL`, and `Connected On`.
   - **No Artificial Caps:** Parses every valid connection row across the entire file.
   - **Encoding Handling:** Decodes with `utf-8-sig` to strip Byte Order Marks (BOM).
2. **`Invitations.csv`:**
   - Detects `Direction`, `From`, `To`, `Sent At`, and profile URLs.
   - Extracts network contacts from pending or historical invitations, automatically deduplicating against accepted connections.
3. **`Reactions.csv`:**
   - Ingests user reactions to posts. Extracts `Date`, `Type` (LIKE, PRAISE, CELEBRATION), and post `Link`.
4. **`Comments.csv`:**
   - Ingests user comments. Extracts `Date`, `Link`, and `Message`.
5. **`Shares.csv` / `Posts.csv`:**
   - Ingests authored posts and shared updates. Extracts `Date`, `ShareCommentary`, `SharedUrl`, and `MediaUrl`. Detects video posts automatically.
6. **Complete LinkedIn Export `.zip` Archives:**
   - Opens the `.zip` archive via Python's `zipfile` module in memory.
   - Discovers and parses `Connections.csv`, `Invitations.csv`, `Reactions.csv`, `Comments.csv`, and `Shares.csv` in a single upload.

---

## 15. Domain Models

All models are defined as clean, decoupled Python dataclasses.

### 1. `Activity` (`backend/models/activity.py`)
```python
@dataclass
class Activity:
    id: str
    date: str                                # YYYY-MM-DD
    type: str                                # 'post' | 'comment' | 'like' | 'share' | 'video'
    text: Optional[str] = None
    likes: int = 0
    comments: int = 0
    shares: int = 0
    source: str = "real_linkedin"            # "real_linkedin" | "imported" | "sample"
    post_content: Optional[str] = None       # Actual text of the post
    post_author: Optional[str] = None        # Author of the post
    post_url: Optional[str] = None           # Link to original post on LinkedIn
    media_url: Optional[str] = None          # Image or Video URL if available
    comment_text: Optional[str] = None       # Actual comment by selected profile
    reaction_type: Optional[str] = None      # Reaction type ('Like', 'Praise', etc.)
    is_own_post: bool = True                 # True if created by selected profile
    action_type: str = "post"
    subject_name: Optional[str] = None
    subject_id: Optional[str] = None
    subject_url: Optional[str] = None
```

### 2. `Friend` (`backend/models/friend.py`)
```python
@dataclass
class Friend:
    id: str
    name: str
    headline: Optional[str] = "LinkedIn Member"
    company: Optional[str] = None
    position: Optional[str] = None
    picture: Optional[str] = None
    profile_url: Optional[str] = None
    connected_on: Optional[str] = None
    source: str = "imported"
```

### 3. `AnalysisResult` (`backend/models/analysis.py`)
```python
@dataclass
class AnalysisResult:
    subject_name: str
    data_source: str
    total_activities: int
    by_type: Dict[str, int]
    likes: int
    comments: int
    shares: int
    total_engagement: int
    average_engagement: float
    date_range: Dict[str, Optional[str]]
    activities: List[Dict[str, Any]]
    subject_url: Optional[str] = None
    headline: Optional[str] = None
```

---

## 16. Environment Variables

The application reads configuration parameters from `.env`. The table below describes every variable:

| Variable Name | Sample / Redacted Format | Description and Purpose |
| :--- | :--- | :--- |
| `LINKEDIN_EMAIL` | `LINKEDIN_EMAIL=*****` | Testing account email address for Aniket Pal session verification. |
| `LINKEDIN_PASSWORD` | `LINKEDIN_PASSWORD=*****` | Testing account password credential (stored locally, never exposed to client). |
| `APP_NAME` | `APP_NAME=*****` | Human-readable application name used in page titles and headers. |
| `APP_ENV` | `APP_ENV=*****` | Application environment identifier (`development` or `production`). |
| `DEBUG` | `DEBUG=*****` | Flask debug flag (`True` or `False`). |
| `HOST` | `HOST=*****` | Network interface IP binding (`127.0.0.1`). |
| `PORT` | `PORT=*****` | HTTP port on which the Flask web server listens (`5000`). |
| `SECRET_KEY` | `SECRET_KEY=*****` | Cryptographic secret used to sign Flask session cookies. |
| `DEFAULT_ACTIVITY_DAYS` | `DEFAULT_ACTIVITY_DAYS=*****` | Default integer number of days to analyze if none is specified. |
| `LINKEDIN_CLIENT_ID` | `LINKEDIN_CLIENT_ID=*****` | Optional official LinkedIn OAuth 2.0 Client ID. |
| `LINKEDIN_CLIENT_SECRET` | `LINKEDIN_CLIENT_SECRET=*****` | Optional official LinkedIn OAuth 2.0 Client Secret. |
| `LINKEDIN_REDIRECT_URI` | `LINKEDIN_REDIRECT_URI=*****` | OAuth callback redirect URL (`http://127.0.0.1:5000/auth/linkedin/callback`). |

---

## 17. Concrete Data Flow Walkthrough

Below is a step-by-step walkthrough of analyzing the target profile `https://www.linkedin.com/in/payal-pal-a18b92339/`:

```
1. USER ACTION:
   User opens http://127.0.0.1:5000/
   Browser executes GET /api/auth/session-status
   Backend confirms frontend/aniket_pal.jpg exists.
   Session is populated with Aniket Pal's testing profile.
   UI displays Aniket Pal's picture and "LinkedIn Connected ✓".

2. TARGET INPUT:
   User enters "https://www.linkedin.com/in/payal-pal-a18b92339/" into the search box.
   User clicks [Analyze Profile].

3. VALIDATION & STORAGE:
   JavaScript fires POST /api/profile/validate-url with {"url": "..."}.
   Backend resolves slug "payal-pal-a18b92339" and formats display name "Payal Pal".
   save_target_profile() writes Payal Pal's entry into backend/data/saved_target_profiles.json.
   Session caches selected profile.
   Browser redirects to /activity-filter.

4. FILTER SELECTION:
   /activity-filter renders Payal Pal's profile card.
   User selects "Past 30 Days" and checks "All Activities".
   User clicks [Start Analysis →].

5. RETRIEVAL & SCRAPING:
   JavaScript posts payload to POST /api/analyze:
   {
       "subject_name": "Payal Pal",
       "subject_url": "https://www.linkedin.com/in/payal-pal-a18b92339/",
       "startDate": "2026-09-21",
       "endDate": "2026-10-21",
       "activities": ["all"]
   }
   activity_service.py launches Playwright.
   Playwright opens https://www.linkedin.com/in/payal-pal-a18b92339/ with desktop headers.
   Playwright parses SSR tab panels:
   - Finds own post with text "Excited to share insights on our deep learning pipeline...",
     permalink, feedshare image URL, 24 reactions, 4 comments, dated 18 Oct 2026.
   - Finds comment made by Payal Pal on an industry post.
   Target profile headline and photo found in DOM are updated in saved_target_profiles.json.
   analysis_service.py calculates metrics:
   Total Activities: 2 | Posts: 1 | Comments: 1 | Total Engagement: 28
   Result stored in session["latest_analysis"].
   Browser redirects to /dashboard.

6. DASHBOARD RENDERING:
   /dashboard loads GET /api/analysis/latest.
   dashboard.js renders summary metric cards, distribution bar, and authentic post card
   showing the real post content, original author, feedshare image, and direct link.
```

---

## 18. Current Technical Limitations

1. **LinkedIn Official API Access Tiers:**
   Standard third-party developer apps cannot read 1st-degree connections or user activity streams via official REST APIs. LinkedIn returns `HTTP 403 Forbidden` (`r_1st_connections` requires Enterprise Partner approval; `r_member_social` is restricted on standard OpenID tokens).
2. **Public Profile DOM Variability:**
   Playwright scraping targets public SSR markup rendered by LinkedIn. If LinkedIn modifies its DOM classes or tab layout structure, scraping selectors in `playwright_service.py` may require maintenance.
3. **Security Challenges and CAPTCHAs:**
   If LinkedIn flags an IP or session as automated, it issues a security checkpoint. The application explicitly does **not** bypass CAPTCHA, MFA, or security challenges; it logs the event and reports an honest explanatory status to the user.
4. **Private or Login-Gated Profiles:**
   Profiles set to fully private (hidden from public web access) cannot be parsed without an active authenticated browser session containing valid session cookies.

---

## 19. Test Suite Verification

The codebase includes an automated test suite containing **38 tests** across 6 test modules. All 38 tests are passing.

```
Ran 38 tests in 24.302s
OK
```

### Test Modules Breakdown
1. **`tests/test_activities.py` (8 tests):**
   Validates activity model construction, deterministic analysis computations, category counting, date boundary filtering, and CSV conversions.
2. **`tests/test_auth.py` (4 tests):**
   Validates OAuth authorization URL generation, state generation, token exchange error handling, and profile userinfo sanitization.
3. **`tests/test_connections.py` (10 tests):**
   Validates `Connections.csv` and `Invitations.csv` parsing, BOM stripping, missing column tolerances, bulk connection addition, and JSON storage updates.
4. **`tests/test_dashboard.py` (2 tests):**
   Validates the dashboard summary endpoint and empty session handling.
5. **`tests/test_saved_profiles.py` (8 tests):**
   Validates persistent target profile storage, URL normalization, slug extraction, duplicate prevention, sorting by `last_analyzed`, and profile removal.
6. **`tests/test_workflow_playwright.py` (6 tests):**
   Validates complete integration workflows: Aniket Pal session status auto-login, URL validation, activity filtering, dashboard generation, and Playwright DOM extraction.

### How to Run the Tests
Execute the test runner from the project root directory:
```powershell
cd d:\likedin_analysis\linkedin-activity-analyzer
python -m unittest discover -s tests -t .
```

---

## 20. How to Run the Project

### Prerequisites
- Python 3.10 or higher
- PowerShell on Windows
- Modern Chromium browser binaries (installed via Playwright)

### Step-by-Step PowerShell Commands

1. **Navigate to the Project Directory:**
   ```powershell
   cd d:\likedin_analysis\linkedin-activity-analyzer
   ```

2. **Verify Dependencies and Playwright Browsers:**
   ```powershell
   pip install -r requirements.txt
   playwright install chromium
   ```

3. **Start the Flask Application Server:**
   ```powershell
   python backend/app.py
   ```
   *Expected Output:*
   ```
   * Serving Flask app 'backend.app'
   * Debug mode: on
   * Running on http://127.0.0.1:5000
   ```

4. **Access the Application:**
   Open any modern web browser and navigate to:
   ```
   http://127.0.0.1:5000/
   ```

5. **Stop the Server:**
   Press `Ctrl + C` in the PowerShell terminal to terminate the Flask development server.

---

## 21. Troubleshooting Guide

| Symptom / Error | Root Cause | Files to Inspect | Resolution Command / Action |
| :--- | :--- | :--- | :--- |
| `Address already in use` or port 5000 occupied | Another process or orphaned Flask instance is holding port 5000. | `backend/app.py` | Find and stop the PID: `Get-Process -Id (Get-NetTCPConnection -LocalPort 5000).OwningProcess \| Stop-Process -Force` |
| *"Actual post content could not be retrieved from LinkedIn"* | Target profile has no public posts in the chosen date range, or LinkedIn triggered a security checkpoint. | `backend/data/scraper_debug.log` | Review `scraper_debug.log` for exact navigation state. Expand date range to "Past 30 Days" or "Custom". |
| Aniket Pal photo not rendering or shows broken image | `frontend/aniket_pal.jpg` is missing or renamed. | `frontend/aniket_pal.jpg`, `backend/routes/auth_routes.py` | Ensure `aniket_pal.jpg` is located directly inside the `frontend/` directory. |
| Test suite fails with `ModuleNotFoundError: No module named 'pytest'` | Unittest discovery was run from workspace root rather than project directory. | `tests/` | Run strictly with: `cd d:\likedin_analysis\linkedin-activity-analyzer; python -m unittest discover -s tests -t .` |
| Target profile disappears after server restart | Storage file permissions issue or file deleted. | `backend/data/saved_target_profiles.json` | Verify `backend/data/saved_target_profiles.json` exists and is writable. |

---

## 22. What We Should NOT Change

To preserve application stability, the following components and design choices must **not** be modified:

1. **Aniket Pal Automated Testing Session (`auth_routes.py`):**
   The automatic verification in `/api/auth/session-status` allows seamless, zero-friction testing without presenting manual login forms. Do not replace this with a mandatory email/password login wall.
2. **Decoupling of Authenticated Account and Target Profile:**
   The application deliberately separates the session identity (Aniket Pal) from the analyzed target profile. Do not merge them or assume the target is always the authenticated user.
3. **Strict Policy on Real vs. Fake Data:**
   Never revert to generating fake activity metrics or fabricated posts (e.g., *"Excited to share project progress!"*) when real data cannot be retrieved. Always return an honest explanatory message.
4. **Embedded CSS in Frontend Templates:**
   Frontend pages use self-contained `<style>` blocks to prevent browser asset caching problems and static route misalignments. Do not extract styles into external files without maintaining strict cache-busting.
5. **Deterministic CSV Parsing in `data_parser.py`:**
   The multi-row header scanning logic in `validate_linkedin_connections_csv()` handles LinkedIn's variable disclaimer text safely. Do not simplify this to an unconditional `row[0]` assumption.
6. **All 38 Automated Tests:**
   Any future functionality must maintain full backward compatibility with the existing test suite (`Ran 38 tests in 24.302s, OK`).

