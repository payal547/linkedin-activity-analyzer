/**
 * Dashboard Page Controller
 * Renders real calculated LinkedIn Activity analysis data and actual post cards.
 * Displays each activity grouped with its actual original post content.
 * Strictly adheres to real data: no fake activities, fake posts, fake comments, or fake likes.
 */

document.addEventListener("DOMContentLoaded", () => {
    initDashboard();
});

async function initDashboard() {
    let analysisData = null;

    // 1. Check sessionStorage for freshly computed analysis
    const stored = sessionStorage.getItem("currentAnalysis");
    if (stored) {
        try {
            analysisData = JSON.parse(stored);
        } catch (e) {
            console.error("Failed to parse stored analysis", e);
        }
    }

    // 2. Fallback: query backend for latest analysis in session
    if (!analysisData) {
        try {
            const res = await fetch("/api/analysis/latest");
            if (res.ok) {
                analysisData = await res.json();
            }
        } catch (e) {
            console.warn("No latest analysis on server:", e);
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

    // 2. Date Range
    const dateRange = data.date_range || {};
    const dateRangeEl = document.getElementById("dateRangeDisplay");
    if (dateRangeEl) {
        if (dateRange.start && dateRange.end) {
            dateRangeEl.textContent = dateRange.start === dateRange.end
                ? `Date: ${dateRange.start}`
                : `Period: ${dateRange.start} → ${dateRange.end}`;
        } else if (dateRange.start) {
            dateRangeEl.textContent = `Since ${dateRange.start}`;
        } else {
            dateRangeEl.textContent = "All Dates";
        }
    }

    // 3. Calculated Statistics (Strictly calculated, no hardcoded values)
    const totalActs = data.total_activities || 0;
    const postsCount = data.posts_count !== undefined ? data.posts_count : (data.by_type?.post || data.by_type?.posts || 0);
    const commentsCount = data.comments_count !== undefined ? data.comments_count : (data.by_type?.comment || data.by_type?.comments || data.comments || 0);
    const likesCount = data.likes_count !== undefined ? data.likes_count : (data.by_type?.like || data.by_type?.likes || data.likes || 0);
    const sharesCount = data.shares_count !== undefined ? data.shares_count : (data.by_type?.share || data.by_type?.shares || data.shares || 0);
    const videosCount = data.videos_count !== undefined ? data.videos_count : (data.by_type?.video || data.by_type?.videos || 0);

    setElementText("totalActivities", totalActs);
    setElementText("postsCount", postsCount);
    setElementText("commentsCount", commentsCount);
    setElementText("likesCount", likesCount);
    setElementText("sharesCount", sharesCount);
    setElementText("videosCount", videosCount);

    // Legacy elements for test compatibility
    setElementText("totalEngagement", data.total_engagement || 0);
    setElementText("avgEngagement", data.average_engagement || 0);
    setElementText("postCount", likesCount);
    setElementText("commentCount", commentsCount);
    setElementText("shareCount", sharesCount);

    // 4. Breakdown by Activity Type
    const breakdownEl = document.getElementById("typeBreakdown");
    if (breakdownEl) {
        breakdownEl.innerHTML = "";
        const byType = data.by_type || {};

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

    // 5. Render Actual Post Cards Feed
    const listEl = document.getElementById("activitiesTimeline");
    const countEl = document.getElementById("timelineItemCount");
    const activities = data.activities || [];

    if (countEl) {
        countEl.textContent = `${activities.length} item${activities.length === 1 ? '' : 's'}`;
    }

    if (!listEl) return;

    if (activities.length === 0) {
        const emptyTitle = data.empty_message || "Actual post content could not be retrieved from LinkedIn.";
        const statusNote = data.retrieval_status ? `<p style="margin-top:10px; color:#475569; font-size:14px;"><strong>Details:</strong> ${escapeHTML(data.retrieval_status)}</p>` : "";
        listEl.innerHTML = `
            <div class="empty-state">
                <div style="font-size:36px; margin-bottom:12px;">🔒</div>
                <h3>${escapeHTML(emptyTitle)}</h3>
                <p>
                    No activity data available for <strong>${escapeHTML(subjectName)}</strong> in the selected date range.
                </p>
                ${statusNote}
                <div style="display:flex; gap:12px; justify-content:center; margin-top:20px; flex-wrap:wrap;">
                    <a href="/activity-filter" class="action-btn">Modify Filters</a>
                    <a href="/connections" class="action-btn" style="background:#475569;">Import Offline LinkedIn Export (CSV/ZIP)</a>
                </div>
            </div>
        `;
        return;
    }

    listEl.innerHTML = "";

    activities.forEach(act => {
        const card = document.createElement("article");
        card.className = "post-card";

        const isOwnPost = act.is_own_post !== false;
        const postAuthor = act.post_author || (isOwnPost ? subjectName : "Original Post Author");
        const postContent = act.post_content || act.text || "Actual post content could not be retrieved from LinkedIn.";
        const postUrl = act.post_url || "";
        const mediaUrl = act.media_url || "";
        const actionType = (act.action_type || act.type || "post").toLowerCase();

        // 1. Media preview (if available)
        let mediaHTML = "";
        if (mediaUrl) {
            if (mediaUrl.match(/\.(jpeg|jpg|gif|png|webp)/i) || mediaUrl.includes("images.unsplash") || mediaUrl.includes("licdn.com")) {
                mediaHTML = `<div class="post-media-box"><img src="${escapeHTML(mediaUrl)}" alt="Post media" onerror="this.parentElement.style.display='none';"></div>`;
            } else {
                mediaHTML = `<div style="margin-bottom:12px;"><a href="${escapeHTML(mediaUrl)}" target="_blank" rel="noopener noreferrer" style="color:#0a66c2; font-size:13px; font-weight:600;">📎 Attached Media Link ↗</a></div>`;
            }
        }

        // 2. View Original Post Link
        const viewPostLinkHTML = postUrl
            ? `<a href="${escapeHTML(postUrl)}" target="_blank" rel="noopener noreferrer" class="view-post-link">[ View Original LinkedIn Post ↗ ]</a>`
            : `<span style="color:#94a3b8; font-size:12px;">[ View Original LinkedIn Post (URL unavailable) ]</span>`;

        // 3. Post metrics
        const statsStripHTML = `
            <div class="post-stats-strip">
                <span>Likes: <strong>${act.likes || 0}</strong></span>
                <span>Comments: <strong>${act.comments || 0}</strong></span>
                <span>Shares: <strong>${act.shares || 0}</strong></span>
            </div>
        `;

        // 4. Construct Card based on exact Activity Category
        if (actionType === "comment") {
            const commentText = act.comment_text || act.text || "";
            card.innerHTML = `
                <div class="post-card-header">
                    <div class="card-header-left">
                        <span class="post-badge original-post">COMMENT BY PROFILE</span>
                        <div style="display:flex; flex-direction:column; gap:2px;">
                            <span class="post-author" style="font-size:16px;">${escapeHTML(subjectName)}</span>
                            <span style="font-size:13px; color:#64748b;">Original Post by: <strong>${escapeHTML(postAuthor)}</strong></span>
                        </div>
                    </div>
                    <span class="post-date">Date: ${escapeHTML(act.date)}</span>
                </div>

                <div class="post-body">
                    <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.5px;">Actual Original Post Content:</div>
                    <div class="post-content-box">${escapeHTML(postContent)}</div>
                    ${mediaHTML}

                    <div class="attached-activity-box">
                        <h4>Activity by ${escapeHTML(subjectName)}:</h4>
                        <div class="activity-action commented">
                            <span style="font-weight:700;">💬 Commented:</span>
                            <blockquote>"${escapeHTML(commentText)}"</blockquote>
                        </div>
                    </div>
                </div>

                <div class="post-card-footer">
                    ${viewPostLinkHTML}
                    ${statsStripHTML}
                </div>
            `;
        } else if (actionType === "like" || actionType === "reaction") {
            const rxName = act.reaction_type || "Like";
            card.innerHTML = `
                <div class="post-card-header">
                    <div class="card-header-left">
                        <span class="post-badge original-post">REACTION BY PROFILE</span>
                        <div style="display:flex; flex-direction:column; gap:2px;">
                            <span class="post-author" style="font-size:16px;">Original Post by: <strong>${escapeHTML(postAuthor)}</strong></span>
                        </div>
                    </div>
                    <span class="post-date">Date: ${escapeHTML(act.date)}</span>
                </div>

                <div class="post-body">
                    <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.5px;">Actual Original Post Content:</div>
                    <div class="post-content-box">${escapeHTML(postContent)}</div>
                    ${mediaHTML}

                    <div class="attached-activity-box">
                        <h4>Activity by ${escapeHTML(subjectName)}:</h4>
                        <div class="activity-action liked">
                            <span style="font-weight:700;">❤️ Reacted: ${escapeHTML(rxName)}</span>
                        </div>
                    </div>
                </div>

                <div class="post-card-footer">
                    ${viewPostLinkHTML}
                    ${statsStripHTML}
                </div>
            `;
        } else if (actionType === "share") {
            card.innerHTML = `
                <div class="post-card-header">
                    <div class="card-header-left">
                        <span class="post-badge original-post">SHARED BY PROFILE</span>
                        <div style="display:flex; flex-direction:column; gap:2px;">
                            <span class="post-author" style="font-size:16px;">${escapeHTML(subjectName)}</span>
                            <span style="font-size:13px; color:#64748b;">Original Post by: <strong>${escapeHTML(postAuthor)}</strong></span>
                        </div>
                    </div>
                    <span class="post-date">Date: ${escapeHTML(act.date)}</span>
                </div>

                <div class="post-body">
                    <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.5px;">Actual Original Post Content:</div>
                    <div class="post-content-box">${escapeHTML(postContent)}</div>
                    ${mediaHTML}

                    <div class="attached-activity-box">
                        <h4>Activity by ${escapeHTML(subjectName)}:</h4>
                        <div class="activity-action">
                            <span style="font-weight:700;">🔄 Shared</span>
                        </div>
                    </div>
                </div>

                <div class="post-card-footer">
                    ${viewPostLinkHTML}
                    ${statsStripHTML}
                </div>
            `;
        } else {
            // Own post or media/video post
            card.innerHTML = `
                <div class="post-card-header">
                    <div class="card-header-left">
                        <span class="post-badge posted-by-profile">POSTED BY PROFILE</span>
                        <span class="post-author" style="font-size:16px;">${escapeHTML(subjectName)}</span>
                    </div>
                    <span class="post-date">Date: ${escapeHTML(act.date)}</span>
                </div>

                <div class="post-body">
                    <div style="font-size:12px; font-weight:700; color:#64748b; text-transform:uppercase; margin-bottom:6px; letter-spacing:0.5px;">Actual LinkedIn Post Content:</div>
                    <div class="post-content-box">${escapeHTML(postContent)}</div>
                    ${mediaHTML}

                    <div class="attached-activity-box">
                        <h4>Activity by ${escapeHTML(subjectName)}:</h4>
                        <div class="activity-action">
                            <span style="font-weight:700;">✍️ Posted this</span>
                        </div>
                    </div>
                </div>

                <div class="post-card-footer">
                    ${viewPostLinkHTML}
                    ${statsStripHTML}
                </div>
            `;
        }

        listEl.appendChild(card);
    });
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
