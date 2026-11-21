/**

 * Dashboard Page Controller

 * Renders real calculated LinkedIn Activity analysis data and actual post cards.

 * Displays each activity grouped with its actual original post content.

 * Strictly adheres to real data: no fake activities, fake posts, fake comments, or fake likes.

 */



/**

 * Standard Date Formatter for LinkedIn Activity Analyzer.

 * Enforces DD/MM/YY format (e.g. 03/11/26) across all frontend displays.

 * Does NOT modify underlying ISO or BSON dates in backend or MongoDB.

 */

function formatDateDDMMYY(dateInput) {
    if (!dateInput) return "";
    let d = null;
    if (dateInput instanceof Date) {
        d = isNaN(dateInput.getTime()) ? null : dateInput;
    } else if (typeof dateInput === "number") {
        const temp = new Date(dateInput);
        d = isNaN(temp.getTime()) ? null : temp;
    } else {
        const s = String(dateInput).trim();
        if (!s) return "";
        const mDmy4 = s.match(/^(\d{1,2})[\/\.-](\d{1,2})[\/\.-](\d{4})$/);
        if (mDmy4) {
            return `${mDmy4[1].padStart(2, "0")}/${mDmy4[2].padStart(2, "0")}/${mDmy4[3]}`;
        }
        const mDmy2 = s.match(/^(\d{1,2})[\/\.-](\d{1,2})[\/\.-](\d{2})$/);
        if (mDmy2) {
            return `${mDmy2[1].padStart(2, "0")}/${mDmy2[2].padStart(2, "0")}/20${mDmy2[3]}`;
        }
        const mIso = s.match(/^(\d{4})[\/\.-](\d{1,2})[\/\.-](\d{1,2})/);
        if (mIso) {
            return `${mIso[3].padStart(2, "0")}/${mIso[2].padStart(2, "0")}/${mIso[1]}`;
        }
        const parsed = new Date(s);
        if (!isNaN(parsed.getTime())) {
            d = parsed;
        } else {
            return s;
        }
    }
    if (d) {
        const dd = String(d.getDate()).padStart(2, "0");
        const mm = String(d.getMonth() + 1).padStart(2, "0");
        const yyyy = String(d.getFullYear());
        return `${dd}/${mm}/${yyyy}`;
    }
    return String(dateInput);
}

function formatDateRangeDDMMYY(start, end) {

    const sFormatted = formatDateDDMMYY(start);

    const eFormatted = formatDateDDMMYY(end);

    if (sFormatted && eFormatted) {

        return sFormatted === eFormatted ? sFormatted : `${sFormatted} → ${eFormatted}`;

    }

    if (sFormatted) return `Since ${sFormatted}`;

    if (eFormatted) return `Until ${eFormatted}`;

    return "All Dates";

}

const FAILED_DASHBOARD_MEDIA_KEY = "failedDashboardMediaPaths";

function dashboardMediaKey(url) {
    try {
        const parsed = new URL(url, window.location.href);
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "";
        return `${parsed.origin}${parsed.pathname}`;
    } catch (error) {
        return "";
    }
}

function isDashboardMediaKnownBroken(url) {
    const key = dashboardMediaKey(url);
    if (!key) return false;
    try {
        return JSON.parse(localStorage.getItem(FAILED_DASHBOARD_MEDIA_KEY) || "[]").includes(key);
    } catch (error) {
        return false;
    }
}

function markDashboardImageFailed(img, fallbackSrc = "") {
    if (!img) return;
    const key = dashboardMediaKey(img.currentSrc || img.src);
    if (key) {
        try {
            const previous = JSON.parse(localStorage.getItem(FAILED_DASHBOARD_MEDIA_KEY) || "[]");
            const known = Array.isArray(previous) ? previous : [];
            if (!known.includes(key)) known.push(key);
            localStorage.setItem(FAILED_DASHBOARD_MEDIA_KEY, JSON.stringify(known.slice(-100)));
        } catch (error) {}
    }

    if (fallbackSrc && img.dataset.fallbackApplied !== "true") {
        img.dataset.fallbackApplied = "true";
        img.src = fallbackSrc;
        return;
    }

    const mediaBox = img.closest(".post-media-box, .feed-post-media, .feed-post-video-poster");
    if (mediaBox) mediaBox.style.display = "none";
}



document.addEventListener("DOMContentLoaded", () => {

    initDashboard();

});



async function initDashboard() {

    const dashboardQuery = new URLSearchParams(window.location.search);

    // 0. Synchronously populate preliminary profile header while fetching analysis data

    try {

        const storedProfile = JSON.parse(sessionStorage.getItem("selectedProfile") || "{}");

        const preliminaryName = dashboardQuery.get("subject_name") || storedProfile.name || localStorage.getItem("selectedFriend");

        if (preliminaryName) {

            const nameEl = document.getElementById("subjectName");

            if (nameEl) nameEl.textContent = preliminaryName;

        }

        const preliminaryHeadline = storedProfile.headline || localStorage.getItem("selectedHeadline");

        if (preliminaryHeadline) {

            const headlineEl = document.getElementById("subjectHeadline");

            if (headlineEl) headlineEl.textContent = preliminaryHeadline;

        }

        const preliminaryUrl = dashboardQuery.get("profile_url") || storedProfile.profile_url || localStorage.getItem("selectedProfileUrl");

        if (preliminaryUrl) {

            const urlLinkEl = document.getElementById("subjectUrlLink");

            if (urlLinkEl) {

                urlLinkEl.textContent = preliminaryUrl;

                urlLinkEl.href = preliminaryUrl;

                urlLinkEl.style.display = "inline";

            }

        }

    } catch (e) {

        // Non-blocking preview error

    }



    let analysisData = null;



    // Retrieve active target profile identity to enforce profile isolation

    let targetSubjectId = "";

    let targetProfileUrl = "";

    let targetSubjectName = "";



    try {

        const storedProfile = JSON.parse(sessionStorage.getItem("selectedProfile") || "{}");

        targetSubjectId = (dashboardQuery.get("subject_id") || storedProfile.subject_id || storedProfile.id || localStorage.getItem("selectedSubjectId") || "").trim();

        targetProfileUrl = (dashboardQuery.get("profile_url") || storedProfile.profile_url || localStorage.getItem("selectedProfileUrl") || "").trim();

        targetSubjectName = (dashboardQuery.get("subject_name") || storedProfile.name || localStorage.getItem("selectedFriend") || "").trim();

    } catch (e) {}

    if (targetProfileUrl || targetSubjectId) loadProfileAnalysisHistory(targetProfileUrl || targetSubjectId);



    // Retrieve active filter criteria to ensure cached date range matches user selection
    let activeFilter = null;
    try {
        const filterStr = localStorage.getItem("activityFilter");
        if (filterStr) activeFilter = JSON.parse(filterStr);
    } catch (e) {}

    // 1. Immediately check sessionStorage for analysis matching this exact profile & date filter

    const stored = sessionStorage.getItem("currentAnalysis");

    if (stored) {

        try {

            const parsedStored = JSON.parse(stored);

            if (parsedStored) {

                const dUrl = (parsedStored.subject_url || "").toLowerCase();

                const dId = (parsedStored.subject_id || "").toLowerCase();

                const dName = (parsedStored.subject_name || "").toLowerCase();



                const tUrl = targetProfileUrl.toLowerCase();

                const tId = targetSubjectId.toLowerCase();

                const tName = targetSubjectName.toLowerCase();



                let isMatch = false;

                if (tUrl && dUrl && (dUrl.includes(tUrl) || tUrl.includes(dUrl))) isMatch = true;

                if (tId && dId && tId === dId) isMatch = true;

                if (tName && dName && tName === dName) isMatch = true;

                if (!tUrl && !tId && !tName) isMatch = true;



                // Prevent old date filters from overriding new user selections
                if (isMatch && activeFilter) {
                    const reqStart = activeFilter.batch ? null : (activeFilter.startDate || null);
                    const reqEnd = activeFilter.batch ? null : (activeFilter.endDate || null);
                    if (parsedStored.requested_date_range && typeof parsedStored.requested_date_range === "object") {
                        const requestedRange = parsedStored.requested_date_range;
                        if ((requestedRange.start || null) !== reqStart || (requestedRange.end || null) !== reqEnd) {
                            isMatch = false;
                        }
                    } else if (reqStart || reqEnd) {
                        // Legacy cached analyses do not record request bounds, so they cannot
                        // safely satisfy an explicit date-filter request.
                        isMatch = false;
                    }
                }

                if (isMatch) {

                    analysisData = parsedStored;

                    renderAnalysisResults(analysisData);

                }

            }

        } catch (e) {

            console.error("Failed to parse stored analysis", e);

        }

    }



    // 2. Query backend for authoritative latest analysis matching profile & date filter

    try {

        let apiUrl = "/api/analysis/latest";

        const qp = [];

        if (targetSubjectId) qp.push(`subject_id=${encodeURIComponent(targetSubjectId)}`);

        if (targetProfileUrl) qp.push(`profile_url=${encodeURIComponent(targetProfileUrl)}`);

        if (targetSubjectName) qp.push(`subject_name=${encodeURIComponent(targetSubjectName)}`);

        if (activeFilter && !activeFilter.batch) {
            if (activeFilter.startDate) qp.push(`startDate=${encodeURIComponent(activeFilter.startDate)}`);
            if (activeFilter.endDate) qp.push(`endDate=${encodeURIComponent(activeFilter.endDate)}`);
        }

        qp.push("page=1", "per_page=20");

        if (qp.length > 0) apiUrl += `?${qp.join("&")}`;



        const res = await fetch(apiUrl);

        if (res.ok) {

            const serverData = await res.json();

            if (serverData && serverData.has_data !== false) {

                const dUrl = (serverData.subject_url || "").toLowerCase();

                const dId = (serverData.subject_id || "").toLowerCase();

                const dName = (serverData.subject_name || "").toLowerCase();



                const tUrl = targetProfileUrl.toLowerCase();

                const tId = targetSubjectId.toLowerCase();

                const tName = targetSubjectName.toLowerCase();



                let isMatch = false;

                if (tUrl && dUrl && (dUrl.includes(tUrl) || tUrl.includes(dUrl))) isMatch = true;

                if (tId && dId && tId === dId) isMatch = true;

                if (tName && dName && tName === dName) isMatch = true;

                if (!tUrl && !tId && !tName) isMatch = true;



                if (isMatch) {

                    analysisData = serverData;

                    renderAnalysisResults(analysisData);

                    return;

                }

            }

        }

    } catch (e) {

        console.warn("Could not fetch latest analysis from server:", e);

        // Show a meaningful user-facing notice when backend is unreachable
        if (!analysisData) {
            const _isNetErr = (e instanceof TypeError && e.message &&
                (e.message.toLowerCase().includes("fetch") ||
                 e.message.toLowerCase().includes("network") ||
                 e.message.toLowerCase().includes("failed")));
            const _noticeId = "dashboard-fetch-error-notice";
            if (!document.getElementById(_noticeId)) {
                const _nd = document.createElement("div");
                _nd.id = _noticeId;
                _nd.style.cssText = "background:#fef3c7;color:#92400e;padding:10px 18px;font-size:13px;border-bottom:1px solid #fde68a;text-align:center;";
                _nd.textContent = _isNetErr
                    ? "⚠️ Could not reach the backend server. Please ensure the Flask server is running on port 5000."
                    : ("⚠️ Failed to load analysis from server: " + (e.message || "Unknown error"));
                const _hdr = document.querySelector("header") || document.body.firstElementChild;
                if (_hdr && _hdr.parentNode) _hdr.parentNode.insertBefore(_nd, _hdr.nextSibling);
                else document.body.insertBefore(_nd, document.body.firstChild);
            }
        }

    }



    if (!analysisData) {

        renderEmptyState();

    }

}

