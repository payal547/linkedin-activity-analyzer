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

    if (dateInput instanceof Date) {
        if (isNaN(dateInput.getTime())) return "";
        const dd = String(dateInput.getDate()).padStart(2, "0");
        const mm = String(dateInput.getMonth() + 1).padStart(2, "0");
        const yy = String(dateInput.getFullYear()).slice(-2);
        return `${dd}/${mm}/${yy}`;
    }

    if (typeof dateInput === "number") {
        const d = new Date(dateInput);
        if (!isNaN(d.getTime())) {
            const dd = String(d.getDate()).padStart(2, "0");
            const mm = String(d.getMonth() + 1).padStart(2, "0");
            const yy = String(d.getFullYear()).slice(-2);
            return `${dd}/${mm}/${yy}`;
        }
    }

    const s = String(dateInput).trim();
    if (!s) return "";

    if (/^\d{2}\/\d{2}\/\d{2}$/.test(s)) {
        return s;
    }

    const mDmy4 = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if (mDmy4) {
        const dd = mDmy4[1].padStart(2, "0");
        const mm = mDmy4[2].padStart(2, "0");
        const yy = mDmy4[3].slice(-2);
        return `${dd}/${mm}/${yy}`;
    }

    const mIso = s.match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/);
    if (mIso) {
        const yy = mIso[1].slice(-2);
        const mm = mIso[2].padStart(2, "0");
        const dd = mIso[3].padStart(2, "0");
        return `${dd}/${mm}/${yy}`;
    }

    const parsed = new Date(s);
    if (!isNaN(parsed.getTime())) {
        const dd = String(parsed.getDate()).padStart(2, "0");
        const mm = String(parsed.getMonth() + 1).padStart(2, "0");
        const yy = String(parsed.getFullYear()).slice(-2);
        return `${dd}/${mm}/${yy}`;
    }

    return s;
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

document.addEventListener("DOMContentLoaded", () => {
    initDashboard();
});

async function initDashboard() {
    // 0. Synchronously populate preliminary profile header while fetching analysis data
    try {
        const storedProfile = JSON.parse(sessionStorage.getItem("selectedProfile") || "{}");
        const preliminaryName = storedProfile.name || localStorage.getItem("selectedFriend");
        if (preliminaryName) {
            const nameEl = document.getElementById("subjectName");
            if (nameEl) nameEl.textContent = preliminaryName;
        }
        const preliminaryHeadline = storedProfile.headline || localStorage.getItem("selectedHeadline");
        if (preliminaryHeadline) {
            const headlineEl = document.getElementById("subjectHeadline");
            if (headlineEl) headlineEl.textContent = preliminaryHeadline;
        }
        const preliminaryUrl = storedProfile.profile_url || localStorage.getItem("selectedProfileUrl");
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
        targetSubjectId = (storedProfile.subject_id || storedProfile.id || localStorage.getItem("selectedSubjectId") || "").trim();
        targetProfileUrl = (storedProfile.profile_url || localStorage.getItem("selectedProfileUrl") || "").trim();
        targetSubjectName = (storedProfile.name || localStorage.getItem("selectedFriend") || "").trim();
    } catch (e) {}

    // 1. Prioritize querying backend for latest authoritative analysis for this specific profile
    try {
        let apiUrl = "/api/analysis/latest";
        const qp = [];
        if (targetSubjectId) qp.push(`subject_id=${encodeURIComponent(targetSubjectId)}`);
        if (targetProfileUrl) qp.push(`profile_url=${encodeURIComponent(targetProfileUrl)}`);
        if (targetSubjectName) qp.push(`subject_name=${encodeURIComponent(targetSubjectName)}`);
        if (qp.length > 0) apiUrl += `?${qp.join("&")}`;

        const res = await fetch(apiUrl);
        if (res.ok) {
            const serverData = await res.json();
            if (serverData && serverData.has_data !== false) {
                analysisData = serverData;
            }
        }
    } catch (e) {
        console.warn("Could not fetch latest analysis from server:", e);
    }

    // 2. Fallback: check sessionStorage if server had no data, strictly verifying identity
    if (!analysisData) {
        const stored = sessionStorage.getItem("currentAnalysis");
        if (stored) {
            try {
                const parsedStored = JSON.parse(stored);
                if (parsedStored) {
                    analysisData = parsedStored;
                }
            } catch (e) {
                console.error("Failed to parse stored analysis", e);
            }
        }
    }

    // 3. Fallback: if user arrived with filter criteria in localStorage, trigger analysis now
    if (!analysisData) {
        const filterStr = localStorage.getItem("activityFilter");
        if (filterStr) {
            try {
                const payload = JSON.parse(filterStr);
                const res = await fetch("/api/analyze", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    analysisData = await res.json();
                }
            } catch (e) {
                console.error("Auto-analysis failed", e);
            }
        }
    }

    // Strict identity verification: never display data belonging to a different profile
    if (analysisData && (targetProfileUrl || targetSubjectId || targetSubjectName)) {
        const dUrl = (analysisData.subject_url || "").toLowerCase();
        const dId = (analysisData.subject_id || "").toLowerCase();
        const dName = (analysisData.subject_name || "").toLowerCase();

        const tUrl = targetProfileUrl.toLowerCase();
        const tId = targetSubjectId.toLowerCase();
        const tName = targetSubjectName.toLowerCase();

        let isMatch = false;
        if (tUrl && dUrl && (dUrl.includes(tUrl) || tUrl.includes(dUrl))) isMatch = true;
        if (tId && dId && tId === dId) isMatch = true;
        if (tName && dName && tName === dName) isMatch = true;
        if (!tUrl && !tId && !tName) isMatch = true;

        if (!isMatch) {
            console.warn("Analysis data mismatch: loaded data for", dName, "but selected profile is", tName);
            analysisData = null;
        }
    }

    if (analysisData) {
        renderAnalysisResults(analysisData);
    } else {
        renderEmptyState();
    }
}

