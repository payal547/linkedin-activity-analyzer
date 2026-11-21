(() => {
  "use strict";
  const state = { overview: null, profiles: [], profilePage: 1, profileTotalPages: 1, profileSort: "name", profileOrder: "asc", contentPage: 1, contentTotalPages: 1, runPage: 1, runTotalPages: 1, metric: "activity_count", trends: [] };
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (value) => { try { const url = new URL(value, window.location.origin); return ["http:", "https:"].includes(url.protocol) ? url.href : ""; } catch (_) { return ""; } };
  const count = (value) => value === null || value === undefined ? "—" : Number(value).toLocaleString();
  const asQuery = (pairs) => new URLSearchParams(Object.entries(pairs).filter(([, value]) => value !== "" && value !== null && value !== undefined)).toString();

  async function api(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }

  function showError(error) {
    const notice = byId("dataNotice");
    notice.className = "notice notice-error";
    notice.textContent = `${error.message || "Could not load analytics."} The page will not substitute demo data.`;
  }

  async function init() {
    bindControls();
    try {
      const [overview, profiles, runs, profileOptions] = await Promise.all([
        api("/api/dashboard/overview"), api("/api/dashboard/profiles?per_page=100"),
        api("/api/dashboard/runs?per_page=20"), api("/api/dashboard/profile-options")
      ]);
      state.overview = overview;
      state.profiles = profileOptions.items || [];
      state.runPage = runs.page || 1; state.runTotalPages = runs.total_pages || 1;
      renderOverview(overview); renderProfiles(profiles); renderRuns(runs); fillProfileSelects(profileOptions.items || []);
      state.trends = overview.trends || []; renderChart();
      await loadContent();
      const notice = byId("dataNotice"); notice.textContent = "Showing records read directly from MongoDB."; notice.className = "notice";
    } catch (error) {
      showError(error);
      ["profileRows", "runRows", "contentGrid"].forEach((id) => { if (byId(id)) byId(id).innerHTML = `<tr><td colspan="7">${escapeHtml(error.message)}</td></tr>`; });
      byId("trendChart").innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
    }
  }

  function renderOverview(data) {
    byId("savedProfiles").textContent = count(data.saved_profile_count);
    byId("storedProfiles").textContent = count(data.profiles_with_stored_successful_history);
    byId("analysisRuns").textContent = count(data.analysis_run_count_since_tracking);
    byId("activityRecords").textContent = count(data.activity_record_count);
    byId("postRecords").textContent = count(data.post_count);
    byId("videoRecords").textContent = count(data.video_count);
    byId("runCoverage").textContent = data.analysis_runs_tracking_started_at ? `Recorded since ${formatDate(data.analysis_runs_tracking_started_at)}` : "No durable runs recorded yet";
    const range = data.observed_activity_date_range;
    byId("coverageRange").textContent = range ? `Observed activity dates: ${formatDate(range.start)} – ${formatDate(range.end)}` : "No dated activity records are currently available.";
    byId("coverageNote").textContent = data.coverage_note || "";
    const insights = data.insights || [];
    byId("insights").innerHTML = insights.length ? insights.map((item) => `<article class="insight"><strong>${escapeHtml(item.label)}</strong><p>${escapeHtml(item.detail)}</p></article>`).join("") : `<p class="empty">No stored records support an insight yet.</p>`;
  }

  function formatDate(value) {
    if (!value) return "Unknown";
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
  }

  async function loadProfiles() {
    const params = asQuery({ q: byId("profileSearch").value.trim(), sort: state.profileSort, order: state.profileOrder, page: state.profilePage, per_page: 20 });
    const data = await api(`/api/dashboard/profiles?${params}`);
    state.profileTotalPages = data.total_pages || 1;
    renderProfiles(data);
  }

  function renderProfiles(data) {
    const items = data.items || [];
    const fallback = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Ccircle cx='12' cy='8' r='4' fill='%2394a3b8'/%3E%3Cpath d='M3 22c0-5 4-8 9-8s9 3 9 8' fill='%2394a3b8'/%3E%3C/svg%3E";
    byId("profileRows").innerHTML = items.length ? items.map((profile) => {
      const url = safeUrl(profile.profile_url);
      const dashboard = `/dashboard?${asQuery({ subject_id: profile.id, profile_url: profile.profile_url, subject_name: profile.name })}`;
      const photo = safeUrl(profile.picture) || fallback;
      const historyLabel = profile.has_stored_successful_history ? `Updated ${formatDate(profile.stored_history_updated_at)}` : "No successful snapshot";
      return `<tr><td><div class="profile-cell"><img class="profile-avatar" src="${escapeHtml(photo)}" alt="" onerror="this.src='${fallback}'"><div><div class="profile-name">${escapeHtml(profile.name)}</div><div class="profile-meta">${escapeHtml(profile.headline || profile.id)}</div></div></div></td><td>${escapeHtml(historyLabel)}</td><td>${count(profile.stored_activity_count)}</td><td>${count(profile.post_count)}</td><td>${count(profile.video_count)}</td><td>${count(profile.analysis_runs_since_tracking)}</td><td><a href="${escapeHtml(dashboard)}" class="button button-light">Open</a></td></tr>`;
    }).join("") : `<tr><td colspan="7">No saved profiles match this search.</td></tr>`;
    byId("profilePageLabel").textContent = `Page ${data.page || 1} of ${data.total_pages || 1} · ${Number(data.total_items || 0).toLocaleString()} profiles`;
    byId("profilesPrev").disabled = (data.page || 1) <= 1; byId("profilesNext").disabled = (data.page || 1) >= (data.total_pages || 1);
  }

  function fillProfileSelects(profiles) {
    const options = profiles.map((profile) => `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.name)}</option>`).join("");
    ["trendProfile", "contentProfile"].forEach((id) => { const select = byId(id); select.innerHTML = `<option value="">All profiles</option>${options}`; });
  }

  async function loadRuns() {
    const data = await api(`/api/dashboard/runs?${asQuery({ page: state.runPage, per_page: 20 })}`);
    state.runTotalPages = data.total_pages || 1; renderRuns(data);
  }

  function renderRuns(data) {
    const profiles = new Map(state.profiles.map((p) => [p.id, p.name]));
    const rows = data.items || [];
    byId("runRows").innerHTML = rows.length ? rows.map((run) => {
      const names = (run.profile_slugs || []).map((slug) => profiles.get(slug) || slug).join(", ") || "Profile not resolved";
      return `<tr><td>${escapeHtml(formatDate(run.completed_at || run.started_at))}</td><td>${escapeHtml(names)}</td><td>${escapeHtml(run.status || "Unknown")}</td><td>${count(run.total_activities)}</td><td>${count(run.posts_count)}</td><td>${count(run.videos_count)}</td><td>${escapeHtml(run.retrieval_completeness || "Unknown")}</td></tr>`;
    }).join("") : `<tr><td colspan="7">No durable analysis runs have been recorded yet.</td></tr>`;
    byId("runPageLabel").textContent = `Page ${data.page || 1} of ${data.total_pages || 1} · ${Number(data.total_items || 0).toLocaleString()} runs`;
    byId("runsPrev").disabled = (data.page || 1) <= 1; byId("runsNext").disabled = (data.page || 1) >= (data.total_pages || 1);
  }

  async function loadTrends() {
    const data = await api(`/api/dashboard/trends?${asQuery({ months: 12, profile: byId("trendProfile").value })}`);
    state.trends = data.items || []; renderChart();
  }

  function renderChart() {
    const rows = state.trends || [];
    if (!rows.length) { byId("trendChart").innerHTML = `<p class="empty">No monthly data available.</p>`; return; }
    const width = 1000, height = 270, left = 45, right = 18, top = 18, bottom = 44;
    const values = rows.map((row) => row[state.metric]);
    const known = values.filter((value) => value !== null && value !== undefined).map(Number);
    if (!known.length) { byId("trendChart").innerHTML = `<p class="empty">No stored ${state.metric.replaceAll("_count", "")} data is available for this period.</p>`; return; }
    const max = Math.max(1, ...known), plotW = width - left - right, plotH = height - top - bottom;
    const x = (index) => left + (rows.length <= 1 ? plotW / 2 : index * plotW / (rows.length - 1));
    const y = (value) => top + plotH - (Number(value) / max) * plotH;
    const guides = [0, .5, 1].map((fraction) => { const gy = top + plotH * fraction; const label = Math.round(max * (1 - fraction)); return `<line class="chart-gridline" x1="${left}" y1="${gy}" x2="${width-right}" y2="${gy}"/><text class="chart-label" x="${left-8}" y="${gy+4}" text-anchor="end">${label}</text>`; }).join("");
    const segments = []; let segment = [];
    rows.forEach((row, index) => { const value = row[state.metric]; if (value === null || value === undefined) { if (segment.length) segments.push(segment); segment = []; } else segment.push(`${x(index)},${y(value)}`); });
    if (segment.length) segments.push(segment);
    const lines = segments.map((points) => points.length > 1 ? `<polyline class="chart-line" points="${points.join(" ")}"/>` : "").join("");
    const dots = rows.map((row, index) => { const value = row[state.metric]; const isUnknown = value === null || value === undefined; const cy = isUnknown ? top + plotH + 8 : y(value); return `<circle class="chart-dot${isUnknown ? " unknown" : ""}" tabindex="0" role="img" aria-label="${escapeHtml(row.month)}: ${isUnknown ? "unknown coverage" : `${escapeHtml(value)} ${escapeHtml(state.metric)}`}" data-index="${index}" cx="${x(index)}" cy="${cy}" r="5"/><text class="chart-label" x="${x(index)}" y="${height-15}" text-anchor="middle">${escapeHtml(row.month.slice(5))}</text>`; }).join("");
    const title = state.metric.replaceAll("_", " ").replace("count", "").trim();
    byId("trendChart").innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Monthly ${escapeHtml(title)} trend"><g>${guides}</g>${lines}<g>${dots}</g></svg>`;
    byId("trendChart").querySelectorAll(".chart-dot").forEach((dot) => {
      const show = (event) => { const row = rows[Number(dot.dataset.index)]; const value = row[state.metric]; const tip = byId("chartTooltip"); tip.textContent = `${row.month}: ${value == null ? "Unknown coverage" : Number(value).toLocaleString() + " " + title}`; tip.hidden = false; tip.style.left = `${Math.min(window.innerWidth - 210, event.clientX + 12)}px`; tip.style.top = `${Math.max(8, event.clientY - 38)}px`; };
      dot.addEventListener("pointerenter", show); dot.addEventListener("pointermove", show); dot.addEventListener("pointerleave", () => { byId("chartTooltip").hidden = true; });
      dot.addEventListener("focus", (event) => show({ clientX: window.innerWidth / 2, clientY: window.innerHeight / 2 }));
    });
  }

  async function loadContent() {
    const params = asQuery({ profile: byId("contentProfile").value, type: byId("contentType").value, q: byId("contentSearch").value.trim(), from: byId("contentFrom").value, to: byId("contentTo").value, sort: byId("contentSort").value, page: state.contentPage, per_page: 12 });
    const data = await api(`/api/dashboard/content?${params}`);
    state.contentTotalPages = data.total_pages || 1;
    renderContent(data);
  }

  function videoMarkup(row) {
    const video = safeUrl(row.video_url); const thumb = safeUrl(row.video_thumbnail || row.media_url); const post = safeUrl(row.post_url || row.original_post_url || row.video_url);
    if (!row.is_video) return row.media_url && safeUrl(row.media_url) ? `<div class="content-media"><img loading="lazy" src="${escapeHtml(safeUrl(row.media_url))}" alt="Post media"></div>` : "";
    const direct = video && (/\.(mp4|webm|ogg|m4v)(\?.*)?$/i.test(video) || /videocdn/i.test(video));
    const extension = direct ? (video.match(/\.(mp4|webm|ogg|m4v)(\?.*)?$/i) || [])[1] : "mp4";
    const mime = { mp4: "video/mp4", m4v: "video/mp4", webm: "video/webm", ogg: "video/ogg" }[(extension || "mp4").toLowerCase()] || "video/mp4";
    if (direct) return `<div class="content-media"><video controls preload="metadata"${thumb ? ` poster="${escapeHtml(thumb)}"` : ""}><source src="${escapeHtml(video)}" type="${mime}"></video></div><div class="content-card-body"><a href="${escapeHtml(post || "#")}" target="_blank" rel="noopener noreferrer">If playback is unavailable, open on LinkedIn ↗</a></div>`;
    if (video && /(^|\.)youtube\.com|youtu\.be|(^|\.)vimeo\.com/i.test(new URL(video).hostname)) {
      let embed = video;
      try { const parsed = new URL(video); if (parsed.hostname.includes("youtu.be")) embed = `https://www.youtube-nocookie.com/embed/${parsed.pathname.slice(1)}`; else if (parsed.hostname.includes("youtube.com") && parsed.searchParams.get("v")) embed = `https://www.youtube-nocookie.com/embed/${parsed.searchParams.get("v")}`; else if (parsed.hostname.includes("vimeo.com")) embed = `https://player.vimeo.com/video/${parsed.pathname.split("/").filter(Boolean).pop()}`; } catch (_) {}
      return `<div class="content-media"><iframe loading="lazy" src="${escapeHtml(embed)}" title="Embedded video" allow="fullscreen; picture-in-picture" allowfullscreen></iframe></div>`;
    }
    return `<div class="content-media">${thumb ? `<a href="${escapeHtml(post || "#")}" target="_blank" rel="noopener noreferrer"><img loading="lazy" src="${escapeHtml(thumb)}" alt="Video thumbnail"><span class="sr-only">Open LinkedIn video</span></a>` : `<a href="${escapeHtml(post || "#")}" target="_blank" rel="noopener noreferrer">Video stream unavailable · Open on LinkedIn</a>`}</div>`;
  }

  function renderContent(data) {
    const items = data.items || [];
    byId("contentCount").textContent = `${Number(data.total_items || 0).toLocaleString()} records`;
    byId("contentGrid").innerHTML = items.length ? items.map((row) => {
      const profile = state.profiles.find((p) => p.id === row.profile_slug);
      const kind = row.is_video ? "Video" : row.type || "Activity";
      const text = row.post_content || row.comment_text || row.text || "No text was stored for this activity.";
      return `<article class="content-card">${videoMarkup(row)}<div class="content-card-body"><div class="content-meta"><span class="content-badge">${escapeHtml(kind)}</span><span>${escapeHtml(profile?.name || row.profile_slug || "Unassigned import")}</span><span>${escapeHtml(formatDate(row.date))}</span></div><h3>${escapeHtml(row.post_author || row.subject_name || "LinkedIn activity")}</h3><p class="content-text">${escapeHtml(text)}</p>${safeUrl(row.post_url) ? `<a href="${escapeHtml(safeUrl(row.post_url))}" target="_blank" rel="noopener noreferrer">Open original activity ↗</a>` : ""}</div></article>`;
    }).join("") : `<div class="empty">No stored content matches these filters.</div>`;
    byId("contentPageLabel").textContent = `Page ${data.page || 1} of ${data.total_pages || 1}`;
    byId("contentPrev").disabled = (data.page || 1) <= 1; byId("contentNext").disabled = (data.page || 1) >= (data.total_pages || 1);
  }

  function bindControls() {
    byId("profileSearch").addEventListener("input", debounce(() => { state.profilePage = 1; loadProfiles().catch(showError); }, 250));
    document.querySelectorAll(".sort-button").forEach((button) => button.addEventListener("click", () => { const key = button.dataset.sort; state.profileOrder = state.profileSort === key && state.profileOrder === "asc" ? "desc" : "asc"; state.profileSort = key; state.profilePage = 1; loadProfiles().catch(showError); }));
    byId("profilesPrev").addEventListener("click", () => { state.profilePage = Math.max(1, state.profilePage - 1); loadProfiles().catch(showError); });
    byId("profilesNext").addEventListener("click", () => { state.profilePage += 1; loadProfiles().catch(showError); });
    byId("runsPrev").addEventListener("click", () => { state.runPage = Math.max(1, state.runPage - 1); loadRuns().catch(showError); });
    byId("runsNext").addEventListener("click", () => { state.runPage += 1; loadRuns().catch(showError); });
    byId("contentPrev").addEventListener("click", () => { state.contentPage = Math.max(1, state.contentPage - 1); loadContent().catch(showError); });
    byId("contentNext").addEventListener("click", () => { state.contentPage += 1; loadContent().catch(showError); });
    byId("applyContentFilters").addEventListener("click", () => { state.contentPage = 1; loadContent().catch(showError); });
    byId("contentSearch").addEventListener("keydown", (event) => { if (event.key === "Enter") { state.contentPage = 1; loadContent().catch(showError); } });
    byId("contentProfile").addEventListener("change", () => { state.contentPage = 1; loadContent().catch(showError); });
    byId("contentType").addEventListener("change", () => { state.contentPage = 1; loadContent().catch(showError); });
    byId("contentSort").addEventListener("change", () => { state.contentPage = 1; loadContent().catch(showError); });
    document.querySelectorAll(".metric-button").forEach((button) => button.addEventListener("click", () => { document.querySelectorAll(".metric-button").forEach((b) => b.classList.toggle("active", b === button)); state.metric = button.dataset.metric; renderChart(); }));
    byId("trendProfile").addEventListener("change", () => loadTrends().catch(showError));
  }

  function debounce(fn, delay) { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); }; }
  document.addEventListener("DOMContentLoaded", init);
})();