async function loadProfileAnalysisHistory(profileIdentity, page = 1) {
    const panel = document.getElementById("profileAnalysisHistory");
    if (!panel || !profileIdentity) return;
    const slugMatch = String(profileIdentity).match(/(?:linkedin\.com\/in\/)?([^/?#]+)\/?$/i);
    const slug = slugMatch ? slugMatch[1] : String(profileIdentity);
    panel.style.display = "block";
    const rows = document.getElementById("profileRunRows");
    rows.innerHTML = "<p style='color:#64748b;font-size:13px;'>Loading saved profile history…</p>";
    try {
        const encodedSlug = encodeURIComponent(slug);
        const [profileResponse, runResponse, trendResponse] = await Promise.all([
            fetch(`/api/dashboard/profiles/${encodedSlug}`),
            fetch(`/api/dashboard/profiles/${encodedSlug}/runs?page=${page}&per_page=10`),
            fetch(`/api/dashboard/trends?profile=${encodedSlug}&months=12`)
        ]);
        const profileData = await profileResponse.json();
        const runData = await runResponse.json();
        const trendData = await trendResponse.json();
        if (!profileResponse.ok && !runResponse.ok) throw new Error(profileData.error || runData.error || "Profile history is unavailable.");
        const history = profileData.history || null;
        const note = document.getElementById("profileHistoryNote");
        note.textContent = history
            ? `Stored successful snapshot updated ${formatDateDDMMYY(history.historical_analyzed_at)} · ${history.total_activities == null ? "activity count unavailable" : `${Number(history.total_activities).toLocaleString()} accumulated activities`}. New run summaries are tracked from this release forward.`
            : "No preserved successful activity snapshot exists for this profile. Run history starts with this release.";
        const runRows = runData.items || [];
        if (!runRows.length) {
            rows.innerHTML = `<p style='color:#64748b;font-size:13px;'>No durable analysis runs recorded for this profile yet. Earlier runs were not reconstructed.</p>`;
        } else {
            rows.innerHTML = `<div style='overflow:auto'><table style='width:100%;border-collapse:collapse;font-size:13px;'><thead><tr><th style='text-align:left;padding:9px;border-bottom:1px solid #e2e8f0;'>Date</th><th style='text-align:left;padding:9px;border-bottom:1px solid #e2e8f0;'>Requested range</th><th style='text-align:left;padding:9px;border-bottom:1px solid #e2e8f0;'>Status</th><th style='text-align:left;padding:9px;border-bottom:1px solid #e2e8f0;'>Activities</th><th style='text-align:left;padding:9px;border-bottom:1px solid #e2e8f0;'>Posts</th><th style='text-align:left;padding:9px;border-bottom:1px solid #e2e8f0;'>Videos</th><th style='text-align:left;padding:9px;border-bottom:1px solid #e2e8f0;'>Change</th></tr></thead><tbody>${runRows.map((run, index) => {
                const previous = runRows[index + 1];
                const sameRange = JSON.stringify(run.profile_requested_date_range || run.requested_date_range || null) === JSON.stringify(previous?.profile_requested_date_range || previous?.requested_date_range || null);
                const sameTypes = JSON.stringify([...(run.profile_requested_activity_types || run.requested_activity_types || [])].sort()) === JSON.stringify([...(previous?.profile_requested_activity_types || previous?.requested_activity_types || [])].sort());
                const comparable = previous && sameRange && sameTypes && run.total_activities != null && previous.total_activities != null;
                const delta = comparable ? Number(run.total_activities) - Number(previous.total_activities) : null;
                const change = delta == null ? (previous ? "Filters differ" : "—") : `${delta > 0 ? "+" : ""}${delta} vs previous comparable run`;
                const range = run.profile_requested_date_range || run.requested_date_range || {};
                const rangeLabel = range.start || range.end ? `${formatDateDDMMYY(range.start) || "…"} – ${formatDateDDMMYY(range.end) || "…"}` : "All dates";
                return `<tr><td style='padding:9px;border-bottom:1px solid #edf1f6;'>${escapeHTML(formatDateDDMMYY(run.completed_at || run.started_at))}</td><td style='padding:9px;border-bottom:1px solid #edf1f6;'>${escapeHTML(rangeLabel)}</td><td style='padding:9px;border-bottom:1px solid #edf1f6;'>${escapeHTML(run.status || "Unknown")}</td><td style='padding:9px;border-bottom:1px solid #edf1f6;'>${run.total_activities == null ? "—" : Number(run.total_activities).toLocaleString()}</td><td style='padding:9px;border-bottom:1px solid #edf1f6;'>${run.posts_count == null ? "—" : Number(run.posts_count).toLocaleString()}</td><td style='padding:9px;border-bottom:1px solid #edf1f6;'>${run.videos_count == null ? "—" : Number(run.videos_count).toLocaleString()}</td><td style='padding:9px;border-bottom:1px solid #edf1f6;'>${escapeHTML(change)}</td></tr>`;
            }).join("")}</tbody></table></div>`;
        }
        const pager = document.getElementById("profileRunPager");
        pager.style.display = Number(runData.total_pages || 1) > 1 ? "flex" : "none";
        document.getElementById("profileRunPageLabel").textContent = `Page ${runData.page || 1} of ${runData.total_pages || 1}`;
        document.getElementById("profileRunPrev").disabled = Number(runData.page || 1) <= 1;
        document.getElementById("profileRunNext").disabled = Number(runData.page || 1) >= Number(runData.total_pages || 1);
        document.getElementById("profileRunPrev").onclick = () => loadProfileAnalysisHistory(slug, Math.max(1, Number(runData.page || 1) - 1));
        document.getElementById("profileRunNext").onclick = () => loadProfileAnalysisHistory(slug, Number(runData.page || 1) + 1);
        renderProfileTrend(trendData.items || []);
    } catch (error) {
        rows.innerHTML = `<p style='color:#9b3c32;font-size:13px;'>${escapeHTML(error.message || "Profile history could not be loaded.")}</p>`;
    }
}

function renderProfileTrend(items) {
    const host = document.getElementById("profileTrendChart");
    if (!host) return;
    const known = items.filter(item => item.activity_count !== null && item.activity_count !== undefined);
    if (!known.length) { host.innerHTML = "<p style='color:#64748b;font-size:13px;'>No dated activity records are available for a monthly profile trend. Empty months remain unknown.</p>"; return; }
    const width = 760, height = 180, left = 34, top = 15, bottom = 30, max = Math.max(1, ...known.map(item => Number(item.activity_count))), plotH = height - top - bottom, plotW = width - left - 10;
    const segments = [];
    let segment = [];
    items.forEach((item, index) => {
        if (item.activity_count == null) {
            if (segment.length) segments.push(segment);
            segment = [];
            return;
        }
        const x = left + index * plotW / Math.max(1, items.length - 1);
        const y = top + plotH - Number(item.activity_count) / max * plotH;
        segment.push(`${x},${y}`);
    });
    if (segment.length) segments.push(segment);
    const lines = segments.filter(points => points.length > 1).map(points => `<polyline points='${points.join(" ")}' fill='none' stroke='#1770cf' stroke-width='3'/>`).join("");
    host.innerHTML = `<h4 style='margin:0 0 5px;font-size:14px;'>Monthly stored activity trend</h4><svg viewBox='0 0 ${width} ${height}' role='img' aria-label='Monthly stored activity trend' style='width:100%;height:180px;'><line x1='${left}' y1='${top + plotH}' x2='${width - 10}' y2='${top + plotH}' stroke='#dbe4ee'/>${lines}${items.map((item, index) => { const x = left + index * plotW / Math.max(1, items.length - 1); const isUnknown = item.activity_count == null; const y = isUnknown ? top + plotH + 7 : top + plotH - Number(item.activity_count) / max * plotH; return `<circle cx='${x}' cy='${y}' r='4' fill='${isUnknown ? "none" : "#fff"}' stroke='${isUnknown ? "#b6c2d0" : "#1770cf"}' stroke-width='2'><title>${escapeHTML(item.month)}: ${isUnknown ? "unknown coverage" : `${item.activity_count} stored activities`}</title></circle><text x='${x}' y='${height - 7}' text-anchor='middle' font-size='10' fill='#728198'>${escapeHTML(item.month.slice(5))}</text>`; }).join("")}</svg><p style='margin:0;color:#64748b;font-size:11px;'>Only stored dated records are counted; no-data months are unknown.</p>`;
}



function renderAnalysisResults(data) {

    renderActivityPagination(data.activity_pagination);

    // 1. Retrieve Profile Details

    let storedProfile = {};

    try {

        storedProfile = JSON.parse(sessionStorage.getItem("selectedProfile") || "{}");

    } catch (e) {

        storedProfile = {};

    }



    const subjectName = data.subject_name || storedProfile.name || localStorage.getItem("selectedFriend") || "Selected Profile";

    const headline = data.headline || storedProfile.headline || localStorage.getItem("selectedHeadline") || "LinkedIn Profile";

    const profileUrl = data.subject_url || storedProfile.profile_url || localStorage.getItem("selectedProfileUrl") || "";

    const source = data.data_source || storedProfile.source || localStorage.getItem("selectedSource") || "real_linkedin";



    // Header fields

    const nameEl = document.getElementById("subjectName");

    if (nameEl) nameEl.textContent = subjectName;



    const headlineEl = document.getElementById("subjectHeadline");

    if (headlineEl) headlineEl.textContent = headline;



    const urlLinkEl = document.getElementById("subjectUrlLink");

    if (urlLinkEl) {

        if (profileUrl) {

            urlLinkEl.textContent = profileUrl;

            urlLinkEl.href = profileUrl;

            urlLinkEl.style.display = "inline";

        } else {

            urlLinkEl.style.display = "none";

        }

    }



    const badgeEl = document.getElementById("subjectSourceBadge");

    if (badgeEl) {

        badgeEl.className = "badge-source " + getBadgeClass(source);

        badgeEl.textContent = getBadgeLabel(source);

    }



    // Determine if this is a multi-profile result

    const isMultiProfile = Boolean(data.is_multi_profile || (data.profile_results && data.profile_results.length > 1));

    const profileResults = (data.profile_results && data.profile_results.length > 0)

        ? data.profile_results

        : [data];



    // Check for historical data in single or multi-profile analyses

    const singleRes = profileResults[0] || data;

    const topActivities = data.activities || [];

    const histData = data.historical_successful_analysis || singleRes.historical_successful_analysis || null;



    // Single-profile historical fallback condition

    const isSingleUsingHistorical = !isMultiProfile && (!topActivities || topActivities.length === 0) && (data.total_activities === 0) && Boolean(histData && Array.isArray(histData.activities) && histData.activities.length > 0);



    // Multi-profile historical check

    let multiHistoricalCount = 0;

    if (isMultiProfile) {

        profileResults.forEach(pr => {

            const prActs = pr.activities || [];

            if ((!prActs || prActs.length === 0) && pr.historical_successful_analysis && Array.isArray(pr.historical_successful_analysis.activities) && pr.historical_successful_analysis.activities.length > 0) {

                multiHistoricalCount++;

            }

        });

    }

    const isMultiUsingHistorical = isMultiProfile && (multiHistoricalCount > 0);

    const isUsingHistorical = isSingleUsingHistorical || isMultiUsingHistorical;



    // Subject badge for historical state

    let histBadge = document.getElementById("subjectHistoricalBadge");

    if (isUsingHistorical) {

        if (!histBadge) {

            histBadge = document.createElement("span");

            histBadge.id = "subjectHistoricalBadge";

            histBadge.className = "badge-source badge-historical";

            const metaContainer = document.querySelector(".subject-meta");

            if (metaContainer) metaContainer.appendChild(histBadge);

        }

        histBadge.textContent = "Preserved History";

        histBadge.style.display = "inline-block";

    } else if (histBadge) {

        histBadge.style.display = "none";

    }



    // 2. Date Range

    const dateRange = data.date_range || {};

    const dateRangeEl = document.getElementById("dateRangeDisplay");

    if (dateRangeEl) {

        if (isSingleUsingHistorical && histData) {

            const histRange = histData.date_range || {};

            const histRangeStr = formatDateRangeDDMMYY(histRange.start, histRange.end);

            const curRangeStr = formatDateRangeDDMMYY(dateRange.start, dateRange.end);

            const isoRange = (histRange.start && histRange.end)

                ? (histRange.start === histRange.end ? histRange.start : `${histRange.start} → ${histRange.end}`)

                : (histRange.start ? `Since ${histRange.start}` : "");

            const labelText = isoRange ? `${histRangeStr} (${isoRange})` : histRangeStr;

            dateRangeEl.innerHTML = `<span style="color:#166534; font-weight:600;">Historical Period: ${escapeHTML(labelText)}</span>`;

            dateRangeEl.title = `Current requested: ${curRangeStr} (Attempt failed/blocked). Displaying preserved historical records.`;

        } else if (dateRange.start && dateRange.end) {

            const rangeStr = formatDateRangeDDMMYY(dateRange.start, dateRange.end);

            dateRangeEl.textContent = dateRange.start === dateRange.end

                ? `Date: ${formatDateDDMMYY(dateRange.start)}`

                : `Period: ${rangeStr}`;

            dateRangeEl.title = "";

        } else if (dateRange.start) {

            dateRangeEl.textContent = `Since ${formatDateDDMMYY(dateRange.start)}`;

            dateRangeEl.title = "";

        } else {

            dateRangeEl.textContent = "All Dates";

            dateRangeEl.title = "";

        }

    }



    // 3. Calculated Statistics (Strictly calculated, no hardcoded values)

    let userFilterTypes = [];

    try {

        const filterStr = localStorage.getItem("activityFilter");

        if (filterStr) {

            const parsedFilter = JSON.parse(filterStr);

            if (Array.isArray(parsedFilter.activities)) {

                userFilterTypes = parsedFilter.activities.map(a => (a || "").toLowerCase().trim()).filter(Boolean);

            }

        }

    } catch (e) {}

    if (data.filtered_activity_types && Array.isArray(data.filtered_activity_types)) {

        userFilterTypes = data.filtered_activity_types.map(a => (a || "").toLowerCase().trim()).filter(Boolean);

    }



    function matchesUserFilter(act) {

        if (!userFilterTypes.length || userFilterTypes.includes("all")) return true;

        const actType = (act.action_type || act.type || "").toLowerCase().trim();

        for (const f of userFilterTypes) {

            const normF = f.endsWith("s") ? f.slice(0, -1) : f;

            if (normF === "comment" && actType === "comment") return true;

            if ((normF === "like" || normF === "reaction") && (actType === "like" || actType === "reaction")) return true;

            if ((normF === "share" || normF === "repost") && (actType === "share" || actType === "repost")) return true;

            if (normF === "video" && (actType === "video" || act.video_url || act.video_thumbnail || act.is_video || (act.video_status && act.video_status !== "No video"))) return true;

            if (normF === "post" && actType === "post") return true;

        }

        return false;

    }



    let totalActs = data.total_activities || 0;

    let postsCount = data.posts_count !== undefined ? data.posts_count : (data.by_type?.post || data.by_type?.posts || 0);

    let commentsCount = data.comments_count !== undefined ? data.comments_count : (data.by_type?.comment || data.by_type?.comments || data.comments || 0);

    let likesCount = data.likes_count !== undefined ? data.likes_count : (data.by_type?.like || data.by_type?.likes || data.likes || 0);

    let sharesCount = data.shares_count !== undefined ? data.shares_count : (data.by_type?.share || data.by_type?.shares || data.shares || 0);

    let videosCount = data.videos_count !== undefined ? data.videos_count : (data.by_type?.video || data.by_type?.videos || 0);

    let totalEng = data.total_engagement || 0;

    let avgEng = data.average_engagement || 0;

    let byType = Object.assign({}, data.by_type || {});



    // Fallback metrics to historical data when current attempt has zero activities

    if (isSingleUsingHistorical && histData) {

        let hActs = Array.isArray(histData.activities) ? histData.activities : [];

        if (userFilterTypes.length > 0 && !userFilterTypes.includes("all")) {

            hActs = hActs.filter(matchesUserFilter);

        }



        totalActs = hActs.length;

        postsCount = hActs.filter(a => (a.action_type || a.type) === "post").length;

        commentsCount = hActs.filter(a => (a.action_type || a.type) === "comment").length;

        likesCount = hActs.filter(a => ["like", "reaction"].includes(a.action_type || a.type)).length;

        sharesCount = hActs.filter(a => ["share", "repost"].includes(a.action_type || a.type)).length;

        videosCount = hActs.filter(a => (a.action_type || a.type) === "video" || a.video_url || a.video_thumbnail || a.is_video || (a.video_status && a.video_status !== "No video")).length;



        byType = {};

        if (postsCount > 0) byType["post"] = postsCount;

        if (commentsCount > 0) byType["comment"] = commentsCount;

        if (likesCount > 0) byType["like"] = likesCount;

        if (sharesCount > 0) byType["share"] = sharesCount;

        if (videosCount > 0) byType["video"] = videosCount;



        totalEng = histData.total_engagement || 0;

        avgEng = totalActs > 0 ? Math.round((totalEng / totalActs) * 100) / 100 : 0;

    } else if (isMultiUsingHistorical) {

        // Multi-profile aggregate historical recalculation

        let aggTotal = 0;

        let aggPosts = 0;

        let aggComments = 0;

        let aggLikes = 0;

        let aggShares = 0;

        let aggVideos = 0;

        let aggEng = 0;

        let aggByType = {};



        profileResults.forEach(pr => {

            const prActs = pr.activities || [];

            const prHist = pr.historical_successful_analysis;

            const useHist = (prActs.length === 0) && Boolean(prHist && Array.isArray(prHist.activities) && prHist.activities.length > 0);

            const sourceObj = useHist ? prHist : pr;

            const pTotal = sourceObj.total_activities || (sourceObj.activities ? sourceObj.activities.length : 0);

            aggTotal += pTotal;

            aggPosts += sourceObj.posts_count !== undefined ? sourceObj.posts_count : (sourceObj.by_type?.post || sourceObj.by_type?.posts || 0);

            aggComments += sourceObj.comments_count !== undefined ? sourceObj.comments_count : (sourceObj.by_type?.comment || sourceObj.by_type?.comments || sourceObj.comments || 0);

            aggLikes += sourceObj.likes_count !== undefined ? sourceObj.likes_count : (sourceObj.by_type?.like || sourceObj.by_type?.likes || sourceObj.likes || 0);

            aggShares += sourceObj.shares_count !== undefined ? sourceObj.shares_count : (sourceObj.by_type?.share || sourceObj.by_type?.shares || sourceObj.shares || 0);

            aggVideos += sourceObj.videos_count !== undefined ? sourceObj.videos_count : (sourceObj.by_type?.video || sourceObj.by_type?.videos || 0);

            aggEng += sourceObj.total_engagement || 0;



            const pByType = sourceObj.by_type || {};

            for (const [k, v] of Object.entries(pByType)) {

                aggByType[k] = (aggByType[k] || 0) + v;

            }

        });



        totalActs = aggTotal;

        postsCount = aggPosts;

        commentsCount = aggComments;

        likesCount = aggLikes;

        sharesCount = aggShares;

        videosCount = aggVideos;

        totalEng = aggEng;

        avgEng = totalActs > 0 ? Math.round((totalEng / totalActs) * 100) / 100 : 0;

        byType = aggByType;

    }



    setElementText("totalActivities", totalActs);

    setElementText("postsCount", postsCount);

    setElementText("commentsCount", commentsCount);

    setElementText("likesCount", likesCount);

    setElementText("sharesCount", sharesCount);

    setElementText("videosCount", videosCount);



    // Legacy elements for test compatibility

    setElementText("totalEngagement", totalEng);

    setElementText("avgEngagement", avgEng);

    setElementText("postCount", postsCount);

    setElementText("commentCount", commentsCount);

    setElementText("shareCount", sharesCount);



    // Historical banner above stats grid

    let statsBanner = document.getElementById("historicalStatsBanner");

    if (isUsingHistorical) {

        if (!statsBanner) {

            statsBanner = document.createElement("div");

            statsBanner.id = "historicalStatsBanner";

            statsBanner.className = "historical-stats-banner";

            statsBanner.style.cssText = "background:#f0fdf4; border:1px solid #86efac; border-radius:8px; padding:12px 18px; margin-bottom:20px; color:#166534; font-size:13.5px; display:flex; align-items:center; gap:10px;";

            const statsGrid = document.querySelector(".stats-grid");

            if (statsGrid && statsGrid.parentNode) {

                statsGrid.parentNode.insertBefore(statsBanner, statsGrid);

            }

        }

        const failStatus = data.status_type || "RATE_LIMITED_999";

        const isRateLimit = (failStatus === "RATE_LIMITED_999" || failStatus === "BLOCKED");

        const friendlyMsg = isRateLimit

            ? "LinkedIn is temporarily limiting automated requests. Please try again later. Your previously saved historical analysis is preserved."

            : "Current LinkedIn attempt was unavailable or protected. Your previously saved historical analysis is preserved.";

        statsBanner.innerHTML = `<span>🕒</span> <div><strong>Displaying Metrics from Preserved Historical Analysis</strong><br><span style="font-size:12.5px; opacity:0.9;">${escapeHTML(friendlyMsg)} (Showing ${totalActs} preserved historical activities).</span></div>`;

        statsBanner.style.display = "flex";

    } else if (statsBanner) {

        statsBanner.style.display = "none";

    }



    // 4. Breakdown by Activity Type

    const breakdownEl = document.getElementById("typeBreakdown");

    if (breakdownEl) {

        breakdownEl.innerHTML = "";



        if (Object.keys(byType).length === 0 || totalActs === 0) {

            breakdownEl.innerHTML = "<p style='color:#64748b; font-size:13px;'>No activity types recorded.</p>";

        } else {

            for (const [type, count] of Object.entries(byType)) {

                const pct = totalActs > 0 ? Math.round((count / totalActs) * 100) : 0;

                const item = document.createElement("div");

                item.className = "breakdown-item";

                item.innerHTML = `

                    <div class="breakdown-header">

                        <span class="breakdown-name">${formatActivityName(type)}</span>

                        <span class="breakdown-count">${count} (${pct}%)</span>

                    </div>

                    <div class="progress-bar-bg">

                        <div class="progress-bar-fill" style="width: ${pct}%"></div>

                    </div>

                `;

                breakdownEl.appendChild(item);

            }

        }

    }



    // 4b. Sentiment & Tone Distribution (Phase 3 - Issue 6)

    const sentimentEl = document.getElementById("sentimentBreakdown");

    if (sentimentEl) {

        sentimentEl.innerHTML = "";



        let sSummary = data.sentiment_summary || null;

        if (!sSummary && isSingleUsingHistorical && histData && histData.sentiment_summary) {

            sSummary = histData.sentiment_summary;

        }



        // Compute dynamically if not present but activities exist

        if (!sSummary) {

            let actsToEvaluate = [];

            if (topActivities && topActivities.length > 0) {

                actsToEvaluate = topActivities;

            } else if (isSingleUsingHistorical && histData && Array.isArray(histData.activities)) {

                actsToEvaluate = histData.activities;

            } else if (isMultiProfile) {

                profileResults.forEach(pr => {

                    const pa = pr.activities || [];

                    if (pa.length > 0) {

                        actsToEvaluate = actsToEvaluate.concat(pa);

                    } else if (pr.historical_successful_analysis && Array.isArray(pr.historical_successful_analysis.activities)) {

                        actsToEvaluate = actsToEvaluate.concat(pr.historical_successful_analysis.activities);

                    }

                });

            }



            if (actsToEvaluate.length > 0) {

                let pos = 0, neg = 0, neu = 0;

                const tones = {};

                actsToEvaluate.forEach(a => {

                    const s = a.sentiment;

                    if (s === "Positive") pos++;

                    else if (s === "Negative") neg++;

                    else if (s === "Neutral") neu++;

                    const t = a.tone;

                    if (t && t !== "Not available" && t !== "Analysis unavailable") {

                        tones[t] = (tones[t] || 0) + 1;

                    }

                });

                let topTone = "Neutral";

                let maxT = 0;

                for (const [k, v] of Object.entries(tones)) {

                    if (v > maxT) { maxT = v; topTone = k; }

                }

                if (pos + neg + neu > 0) {

                    sSummary = {

                        positive: pos,

                        negative: neg,

                        neutral: neu,

                        dominant_tone: topTone

                    };

                }

            }

        }



        const posCount = (sSummary && typeof sSummary.positive === "number") ? sSummary.positive : 0;

        const neuCount = (sSummary && typeof sSummary.neutral === "number") ? sSummary.neutral : 0;

        const negCount = (sSummary && typeof sSummary.negative === "number") ? sSummary.negative : 0;

        const totalEvaluated = posCount + neuCount + negCount;



        if (!sSummary || totalEvaluated === 0) {

            sentimentEl.innerHTML = "<p style='color:#64748b; font-size:13px;'>No sentiment data recorded.</p>";

        } else {

            const posPct = Math.round((posCount / totalEvaluated) * 100);

            const neuPct = Math.round((neuCount / totalEvaluated) * 100);

            const negPct = Math.round((negCount / totalEvaluated) * 100);

            const dominantTone = sSummary.dominant_tone || "Neutral";



            sentimentEl.innerHTML = `

                <div class="breakdown-item" style="margin-bottom:12px;">

                    <div class="breakdown-header">

                        <span class="breakdown-name" style="color:#16a34a; font-weight:600;">Positive</span>

                        <span class="breakdown-count">${posCount} (${posPct}%)</span>

                    </div>

                    <div class="progress-bar-bg">

                        <div class="progress-bar-fill" style="width: ${posPct}%; background:#16a34a;"></div>

                    </div>

                </div>

                <div class="breakdown-item" style="margin-bottom:12px;">

                    <div class="breakdown-header">

                        <span class="breakdown-name" style="color:#475569; font-weight:600;">Neutral</span>

                        <span class="breakdown-count">${neuCount} (${neuPct}%)</span>

                    </div>

                    <div class="progress-bar-bg">

                        <div class="progress-bar-fill" style="width: ${neuPct}%; background:#64748b;"></div>

                    </div>

                </div>

                <div class="breakdown-item" style="margin-bottom:12px;">

                    <div class="breakdown-header">

                        <span class="breakdown-name" style="color:#dc2626; font-weight:600;">Negative</span>

                        <span class="breakdown-count">${negCount} (${negPct}%)</span>

                    </div>

                    <div class="progress-bar-bg">

                        <div class="progress-bar-fill" style="width: ${negPct}%; background:#dc2626;"></div>

                    </div>

                </div>

                <div style="margin-top:14px; padding-top:10px; border-top:1px solid #eef2f6; display:flex; justify-content:space-between; align-items:center; font-size:12.5px;">

                    <span style="color:#64748b; font-weight:500;">Dominant Tone</span>

                    <span style="background:#f1f5f9; color:#0f172a; padding:3px 9px; border-radius:12px; font-weight:600; font-size:12px; border:1px solid #e2e8f0;">${escapeHTML(dominantTone)}</span>

                </div>

            `;

        }

    }



    // 5. Render Actual Post Cards Feed

    const listEl = document.getElementById("activitiesTimeline");

    const countEl = document.getElementById("timelineItemCount");

    if (!listEl) return;



    const defaultAvatar = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2394a3b8'%3E%3Cpath d='M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z'/%3E%3C/svg%3E";



    if (isMultiProfile) {

        listEl.innerHTML = "";

        let totalItems = 0;



        profileResults.forEach((pRes, idx) => {

            const pName = pRes.subject_name || `Profile ${idx+1}`;

            const pHeadline = pRes.headline || "LinkedIn Profile";

            const pUrl = pRes.subject_url || "";

            const pAvatar = pRes.picture && !isDashboardMediaKnownBroken(pRes.picture) ? pRes.picture : defaultAvatar;



            const pActs = pRes.activities || [];

            const pHist = pRes.historical_successful_analysis;

            const pHistActs = (pHist && Array.isArray(pHist.activities)) ? pHist.activities : [];

            const pIsHistorical = (pActs.length === 0) && (pHistActs.length > 0);

            const effectivePActs = pIsHistorical ? pHistActs : pActs;



            const pRange = (pIsHistorical && pHist && pHist.date_range) ? pHist.date_range : (pRes.date_range || {});

            const rangeStr = formatDateRangeDDMMYY(pRange.start, pRange.end);



            totalItems += effectivePActs.length;



            const sectionEl = document.createElement("section");

            sectionEl.className = "profile-section-divider";



            const histSectionBadge = pIsHistorical

                ? `<span class="profile-section-badge badge-historical" style="background:#fef3c7; color:#92400e; border:1px solid #fcd34d;">Preserved History</span>`

                : "";



            const countDisplayStr = pIsHistorical

                ? `${effectivePActs.length} historical activities`

                : `${effectivePActs.length} activit${effectivePActs.length === 1 ? 'y' : 'ies'} found`;



            sectionEl.innerHTML = `

                <div class="profile-section-header">

                    <div class="profile-section-info">

                        <img class="profile-section-avatar" src="${escapeAttr(pAvatar)}" alt="${escapeAttr(pName)}" onerror="markDashboardImageFailed(this, '${defaultAvatar}');">

                        <div>

                            <h3 class="profile-section-title">

                                <span>${escapeHTML(pName)}</span>

                            </h3>

                            <div class="profile-section-headline">${escapeHTML(pHeadline)}</div>

                            <div class="profile-section-meta">

                                <span class="profile-section-badge">Date Range: ${escapeHTML(rangeStr)}</span>

                                ${histSectionBadge}

                                ${pUrl ? `<a href="${escapeAttr(pUrl)}" target="_blank" rel="noopener noreferrer" style="color:#0a66c2; font-size:12px; text-decoration:none; font-weight:600;">View Profile ↗</a>` : ''}

                            </div>

                        </div>

                    </div>

                    <div style="font-size:13px; font-weight:700; color:${pIsHistorical ? '#166534' : '#475569'};">

                        ${countDisplayStr}

                    </div>

                </div>

                <div class="profile-section-feed" id="profileFeed_${idx}"></div>

            `;



            const feedEl = sectionEl.querySelector(`#profileFeed_${idx}`);



            if (effectivePActs.length === 0) {

                const emptyMsg = pRes.empty_message || `No activity data available for ${pName} in this date range.`;

                const pStatusType = pRes.status_type || "";

                let pIcon = "ℹ️";

                if (pStatusType === "RATE_LIMITED_999") pIcon = "⏳";

                else if (pStatusType === "AUTH_REQUIRED" || pStatusType === "SESSION_EXPIRED" || pStatusType === "AUTH_FAILED") pIcon = "🔒";

                else if (pStatusType === "ACTIVITY_FEED_RESTRICTED" || pStatusType === "VIDEO_ACTIVITIES_INACCESSIBLE") pIcon = "🛡️";

                else if (pStatusType === "NO_VIDEO_ACTIVITIES_FOUND") pIcon = "🎥";

                else if (pStatusType === "NETWORK_TIMEOUT") pIcon = "🌐";

                else if (pStatusType === "NO_PUBLIC_ACTIVITIES") pIcon = "📭";



                feedEl.innerHTML = `

                    <div style="background:#f8fafc; border:1px dashed #cbd5e1; border-radius:8px; padding:24px; text-align:center; color:#64748b; font-size:14px;">

                        <div style="font-size:24px; margin-bottom:8px;">${pIcon}</div>

                        <p><strong>${escapeHTML(emptyMsg)}</strong></p>

                    </div>

                `;

            } else {

                if (pIsHistorical) {

                    const histNotice = document.createElement("div");

                    histNotice.style.cssText = "background:#f0fdf4; border:1px solid #86efac; border-radius:6px; padding:10px 14px; margin-bottom:12px; color:#166534; font-size:12.5px;";

                    histNotice.innerHTML = `<strong>🕒 Displaying Preserved Historical Analysis for ${escapeHTML(pName)}</strong> (Current attempt: ${escapeHTML(pRes.status_type || "Unavailable")}).`;

                    feedEl.appendChild(histNotice);

                }



                const CHUNK_SIZE = 20;

                let renderedPCount = 0;

                function renderNextPChunk() {

                    const nextBatch = effectivePActs.slice(renderedPCount, renderedPCount + CHUNK_SIZE);

                    nextBatch.forEach(act => {

                        const card = createActivityPostCard(act, pName, pIsHistorical);

                        feedEl.appendChild(card);

                    });

                    renderedPCount += nextBatch.length;



                    const existingBtn = sectionEl.querySelector(`.load-more-prof-${idx}`);

                    if (existingBtn) existingBtn.remove();



                    if (renderedPCount < effectivePActs.length) {

                        const btn = document.createElement("button");

                        btn.className = `action-btn load-more-prof-${idx}`;

                        btn.style.cssText = "display:block; width:100%; max-width:280px; margin:16px auto; padding:10px 20px; font-weight:600; font-size:13px; text-align:center; background:#f1f5f9; color:#0a66c2; border:1px solid #cbd5e1; border-radius:20px; cursor:pointer;";

                        btn.textContent = `Load More for ${escapeHTML(pName)} (${renderedPCount} of ${effectivePActs.length})`;

                        btn.onclick = () => renderNextPChunk();

                        feedEl.appendChild(btn);

                    }

                }

                renderNextPChunk();

            }



            listEl.appendChild(sectionEl);

        });



        if (countEl) {

            const overallCount = Number(data.activity_pagination?.total_items) || totalItems;
            countEl.textContent = `${overallCount.toLocaleString()} item${overallCount === 1 ? '' : 's'}`;

        }



    } else {

        // Single Profile rendering

        const singleRes = profileResults[0] || data;

        const activities = singleRes.activities || [];

        const histData = data.historical_successful_analysis || singleRes.historical_successful_analysis;



        if (countEl) {

            if (activities.length > 0) {

                const overallCount = Number(data.activity_pagination?.total_items) || activities.length;
                countEl.textContent = `${overallCount.toLocaleString()} item${overallCount === 1 ? '' : 's'}`;

            } else if (histData && histData.activities && histData.activities.length > 0) {

                const overallCount = Number(data.activity_pagination?.total_items) || histData.activities.length;
                countEl.textContent = `${overallCount.toLocaleString()} historical item${overallCount === 1 ? '' : 's'}`;

            } else {

                countEl.textContent = "0 items";

            }

        }



        if (activities.length === 0) {

            const emptyTitle = singleRes.empty_message || data.empty_message || "Activity could not be retrieved from LinkedIn.";

            const statusType = singleRes.status_type || data.status_type || "";

            const lowerTitle = emptyTitle.toLowerCase();



            let iconEmoji = "🔒";

            let displayTitle = emptyTitle;

            let descText = `No activity data available for <strong>${escapeHTML(subjectName)}</strong> in the selected date range.`;



            if (statusType === "RATE_LIMITED_999" || lowerTitle.includes("999") || lowerTitle.includes("rate limit")) {

                iconEmoji = "⏳";

                displayTitle = "LinkedIn Rate Limit Encountered (HTTP 999)";

                descText = `No activity data available: LinkedIn temporarily throttled automated requests for <strong>${escapeHTML(subjectName)}</strong>. Please wait a few moments before retrying.`;

            } else if (statusType === "ACTIVITY_FEED_RESTRICTED" || lowerTitle.includes("private or restricted") || lowerTitle.includes("feed is private")) {

                iconEmoji = "🛡️";

                displayTitle = "Profile Activity Restricted";

                descText = `No activity data available: The activity feed for <strong>${escapeHTML(subjectName)}</strong> is set to private or restricted by the LinkedIn member.`;

            } else if (statusType === "AUTH_REQUIRED" || lowerTitle.includes("authwall") || lowerTitle.includes("session") || lowerTitle.includes("authentication")) {

                iconEmoji = "🔒";

                displayTitle = "LinkedIn Authentication Required";

                descText = `No activity data available: Profile activity for <strong>${escapeHTML(subjectName)}</strong> is protected behind LinkedIn's authentication wall. An authenticated session or manual export is required.`;

            } else if (statusType === "ACTIVITY_SECTION_UNAVAILABLE" || lowerTitle.includes("not accessible for this profile")) {

                iconEmoji = "ℹ️";

                displayTitle = "Activity Section Unavailable";

                descText = `No activity data available: LinkedIn returned the profile page for <strong>${escapeHTML(subjectName)}</strong>, but does not expose public activity tabs for this account to logged-out visitors. An authenticated session is required to inspect activities.`;

            } else if (statusType === "PROFILE_NOT_FOUND" || lowerTitle.includes("not found")) {

                iconEmoji = "❓";

                displayTitle = "LinkedIn Profile Not Found";

                descText = `No activity data available: LinkedIn reported that the profile URL for <strong>${escapeHTML(subjectName)}</strong> does not exist (HTTP 404 or Page Not Found). Please verify the profile URL.`;

            } else if (statusType === "NO_ACTIVITIES_IN_RANGE" || lowerTitle.includes("date range") || lowerTitle.includes("no activities found for selected")) {

                iconEmoji = "📅";

                displayTitle = "No Activities in Selected Date Range";

                descText = `No activity data available in date range: Activities exist on LinkedIn for <strong>${escapeHTML(subjectName)}</strong>, but none fall within the chosen date range. Try broadening the date filter.`;

            } else if (statusType === "NO_PUBLIC_ACTIVITIES" || lowerTitle.includes("no public activity found")) {

                iconEmoji = "📭";

                displayTitle = "No Public Activities Found";

                descText = `No activity data available: The activity section was checked on LinkedIn, but <strong>${escapeHTML(subjectName)}</strong> currently has no public activities visible.`;

            } else if (statusType === "SECURITY_CHECKPOINT" || lowerTitle.includes("checkpoint") || lowerTitle.includes("challenge")) {

                iconEmoji = "🛡️";

                displayTitle = "Security Checkpoint Encountered";

                descText = `No activity data available: LinkedIn presented a security challenge for <strong>${escapeHTML(subjectName)}</strong>. Manual verification or an authenticated session is required.`;

            } else if (statusType === "NETWORK_TIMEOUT" || lowerTitle.includes("timeout") || lowerTitle.includes("unreachable")) {

                iconEmoji = "🌐";

                displayTitle = "Network Timeout";

                descText = `No activity data available: The connection to LinkedIn timed out or was unreachable. Please verify your network connection.`;

            } else if (statusType === "NO_VIDEO_ACTIVITIES_FOUND" || lowerTitle.includes("no video activities found")) {
                iconEmoji = "🎥";
                displayTitle = "No Video Activities Found";
                descText = `No video activities found: <strong>${escapeHTML(subjectName)}</strong> has not posted, liked, commented on, or shared any videos in the selected date range.`;
            } else if (statusType === "VIDEO_ACTIVITIES_INACCESSIBLE" || lowerTitle.includes("video activities are inaccessible") || lowerTitle.includes("video activities could not be retrieved")) {
                iconEmoji = "🔒";
                displayTitle = "Video Activities Inaccessible";
                descText = `Video activities are inaccessible: LinkedIn requires an active authenticated session to access member-level video likes, comments, and reactions for <strong>${escapeHTML(subjectName)}</strong>.`;
            } else if (statusType === "SESSION_EXPIRED" || statusType === "AUTH_FAILED" || lowerTitle.includes("session expired") || lowerTitle.includes("session failure")) {
                iconEmoji = "🔑";
                displayTitle = "Authentication / Session Failure";
                descText = `Authentication / Session Failure: The LinkedIn session is unavailable or expired. An authenticated session is required to retrieve video activities.`;
            }



            const statusNote = singleRes.retrieval_status ? `<p style="margin-top:10px; color:#475569; font-size:14px;"><strong>Diagnostic details:</strong> ${escapeHTML(singleRes.retrieval_status)}</p>` : "";



            // Check if preserved historical analysis is available

            if (histData && Array.isArray(histData.activities) && histData.activities.length > 0) {

                const histActs = histData.activities;

                const histRange = histData.date_range || {};

                const histRangeStr = formatDateRangeDDMMYY(histRange.start, histRange.end);

                const histAnalyzedAt = histData.historical_analyzed_at ? formatDateDDMMYY(histData.historical_analyzed_at) : "Earlier Session";



                listEl.innerHTML = `

                    <div class="diagnostic-current-banner" style="background:#fffbeb; border:1px solid #fcd34d; border-radius:8px; padding:16px 20px; margin-bottom:20px; color:#92400e;">

                        <div style="display:flex; align-items:center; gap:8px; font-weight:700; font-size:15px; margin-bottom:6px;">

                            <span>${iconEmoji}</span> Current Attempt: ${escapeHTML(displayTitle)}

                        </div>

                        <p style="font-size:13px; margin:0 0 10px 0; line-height:1.5;">${descText}</p>

                        ${statusNote}

                        <div style="background:#f0fdf4; border:1px solid #86efac; border-radius:6px; padding:12px 16px; margin-top:12px; color:#166534; font-size:13px;">

                            <strong>🕒 Displaying Preserved Historical Analysis</strong><br>

                            Retrieved: <strong>${escapeHTML(histAnalyzedAt)}</strong> | Filter Range: <strong>${escapeHTML(histRangeStr)}</strong> (${histActs.length} activities).

                            These activities were retrieved during a previous successful session and are preserved here for reference.

                        </div>

                    </div>

                `;



                // Filter preserved historical activities according to user filter

                let renderHistActs = histActs;

                if (typeof matchesUserFilter === "function") {

                    renderHistActs = histActs.filter(matchesUserFilter);

                }



                if (renderHistActs.length === 0) {

                    listEl.innerHTML = `

                        <div class="diagnostic-current-banner" style="background:#fffbeb; border:1px solid #fcd34d; border-radius:8px; padding:16px 20px; margin-bottom:20px; color:#92400e;">

                            <div style="display:flex; align-items:center; gap:8px; font-weight:700; font-size:15px; margin-bottom:6px;">

                                <span>${iconEmoji}</span> Current Attempt: ${escapeHTML(displayTitle)}

                            </div>

                            <p style="font-size:13px; margin:0 0 10px 0; line-height:1.5;">${descText}</p>

                            ${statusNote}

                        </div>

                        <div class="empty-state" style="text-align:center; padding:40px 20px; color:#64748b;">

                            <div style="font-size:40px; margin-bottom:12px;">📭</div>

                            <h3 style="font-size:16px; font-weight:600; color:#1e293b; margin-bottom:6px;">No Matching Historical Activities</h3>

                            <p style="font-size:13.5px; max-width:480px; margin:0 auto;">No activities matched the chosen filter in the preserved historical analysis for ${escapeHTML(subjectName)}.</p>

                        </div>

                    `;

                    return;

                }



                // Render preserved historical activities feed

                renderHistActs.forEach(act => {

                    const card = createActivityPostCard(act, subjectName, true);

                    listEl.appendChild(card);

                });

                return;

            }



            // No historical analysis available - render empty state

            listEl.innerHTML = `

                <div class="empty-state">

                    <div style="font-size:36px; margin-bottom:12px;">${iconEmoji}</div>

                    <h3>${escapeHTML(displayTitle)}</h3>

                    <p>${descText}</p>

                    ${statusNote}

                    <div style="display:flex; gap:12px; justify-content:center; margin-top:20px; flex-wrap:wrap;">

                        <a href="/activity-filter" class="action-btn">Modify Filters</a>

                        <a href="/connections" class="action-btn" style="background:#475569;">Import Offline LinkedIn Export (CSV/ZIP)</a>

                    </div>

                </div>

            `;

            return;

        }



        // Check completeness status

        const completeness = singleRes.retrieval_completeness || data.retrieval_completeness;

        let noticeHTML = "";

        if (completeness === "PARTIAL_CEILING_REACHED") {

            noticeHTML = `

                <div class="retrieval-notice partial-ceiling-notice" style="background:#fffbeb; border:1px solid #fcd34d; border-radius:8px; padding:14px 18px; margin-bottom:18px; color:#92400e; font-size:13.5px; display:flex; align-items:center; gap:10px;">

                    <span style="font-size:18px;">⚠️</span>

                    <div>

                        <strong>Partial Retrieval:</strong> The configured activity ceiling was reached (${activities.length} activities retrieved). Older activities outside this ceiling could exist on LinkedIn.

                    </div>

                </div>

            `;

        } else if (completeness === "PARTIAL_GUEST_LIMIT") {

            noticeHTML = `

                <div class="retrieval-notice partial-guest-notice" style="background:#f0fdf4; border:1px solid #86efac; border-radius:8px; padding:14px 18px; margin-bottom:18px; color:#166534; font-size:13.5px; display:flex; align-items:center; gap:10px;">

                    <span style="font-size:18px;">ℹ️</span>

                    <div>

                        <strong>Guest Public View:</strong> Displaying ${activities.length} activities visible to public visitors. Authenticated sessions or official LinkedIn data exports access deeper profile history.

                    </div>

                </div>

            `;

        }



        listEl.innerHTML = noticeHTML;

        const feedContainer = document.createElement("div");

        feedContainer.className = "feed-cards-container";

        listEl.appendChild(feedContainer);



        const RENDER_CHUNK_SIZE = 30;

        let renderedCount = 0;



        function renderNextChunk() {

            const nextSlice = activities.slice(renderedCount, renderedCount + RENDER_CHUNK_SIZE);

            nextSlice.forEach(act => {

                const card = createActivityPostCard(act, subjectName);

                feedContainer.appendChild(card);

            });

            renderedCount += nextSlice.length;



            const existingBtn = document.getElementById("loadMoreActivitiesBtn");

            if (existingBtn) existingBtn.remove();



            if (renderedCount < activities.length) {

                const loadMoreBtn = document.createElement("button");

                loadMoreBtn.id = "loadMoreActivitiesBtn";

                loadMoreBtn.className = "action-btn";

                loadMoreBtn.style.cssText = "display:block; width:100%; max-width:340px; margin:24px auto; padding:12px 24px; font-weight:700; font-size:14px; text-align:center; background:#0a66c2; color:white; border:none; border-radius:24px; cursor:pointer;";

                loadMoreBtn.textContent = `Load More Activities (${renderedCount} of ${activities.length} shown)`;

                loadMoreBtn.onclick = () => renderNextChunk();

                listEl.appendChild(loadMoreBtn);

            }

        }



        renderNextChunk();

    }

}

function renderActivityPagination(pagination) {
    const bar = document.getElementById("activityPagination");
    if (!bar || !pagination) return;
    const page = Number(pagination.page) || 1;
    const pages = Number(pagination.total_pages) || 1;
    const total = Number(pagination.total_items) || 0;
    const size = Number(pagination.per_page) || 20;
    bar.style.display = pages > 1 ? "flex" : "none";
    const label = document.getElementById("activityPageLabel");
    if (label) label.textContent = `Page ${page} of ${pages} | ${total.toLocaleString()} activities`;
    const select = document.getElementById("activityPageSize");
    if (select && Array.from(select.options).some(option => Number(option.value) === size)) select.value = String(size);
    const prev = document.getElementById("prevActivityPage");
    const next = document.getElementById("nextActivityPage");
    if (prev) prev.disabled = page <= 1;
    if (next) next.disabled = page >= pages;
}

async function fetchActivityPage(page, perPage) {
    const profile = JSON.parse(sessionStorage.getItem("selectedProfile") || "{}");
    let filter = {};
    try { filter = JSON.parse(localStorage.getItem("activityFilter") || "{}"); } catch (e) {}
    const params = new URLSearchParams();
    const subjectId = profile.subject_id || profile.id || (!profile.name ? localStorage.getItem("selectedSubjectId") : "");
    const profileUrl = profile.profile_url || (!profile.name ? localStorage.getItem("selectedProfileUrl") : "");
    const subjectName = profile.name || localStorage.getItem("selectedFriend");
    if (subjectId) params.set("subject_id", subjectId);
    if (profileUrl) params.set("profile_url", profileUrl);
    if (subjectName) params.set("subject_name", subjectName);
    if (!filter.batch && filter.startDate) params.set("startDate", filter.startDate);
    if (!filter.batch && filter.endDate) params.set("endDate", filter.endDate);
    params.set("page", String(page));
    params.set("per_page", String(perPage));
    const response = await fetch(`/api/analysis/latest?${params.toString()}`);
    const result = await response.json();
    if (!response.ok || result.has_data === false) throw new Error(result.error || "Could not load this activity page.");
    sessionStorage.setItem("currentAnalysis", JSON.stringify(result));
    renderAnalysisResults(result);
}

async function changeActivityPage(offset) {
    const pagination = JSON.parse(sessionStorage.getItem("currentAnalysis") || "{}").activity_pagination || {};
    const requestedPage = Math.max(1, (Number(pagination.page) || 1) + offset);
    const size = Number(pagination.per_page) || 20;
    const button = document.getElementById(offset < 0 ? "prevActivityPage" : "nextActivityPage");
    if (button) button.disabled = true;
    try {
        await fetchActivityPage(requestedPage, size);
        document.getElementById("activityPagination")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (error) {
        console.error("Activity page load failed", error);
        const current = JSON.parse(sessionStorage.getItem("currentAnalysis") || "{}");
        renderActivityPagination(current.activity_pagination);
    }
}

async function changeActivityPageSize(value) {
    try { await fetchActivityPage(1, Number(value) || 20); }
    catch (error) {
        console.error("Activity page size update failed", error);
        const current = JSON.parse(sessionStorage.getItem("currentAnalysis") || "{}");
        renderActivityPagination(current.activity_pagination);
    }
}



function createActivityPostCard(act, defaultSubjectName, isHistorical = false) {

    const card = document.createElement("article");

    card.className = "post-card feed-post-card";



    const subjectName = act.target_profile_name || act.subject_name || defaultSubjectName || "Target Profile";

    const isOwnPost = act.is_own_post !== false;

    const postAuthor = act.post_author || (isOwnPost ? subjectName : "Original Post Author");

    const authorHeadline = act.post_author_headline || (isOwnPost ? (act.target_profile_headline || "LinkedIn Member") : "LinkedIn Member");

    const authorPhoto = act.post_author_photo || (isOwnPost ? (act.target_profile_photo || null) : null);

    

    // Standard DD/MM/YY date formatting (Part 4)

    const rawDate = act.activity_date || act.date || "";

    const formattedDate = formatDateDDMMYY(rawDate);

    let postDate = formattedDate;

    if (act.relative_date && act.relative_date.trim()) {

        const relClean = act.relative_date.trim();

        if (formattedDate && relClean !== formattedDate) {

            postDate = `${formattedDate} (${relClean})`;

        } else if (!formattedDate) {

            postDate = relClean;

        }

    }

    if (!postDate) postDate = "Recent";



    const postContent = (act.post_content && act.post_content.trim()) || (act.text && act.text.trim()) || "";

    const postUrl = act.original_post_url || act.post_url || act.url || "";

    const mediaUrl = act.media_url || "";

    const actionType = (act.action_type || act.type || "post").toLowerCase();



    // Video extraction & component detection (Part 1)

    const videoUrl = act.video_url || "";

    const videoThumbnail = act.video_thumbnail || (actionType === "video" ? mediaUrl : "");

    const videoStatus = act.video_status || (actionType === "video" ? "Video detected" : "");

    const isVideoPost = actionType === "video" || Boolean(videoUrl) || Boolean(videoThumbnail) || Boolean(act.is_video) || Boolean(act.video_status && act.video_status !== "No video");



    const defaultAvatar = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2394a3b8'%3E%3Cpath d='M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z'/%3E%3C/svg%3E";

    const avatarSrc = authorPhoto && !isDashboardMediaKnownBroken(authorPhoto) ? authorPhoto : defaultAvatar;



    // Context banner showing target user's interaction

    let contextBannerHTML = "";

    let legacyBadgeClass = "posted-by-profile";

    let legacyBadgeText = "POSTED BY PROFILE";



    const hasOriginalAuthor = Boolean(act.post_author && act.post_author !== subjectName && act.post_author !== "Original Post Author" && act.post_author !== "LinkedIn Author");

    const targetMediaType = isVideoPost ? "video" : "post";
    const targetAuthorText = hasOriginalAuthor ? `<strong>${escapeHTML(act.post_author)}\'s</strong> ${targetMediaType}` : `this ${targetMediaType}`;



    if (actionType === "comment") {

        legacyBadgeClass = "original-post";

        legacyBadgeText = "COMMENT BY PROFILE";

        contextBannerHTML = `

            <div class="feed-context-bar">

                <span class="post-badge original-post" style="display:none;">${legacyBadgeText}</span>

                <span>💬 <strong>${escapeHTML(subjectName)}</strong> commented on ${targetAuthorText}</span>

            </div>

        `;

    } else if (actionType === "like" || actionType === "reaction") {

        legacyBadgeClass = "original-post";

        legacyBadgeText = "REACTION BY PROFILE";

        const rx = act.reaction_type || "Like";

        contextBannerHTML = `

            <div class="feed-context-bar">

                <span class="post-badge original-post" style="display:none;">${legacyBadgeText}</span>

                <span>❤️ <strong>${escapeHTML(subjectName)}</strong> reacted (${escapeHTML(rx)}) to ${targetAuthorText}</span>

            </div>

        `;

    } else if (actionType === "share") {

        legacyBadgeClass = "original-post";

        legacyBadgeText = "SHARED BY PROFILE";

        const shareTarget = hasOriginalAuthor ? `<strong>${escapeHTML(act.post_author)}\'s</strong> ${targetMediaType}` : (isVideoPost ? "this video" : "this");

        contextBannerHTML = `

            <div class="feed-context-bar">

                <span class="post-badge original-post" style="display:none;">${legacyBadgeText}</span>

                <span>🔄 <strong>${escapeHTML(subjectName)}</strong> reposted ${shareTarget}</span>

            </div>

        `;

    } else if (actionType === "video" || isVideoPost) {
        legacyBadgeClass = "posted-by-profile";
        legacyBadgeText = "POSTED BY PROFILE";
        contextBannerHTML = `
            <div class="feed-context-bar">
                <span class="post-badge posted-by-profile" style="display:none;">${legacyBadgeText}</span>
                <span>🎥 <strong>${escapeHTML(subjectName)}</strong> posted a video</span>
            </div>
        `;
    } else {

        contextBannerHTML = `

            <div class="feed-context-bar">

                <span class="post-badge posted-by-profile" style="display:none;">${legacyBadgeText}</span>

                <span>✍️ <strong>${escapeHTML(subjectName)}</strong> posted this</span>

            </div>

        `;

    }



    // Media HTML

    let mediaHTML = "";

    if (mediaUrl && !isDashboardMediaKnownBroken(mediaUrl)) {

        if (mediaUrl.match(/\.(jpeg|jpg|gif|png|webp)/i) || mediaUrl.includes("images.unsplash") || mediaUrl.includes("licdn.com") || mediaUrl.includes("feedshare")) {

            mediaHTML = `

                <div class="feed-post-media post-media-box">

                    <img src="${escapeAttr(mediaUrl)}" alt="Post media" onerror="markDashboardImageFailed(this);">

                </div>

            `;

        } else {

            mediaHTML = `

                <div style="padding: 0 18px 12px 18px;">

                    <a href="${escapeAttr(mediaUrl)}" target="_blank" rel="noopener noreferrer" style="color:#0a66c2; font-size:13px; font-weight:600;">

                        📎 View Attached Media ↗

                    </a>

                </div>

            `;

        }

    }



    // Format Sentiment & Tone for Post

    const sentiment = act.sentiment || "Not available";

    let toneDisplay = act.tone || "Not available";

    if (act.secondary_tone && act.tone && act.tone !== "Not available" && act.tone !== "Analysis unavailable") {

        toneDisplay = `${act.tone} / ${act.secondary_tone}`;

    }



    const sentimentClass = sentiment.toLowerCase().replace(/[^a-z0-9]+/g, "-");



    // Format Sentiment & Tone for Comment (if applicable)

    let commentAiHTML = "";

    if (actionType === "comment" && act.comment_text) {

        const cSentiment = act.comment_sentiment || "Not available";

        let cTone = act.comment_tone || "Not available";

        if (act.comment_secondary_tone && act.comment_tone && act.comment_tone !== "Not available" && act.comment_tone !== "Analysis unavailable") {

            cTone = `${act.comment_tone} / ${act.comment_secondary_tone}`;

        }

        const cSentimentClass = cSentiment.toLowerCase().replace(/[^a-z0-9]+/g, "-");

        commentAiHTML = `

            <div class="ai-comment-analysis">

                <div class="ai-comment-analysis-title">Analysis of the comment:</div>

                <div class="ai-analysis-metrics">

                    <div class="ai-metric-line">

                        <strong>Sentiment:</strong> <span class="ai-sentiment-badge sentiment-${cSentimentClass}">${escapeHTML(cSentiment)}</span>

                    </div>

                    <div class="ai-metric-line">

                        <strong>Tone:</strong> <span class="ai-tone-badge">${escapeHTML(cTone)}</span>

                    </div>

                </div>

            </div>

        `;

    }



    // Comment bubble (if user commented)

    let commentBubbleHTML = "";

    if (actionType === "comment" && act.comment_text) {

        commentBubbleHTML = `

            <div class="feed-user-comment">

                <div class="feed-user-comment-title">💬 ${escapeHTML(subjectName)}'s Comment:</div>

                <div class="feed-user-comment-text">"${escapeHTML(act.comment_text)}"</div>

                ${commentAiHTML}

            </div>

        `;

    }



    // Post content text

    let contentHTML = "";

    if (postContent && postContent !== "Original post content unavailable" && postContent !== "Actual post content could not be retrieved from LinkedIn.") {

        contentHTML = `<div class="feed-post-text post-content-box">${escapeHTML(postContent)}</div>`;

    } else {

        contentHTML = `

            <div class="feed-post-text post-content-box">

                <span class="content-unavailable-note">Original post content unavailable</span>

            </div>

        `;

    }



    // AI Analysis section for post

    let aiAnalysisHTML = `

        <div class="ai-analysis-box">

            <div class="ai-analysis-title">AI Analysis</div>

            <div class="ai-analysis-metrics">

                <div class="ai-metric-line">

                    <strong>Sentiment:</strong> <span class="ai-sentiment-badge sentiment-${sentimentClass}">${escapeHTML(sentiment)}</span>

                </div>

                <div class="ai-metric-line">

                    <strong>Tone:</strong> <span class="ai-tone-badge">${escapeHTML(toneDisplay)}</span>

                </div>

            </div>

        </div>

    `;



    // Video Component Rendering (Part 1)

    let videoHTML = "";

    if (isVideoPost) {

        let videoInner = "";

        const isDirectStream = videoUrl && (videoUrl.match(/\.(mp4|webm|ogg|m4v)(\?.*)?$/i) || videoUrl.includes("videocdn"));

        const isEmbed = videoUrl && (videoUrl.includes("youtube.com") || videoUrl.includes("youtu.be") || videoUrl.includes("vimeo.com"));



        if (isDirectStream) {

            videoInner = `

                <div class="feed-post-video-player">

                    <video controls preload="metadata" poster="${escapeAttr(videoThumbnail || '')}">

                        <source src="${escapeAttr(videoUrl)}" type="video/mp4">

                        Your browser does not support the video tag.

                    </video>

                </div>

            `;

        } else if (isEmbed) {

            let embedUrl = videoUrl;

            if (videoUrl.includes("watch?v=")) {

                embedUrl = videoUrl.replace("watch?v=", "embed/");

            } else if (videoUrl.includes("youtu.be/")) {

                embedUrl = videoUrl.replace("youtu.be/", "www.youtube.com/embed/");

            }

            videoInner = `

                <div class="feed-post-video-embed" style="position:relative; padding-bottom:56.25%; height:0; overflow:hidden;">

                    <iframe src="${escapeAttr(embedUrl)}" frameborder="0" allowfullscreen style="position:absolute; top:0; left:0; width:100%; height:100%;"></iframe>

                </div>

            `;

        } else if (videoThumbnail && !isDashboardMediaKnownBroken(videoThumbnail)) {

            const watchLink = postUrl || videoUrl || "#";

            videoInner = `

                <div class="feed-post-video-poster" onclick="${watchLink !== '#' ? `window.open('${escapeAttr(watchLink)}', '_blank', 'noopener,noreferrer')` : ''}">

                    <img src="${escapeAttr(videoThumbnail)}" alt="Video thumbnail" onerror="markDashboardImageFailed(this);">

                    <div class="video-play-overlay">

                        <div class="video-play-button" title="Watch Video on LinkedIn">

                            <svg viewBox="0 0 24 24" width="30" height="30" fill="#ffffff" style="margin-left:3px;"><polygon points="6,3 20,12 6,21"></polygon></svg>

                        </div>

                    </div>

                </div>

            `;

        } else {

            videoInner = `

                <div class="feed-post-video-placeholder">

                    <div class="video-placeholder-icon">🎬</div>

                    <div class="video-placeholder-title">LinkedIn Video Post</div>

                    <div class="video-placeholder-desc">Direct playback stream is protected by LinkedIn. Click the "Open on LinkedIn" button below to watch this video.</div>

                </div>

            `;

        }



        const statusBadge = videoStatus ? `<span class="video-status-badge">${escapeHTML(videoStatus)}</span>` : "";



        videoHTML = `

            <div class="feed-post-video-container post-media-box">

                ${videoInner}

                <div class="feed-video-footer">

                    <span class="video-type-pill">▶ VIDEO</span>

                    ${statusBadge}

                </div>

            </div>

        `;

    }



    // Open on LinkedIn Button (Part 3)

    let openBtnHTML = "";

    const isProfileUrl = postUrl && postUrl.includes("linkedin.com/in/") && !postUrl.includes("/recent-activity/") && !postUrl.includes("/feed/update/") && !postUrl.includes("/posts/") && !postUrl.includes("activity-urn");

    if (postUrl && postUrl.startsWith("http") && !isProfileUrl) {

        openBtnHTML = `

            <a href="${escapeAttr(postUrl)}" target="_blank" rel="noopener noreferrer" class="btn-open-linkedin view-post-link" aria-label="Open post on LinkedIn">

                <span>Open on LinkedIn</span>

                <svg class="open-linkedin-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">

                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>

                    <polyline points="15 3 21 3 21 9"></polyline>

                    <line x1="10" y1="14" x2="21" y2="3"></line>

                </svg>

            </a>

        `;

    } else {

        openBtnHTML = `

            <span class="btn-open-linkedin disabled view-post-link" title="Link not available" aria-disabled="true">

                <span>Open on LinkedIn</span>

                <span class="btn-disabled-note">(Unavailable)</span>

            </span>

        `;

    }



    // Engagement count strip

    const likesVal = act.likes || 0;

    const commentsVal = act.comments || 0;

    const sharesVal = act.shares || 0;

    let countsHTML = `

        <div class="feed-engagement-counts post-stats-strip">

            <span>${likesVal > 0 ? `👍 ${likesVal} reaction${likesVal === 1 ? '' : 's'}` : '0 reactions'}</span>

            <div style="display:flex; gap:12px;">

                <span>${commentsVal} comment${commentsVal === 1 ? '' : 's'}</span>

                <span>${sharesVal} repost${sharesVal === 1 ? '' : 's'}</span>

            </div>

        </div>

    `;



    card.innerHTML = `

        ${contextBannerHTML}



        <!-- Post Header -->

        <div class="feed-post-header post-card-header">

            <img class="feed-author-avatar" src="${escapeAttr(avatarSrc)}" alt="${escapeAttr(postAuthor)}" onerror="markDashboardImageFailed(this, '${defaultAvatar}');">

            <div class="feed-author-meta">

                <div class="feed-author-name post-author">${escapeHTML(postAuthor)}</div>

                <div class="feed-author-headline">${escapeHTML(authorHeadline)}</div>

                <div class="feed-post-date post-date">${escapeHTML(postDate)} • 🌐</div>

            </div>

            ${(() => {
                const origin = act.storage_origin;
                if (origin === "live_retrieval") {
                    return '<span class="live-retrieval-badge" style="margin-left:auto; background:#ecfdf5; color:#065f46; border:1px solid #a7f3d0; border-radius:4px; font-size:11px; font-weight:700; padding:2px 8px; align-self:flex-start;" title="Retrieved in live session scan">⚡ LIVE RETRIEVED</span>';
                } else if (isHistorical || origin === "historical_mongodb" || act.is_historical) {
                    return '<span class="historical-badge" style="margin-left:auto; background:#f1f5f9; color:#475569; border:1px solid #cbd5e1; border-radius:4px; font-size:11px; font-weight:700; padding:2px 8px; align-self:flex-start;" title="Preserved from previous MongoDB scan">🕒 HISTORICAL (MongoDB)</span>';
                }
                return '';
            })()}

        </div>



        <!-- Attached Comment (if applicable) -->

        ${commentBubbleHTML}



        <!-- Actual Post Text Content -->

        ${contentHTML}



        <!-- Post Image/Media or Video (if available) -->

        ${isVideoPost ? videoHTML : mediaHTML}



        <!-- AI Analysis Section -->

        ${aiAnalysisHTML}



        <!-- Engagement Bar -->

        ${countsHTML}



        <!-- Social Action Buttons + Open on LinkedIn Button -->

        <div class="feed-post-actions-bar post-card-footer">

            <div class="feed-social-buttons">

                <button type="button" class="social-btn">👍 Like</button>

                <button type="button" class="social-btn">💬 Comment</button>

                <button type="button" class="social-btn">🔄 Repost</button>

                <button type="button" class="social-btn">✈️ Send</button>

            </div>

            ${openBtnHTML}

        </div>

    `;



    return card;

}



function renderEmptyState() {

    const nameEl = document.getElementById("subjectName");

    if (nameEl && (!nameEl.textContent || nameEl.textContent === "Selected Profile")) {

        try {

            const storedProfile = JSON.parse(sessionStorage.getItem("selectedProfile") || "{}");

            const pName = storedProfile.name || localStorage.getItem("selectedFriend");

            if (pName) nameEl.textContent = pName;

        } catch (e) {}

    }

    const listEl = document.getElementById("activitiesTimeline");

    if (listEl) {

        listEl.innerHTML = `

            <div class="empty-state">

                <h3>No activity data available for this profile and date range.</h3>

                <p>Please enter a LinkedIn profile URL and choose your filter criteria to run an analysis.</p>

                <a href="/" class="action-btn">Enter Profile URL →</a>

            </div>

        `;

    }

}



async function handleOfflineUpload(event) {

    event.preventDefault();

    const fileInput = document.getElementById("offlineActivityFile");

    const statusEl = document.getElementById("offlineStatusMsg");

    const btn = document.getElementById("offlineUploadBtn");



    if (!fileInput || !fileInput.files[0]) {

        alert("Please select a CSV or ZIP export file.");

        return;

    }



    const formData = new FormData();

    formData.append("file", fileInput.files[0]);

    formData.append("page", "1");

    formData.append("per_page", "20");



    // Pull current filter criteria

    let currentFilter = {};

    try {

        currentFilter = JSON.parse(localStorage.getItem("activityFilter") || "{}");

    } catch (e) {

        currentFilter = {};

    }



    if (currentFilter.startDate) formData.append("startDate", currentFilter.startDate);

    if (currentFilter.endDate) formData.append("endDate", currentFilter.endDate);

    if (currentFilter.subject_name) formData.append("subject_name", currentFilter.subject_name);

    if (currentFilter.subject_url) formData.append("subject_url", currentFilter.subject_url);



    if (btn) {

        btn.disabled = true;

        btn.textContent = "Uploading...";

    }



    if (statusEl) {

        statusEl.style.display = "block";

        statusEl.style.color = "#0a66c2";

        statusEl.textContent = "Processing offline activity archive...";

    }



    try {

        const response = await fetch("/api/analyze", {

            method: "POST",

            body: formData

        });



        const result = await response.json();

        if (!response.ok) {

            throw new Error(result.error || "Failed to process offline file.");

        }



        sessionStorage.setItem("currentAnalysis", JSON.stringify(result));
        sessionStorage.setItem("selectedProfile", JSON.stringify({
            name: result.subject_name || "Imported Activity Data",
            profile_url: result.subject_url || "",
            headline: result.headline || "Imported LinkedIn Activity",
            source: "imported"
        }));
        localStorage.setItem("activityFilter", JSON.stringify({
            startDate: result.requested_date_range?.start || "",
            endDate: result.requested_date_range?.end || ""
        }));

        if (statusEl) {

            statusEl.style.color = "#15803d";

            statusEl.textContent = `Loaded ${result.total_activities} activities!`;

        }



        renderAnalysisResults(result);

        fetchPipelineStatus(result.run_id || null);

    } catch (err) {

        if (statusEl) {

            statusEl.style.color = "#b91c1c";

            statusEl.textContent = err.message;

        }

    } finally {

        if (btn) {

            btn.disabled = false;

            btn.textContent = "Upload Archive";

        }

    }

}



function setElementText(id, text) {

    const el = document.getElementById(id);

    if (el) el.textContent = text;

}



function getBadgeClass(source) {

    if (source === "real_linkedin") return "badge-real";

    if (source === "imported") return "badge-imported";

    return "badge-sample";

}



function getBadgeLabel(source) {

    if (source === "real_linkedin") return "Real LinkedIn Profile";

    if (source === "imported") return "Imported from LinkedIn Connections export";

    return "Demo / Sample Profile";

}



function formatActivityName(type) {

    const map = {

        "post": "Posts",

        "posts": "Posts",

        "comment": "Comments",

        "comments": "Comments",

        "like": "Reactions / Likes",

        "likes": "Reactions / Likes",

        "share": "Shares",

        "shares": "Shares",

        "video": "Videos & Media",

        "videos": "Videos & Media"

    };

    return map[type.toLowerCase()] || capitalize(type);

}



function capitalize(str) {

    if (!str) return "";

    return str.charAt(0).toUpperCase() + str.slice(1);

}



function escapeHTML(str) {

    return (str || "")

        .replace(/&/g, "&amp;")

        .replace(/</g, "&lt;")

        .replace(/>/g, "&gt;");

}



function escapeAttr(str) {

    if (!str) return "";

    return String(str)

        .replace(/&/g, "&amp;")

        .replace(/"/g, "&quot;")

        .replace(/'/g, "&#39;")

        .replace(/</g, "&lt;")

        .replace(/>/g, "&gt;");

}
/* YouTube-Style Live Top Progress Bar Controller */
let _ytPollInterval = null;

function startYouTubeProgressBar(initialText = "Connecting to Chrome (5%)...", targetRunId = null) {
    const bar = document.getElementById("ytAnalysisProgressBar");
    const chip = document.getElementById("ytAnalysisProgressChip");
    const textEl = document.getElementById("ytChipText");
    const profEl = document.getElementById("ytChipProfile");

    if (bar) {
        bar.className = "yt-progress-bar";
        bar.style.width = "4%";
        bar.style.opacity = "1";
        bar.style.display = "block";
    }
    if (chip) {
        chip.style.display = "flex";
        chip.style.opacity = "1";
    }
    if (textEl) {
        textEl.textContent = initialText;
    }
    if (profEl) {
        profEl.style.display = "none";
    }

    if (_ytPollInterval) clearInterval(_ytPollInterval);

    _ytPollInterval = setInterval(async () => {
        try {
            const endpoint = targetRunId ? `/api/pipeline/${encodeURIComponent(targetRunId)}` : "/api/pipeline/latest";
            const res = await fetch(endpoint);
            if (!res.ok) return;
            const data = await res.json();
            if (!data || data.status === "no_runs") return;

            const pct = Math.min(95, Math.max(4, Number(data.progress_percentage) || 4));
            const stName = data.current_stage_name || "Analyzing Profile";
            const isIndeterminate = Boolean(data.is_indeterminate);

            if (bar) {
                bar.style.width = `${pct}%`;
                if (isIndeterminate) {
                    bar.classList.add("indeterminate");
                } else {
                    bar.classList.remove("indeterminate");
                }
            }

            if (textEl) {
                textEl.textContent = `${stName} (${pct}%)`;
            }

            if (profEl && data.total_profiles && data.total_profiles > 1) {
                profEl.style.display = "inline-block";
                const pIdx = data.current_profile_index || 1;
                const pTot = data.total_profiles;
                const pName = data.current_profile_name ? `: ${data.current_profile_name}` : "";
                profEl.textContent = `Profile ${pIdx} of ${pTot}${pName}`;
            }

            if (data.status === "completed" || data.progress_percentage >= 100) {
                completeYouTubeProgressBar();
            } else if (data.status === "failed") {
                errorYouTubeProgressBar("Analysis failed");
            }
        } catch (e) {
            // Non-blocking poll exception
        }
    }, 300);
}

function completeYouTubeProgressBar() {
    if (_ytPollInterval) {
        clearInterval(_ytPollInterval);
        _ytPollInterval = null;
    }
    const bar = document.getElementById("ytAnalysisProgressBar");
    const chip = document.getElementById("ytAnalysisProgressChip");
    const textEl = document.getElementById("ytChipText");

    if (bar) {
        bar.classList.remove("indeterminate");
        bar.style.width = "100%";
    }
    if (textEl) {
        textEl.textContent = "Preparing Dashboard (100%)";
    }
    setTimeout(() => {
        if (chip) {
            chip.style.opacity = "0";
            setTimeout(() => { chip.style.display = "none"; }, 300);
        }
        if (bar) {
            bar.style.opacity = "0";
            setTimeout(() => {
                bar.style.display = "none";
                bar.style.opacity = "1";
                bar.style.width = "0%";
            }, 300);
        }
    }, 700);
}

function errorYouTubeProgressBar(msg = "Analysis failed") {
    if (_ytPollInterval) {
        clearInterval(_ytPollInterval);
        _ytPollInterval = null;
    }
    const bar = document.getElementById("ytAnalysisProgressBar");
    const chip = document.getElementById("ytAnalysisProgressChip");
    const textEl = document.getElementById("ytChipText");

    if (bar) {
        bar.classList.remove("indeterminate");
        bar.classList.add("error-bar");
    }
    if (textEl) {
        textEl.textContent = `⚠️ ${msg}`;
    }
    setTimeout(() => {
        if (chip) {
            chip.style.opacity = "0";
            setTimeout(() => { chip.style.display = "none"; }, 300);
        }
        if (bar) {
            bar.style.opacity = "0";
            setTimeout(() => {
                bar.style.display = "none";
                bar.style.opacity = "1";
                bar.classList.remove("error-bar");
                bar.style.width = "0%";
            }, 300);
        }
    }, 4000);
}