function renderAnalysisResults(data) {
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
        totalActs = histData.total_activities || (histData.activities ? histData.activities.length : 0);
        postsCount = histData.posts_count !== undefined ? histData.posts_count : (histData.by_type?.post || histData.by_type?.posts || 0);
        commentsCount = histData.comments_count !== undefined ? histData.comments_count : (histData.by_type?.comment || histData.by_type?.comments || histData.comments || 0);
        likesCount = histData.likes_count !== undefined ? histData.likes_count : (histData.by_type?.like || histData.by_type?.likes || data.likes || 0);
        sharesCount = histData.shares_count !== undefined ? histData.shares_count : (histData.by_type?.share || histData.by_type?.shares || data.shares || 0);
        videosCount = histData.videos_count !== undefined ? histData.videos_count : (histData.by_type?.video || histData.by_type?.videos || 0);
        totalEng = histData.total_engagement || 0;
        avgEng = histData.average_engagement || (totalActs > 0 ? Math.round((totalEng / totalActs) * 100) / 100 : 0);
        byType = Object.assign({}, histData.by_type || {});

        // Compute dynamically if counts are 0 but activities exist
        if (totalActs > 0 && (postsCount + commentsCount + likesCount + sharesCount + videosCount === 0) && Array.isArray(histData.activities)) {
            byType = {};
            histData.activities.forEach(a => {
                const t = (a.action_type || a.type || "post").toLowerCase();
                byType[t] = (byType[t] || 0) + 1;
                if (t === "post") postsCount++;
                else if (t === "comment") commentsCount++;
                else if (t === "like" || t === "reaction") likesCount++;
                else if (t === "share" || t === "repost") sharesCount++;
                else if (t === "video") videosCount++;
            });
        }
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
        statsBanner.innerHTML = `<span>📊</span> <div><strong>Displaying Metrics from Preserved Historical Analysis</strong><br><span style="font-size:12.5px; opacity:0.9;">Current LinkedIn attempt was rate-limited or blocked (${escapeHTML(failStatus)}). The metrics and activity distribution above reflect the ${totalActs} preserved historical activities shown below.</span></div>`;
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
            const pAvatar = pRes.picture || defaultAvatar;

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
                        <img class="profile-section-avatar" src="${escapeAttr(pAvatar)}" alt="${escapeAttr(pName)}" onerror="this.src='${defaultAvatar}';">
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
                else if (pStatusType === "AUTH_REQUIRED") pIcon = "🔒";
                else if (pStatusType === "ACTIVITY_FEED_RESTRICTED") pIcon = "🛡️";
                else if (pStatusType === "PROFILE_NOT_FOUND") pIcon = "❓";
                else if (pStatusType === "NO_ACTIVITIES_IN_RANGE") pIcon = "📅";
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
            countEl.textContent = `${totalItems} item${totalItems === 1 ? '' : 's'}`;
        }

    } else {
        // Single Profile rendering
        const singleRes = profileResults[0] || data;
        const activities = singleRes.activities || [];
        const histData = data.historical_successful_analysis || singleRes.historical_successful_analysis;

        if (countEl) {
            if (activities.length > 0) {
                countEl.textContent = `${activities.length} item${activities.length === 1 ? '' : 's'}`;
            } else if (histData && histData.activities && histData.activities.length > 0) {
                countEl.textContent = `${histData.activities.length} historical item${histData.activities.length === 1 ? '' : 's'}`;
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

                // Render preserved historical activities feed
                histActs.forEach(act => {
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
    const isVideoPost = actionType === "video" || Boolean(videoUrl) || Boolean(act.video_status && act.video_status !== "No video");

    const defaultAvatar = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%2394a3b8'%3E%3Cpath d='M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z'/%3E%3C/svg%3E";
    const avatarSrc = authorPhoto || defaultAvatar;

    // Context banner showing target user's interaction
    let contextBannerHTML = "";
    let legacyBadgeClass = "posted-by-profile";
    let legacyBadgeText = "POSTED BY PROFILE";

    const hasOriginalAuthor = Boolean(act.post_author && act.post_author !== subjectName && act.post_author !== "Original Post Author" && act.post_author !== "LinkedIn Author");
    const targetAuthorText = hasOriginalAuthor ? `<strong>${escapeHTML(act.post_author)}'s</strong> post` : "this post";

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
        const shareTarget = hasOriginalAuthor ? `<strong>${escapeHTML(act.post_author)}'s</strong> post` : "this";
        contextBannerHTML = `
            <div class="feed-context-bar">
                <span class="post-badge original-post" style="display:none;">${legacyBadgeText}</span>
                <span>🔄 <strong>${escapeHTML(subjectName)}</strong> reposted ${shareTarget}</span>
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
    if (mediaUrl) {
        if (mediaUrl.match(/\.(jpeg|jpg|gif|png|webp)/i) || mediaUrl.includes("images.unsplash") || mediaUrl.includes("licdn.com") || mediaUrl.includes("feedshare")) {
            mediaHTML = `
                <div class="feed-post-media post-media-box">
                    <img src="${escapeAttr(mediaUrl)}" alt="Post media" onerror="this.parentElement.style.display='none';">
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
        } else if (videoThumbnail) {
            const watchLink = postUrl || videoUrl || "#";
            videoInner = `
                <div class="feed-post-video-poster" onclick="${watchLink !== '#' ? `window.open('${escapeAttr(watchLink)}', '_blank', 'noopener,noreferrer')` : ''}">
                    <img src="${escapeAttr(videoThumbnail)}" alt="Video thumbnail" onerror="this.parentElement.style.display='none';">
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
    if (postUrl && postUrl.startsWith("http")) {
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
            <img class="feed-author-avatar" src="${escapeAttr(avatarSrc)}" alt="${escapeAttr(postAuthor)}" onerror="this.src='${defaultAvatar}';">
            <div class="feed-author-meta">
                <div class="feed-author-name post-author">${escapeHTML(postAuthor)}</div>
                <div class="feed-author-headline">${escapeHTML(authorHeadline)}</div>
                <div class="feed-post-date post-date">${escapeHTML(postDate)} • 🌐</div>
            </div>
            ${isHistorical ? '<span class="historical-badge" style="margin-left:auto; background:#f1f5f9; color:#475569; border:1px solid #cbd5e1; border-radius:4px; font-size:11px; font-weight:700; padding:2px 8px; align-self:flex-start;">🕒 HISTORICAL RECORD</span>' : ''}
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


