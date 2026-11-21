(() => {
  "use strict";

  const state = { page: 1, totalPages: 1, kind: "", profiles: [], requestVersion: 0, searchTimer: null };
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));

  function safeUrl(value) {
    if (typeof value !== "string" || !value.trim()) return "";
    try {
      const url = new URL(value.trim(), window.location.origin);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch (_) { return ""; }
  }

  function queryString(values) {
    return new URLSearchParams(Object.entries(values).filter(([, value]) => value !== "" && value !== null && value !== undefined)).toString();
  }

  function formatDate(value) {
    if (!value) return "Date unavailable";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return String(value);
    const hasTime = /[T ]\d{2}:\d{2}/.test(String(value));
    return date.toLocaleString(undefined, hasTime
      ? { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }
      : { day: "2-digit", month: "short", year: "numeric" });
  }

  async function api(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  }

  function setStatus(message, isError = false) {
    const status = byId("libraryStatus");
    status.textContent = message;
    status.className = `notice library-status${isError ? " notice-error" : ""}`;
  }

  function selectedProfileName() {
    const id = byId("contentProfile").value;
    return state.profiles.find((profile) => profile.id === id)?.name || "All profiles";
  }

  async function init() {
    bindControls();
    try {
      const result = await api("/api/dashboard/profile-options");
      state.profiles = result.items || [];
      byId("contentProfile").innerHTML = `<option value="">All profiles</option>${state.profiles.map((profile) => `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.name)}</option>`).join("")}`;
      await loadContent();
    } catch (error) {
      setStatus(`${error.message || "Could not load stored content."} No sample data is being shown.`, true);
      byId("contentCount").textContent = "Unavailable";
      byId("contentGrid").innerHTML = `<div class="empty">${escapeHtml(error.message || "Content could not be loaded.")}</div>`;
    }
  }

  async function loadContent() {
    const requestVersion = ++state.requestVersion;
    const params = queryString({
      profile: byId("contentProfile").value,
      type: state.kind,
      q: byId("contentSearch").value.trim(),
      from: byId("contentFrom").value,
      to: byId("contentTo").value,
      sort: byId("contentSort").value,
      page: state.page,
      per_page: 12,
    });
    byId("selectedProfileLabel").textContent = `Showing: ${selectedProfileName()}`;
    byId("contentGrid").innerHTML = `<p class="empty">Loading content…</p>`;
    setStatus("Loading stored records…");
    try {
      const result = await api(`/api/dashboard/content?${params}`);
      if (requestVersion !== state.requestVersion) return;
      state.totalPages = result.total_pages || 1;
      renderContent(result);
      setStatus("Showing actual records currently stored in MongoDB.");
    } catch (error) {
      if (requestVersion !== state.requestVersion) return;
      setStatus(`${error.message || "Could not load content."} No sample data is being shown.`, true);
      byId("contentGrid").innerHTML = `<div class="empty">${escapeHtml(error.message || "Content could not be loaded.")}</div>`;
      byId("contentCount").textContent = "Unavailable";
      byId("pageLabel").textContent = "";
    }
  }

  function directVideoSource(value, declaredMime = "") {
    const url = safeUrl(value);
    if (!url) return null;
    let parsed;
    try { parsed = new URL(url); } catch (_) { return null; }
    const hostname = parsed.hostname.toLowerCase();
    if (hostname === "linkedin.com" || hostname.endsWith(".linkedin.com") || /(^|\.)youtube\.com$|(^|\.)youtu\.be$|(^|\.)vimeo\.com$/.test(hostname)) return null;
    const extension = (parsed.pathname.match(/\.(mp4|m4v|webm|ogg)$/i) || [])[1]?.toLowerCase();
    const extensionMime = { mp4: "video/mp4", m4v: "video/mp4", webm: "video/webm", ogg: "video/ogg" }[extension];
    const normalizedMime = String(declaredMime || "").split(";")[0].trim().toLowerCase();
    const supportedMimes = new Set(["video/mp4", "video/webm", "video/ogg"]);
    const mime = supportedMimes.has(normalizedMime) ? normalizedMime : extensionMime;
    // A post permalink, LinkedIn video page, blob URL, or external embed URL is not a direct media stream.
    return mime ? { url, mime } : null;
  }

  function officialLinkedInEmbed(value) {
    const url = safeUrl(value);
    if (!url) return "";
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.toLowerCase();
      if (parsed.protocol !== "https:" || !["linkedin.com", "www.linkedin.com"].includes(host)) return "";
      if (!/^\/embed\/feed\/update\//i.test(parsed.pathname)) return "";
      return parsed.href;
    } catch (_) { return ""; }
  }

  function linkedInPostUrl(value) {
    const url = safeUrl(value);
    if (!url) return "";
    try {
      const parsed = new URL(url);
      const host = parsed.hostname.toLowerCase();
      return parsed.protocol === "https:" && (host === "linkedin.com" || host.endsWith(".linkedin.com")) ? parsed.href : "";
    } catch (_) { return ""; }
  }

  function imageSource(row) {
    const candidates = [row.image_url, row.photo_url, row.media_url];
    for (const item of [...(Array.isArray(row.images) ? row.images : []), ...(Array.isArray(row.media_urls) ? row.media_urls : [])]) {
      candidates.push(typeof item === "string" ? item : item?.url || item?.src || item?.media_url);
    }
    return candidates.map(safeUrl).find(Boolean) || "";
  }

  function videoUnavailableMessage(row, hasBadStream = false) {
    if (hasBadStream) return "Playback failed. The media may be expired, protected, or unsupported by this browser.";
    const status = String(row.video_status || "").toLowerCase();
    if (/auth|protect|login|restricted/.test(status)) return "LinkedIn did not provide a playable stream for this post. This is only a preview; open the original post on LinkedIn to view it if your account has access.";
    if ((row.video_stream_url || row.video_url) && !directVideoSource(row.video_stream_url || row.video_url, row.video_mime_type)) return "No directly playable media URL was stored. A LinkedIn post or video page cannot be used as a video stream.";
    if (row.video_thumbnail || row.media_url) return "The video was detected, but an accessible video stream was not extracted.";
    return "No accessible video stream or thumbnail is available for this activity.";
  }

  function renderMedia(row) {
    if (row.is_video) {
      const stream = directVideoSource(row.video_stream_url || row.video_url, row.video_mime_type);
      const embed = officialLinkedInEmbed(row.embed_src);
      const thumbnail = safeUrl(row.video_thumbnail || row.media_url);
      if (stream) {
        return `<div class="content-media library-video-wrap"><video class="library-video" controls playsinline preload="metadata"${thumbnail ? ` poster="${escapeHtml(thumbnail)}"` : ""} aria-label="Video activity"><source src="${escapeHtml(stream.url)}" type="${stream.mime}"></video>${embed ? `<iframe class="library-video-embed" src="${escapeHtml(embed)}" title="Embedded LinkedIn post" loading="lazy" allowfullscreen hidden></iframe>` : ""}</div>`;
      }
      if (embed) return `<div class="content-media library-video-wrap"><iframe class="library-video-embed" src="${escapeHtml(embed)}" title="Embedded LinkedIn post" loading="lazy" allowfullscreen></iframe></div>`;
      return `<div class="content-media library-unavailable-media">${thumbnail ? `<img class="library-preview-image" loading="lazy" src="${escapeHtml(thumbnail)}" alt="Video thumbnail">` : ""}<div class="library-media-empty"><strong>Inline playback unavailable</strong><span>${escapeHtml(videoUnavailableMessage(row))}</span></div></div>`;
    }

    if (row.is_photo) {
      const image = imageSource(row);
      if (image) return `<div class="content-media"><img class="library-photo" loading="lazy" src="${escapeHtml(image)}" alt="Extracted activity image"></div>`;
      return `<div class="library-media-empty"><strong>Photo identified</strong><span>An accessible image URL was not stored with this activity.</span></div>`;
    }
    return "";
  }

  function eventLabel(kind) {
    return ({ post: "Post", video: "Video activity", like: "Like / Reaction", comment: "Comment", share: "Repost / Share" })[kind] || "Activity";
  }

  function initials(name) {
    return String(name || "?").trim().split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase() || "?";
  }

  function profileIdentity(row) {
    const name = row.profile_name || row.subject_name || row.profile_slug || "Unassigned import";
    const photo = safeUrl(row.profile_picture);
    const avatar = photo
      ? `<img class="library-avatar library-avatar-image" loading="lazy" src="${escapeHtml(photo)}" alt="" data-initials="${escapeHtml(initials(name))}">`
      : `<span class="library-avatar library-avatar-fallback" aria-hidden="true">${escapeHtml(initials(name))}</span>`;
    return `<span class="library-identity">${avatar}<span class="library-profile-name">${escapeHtml(name)}</span></span>`;
  }

  function videoEmbedEditor(row, originalUrl) {
    if (!row.is_video || row.embed_src || !originalUrl || !row.profile_slug) return "";
    return `<details class="library-embed-editor"><summary>Add official LinkedIn embed</summary><p>For a public post, copy LinkedIn’s “Embed this post” code and paste it here. Protected posts may not be embeddable.</p><form class="library-embed-form" data-profile="${escapeHtml(row.profile_slug)}" data-post="${escapeHtml(originalUrl)}"><label>LinkedIn embed code<textarea required maxlength="12000" rows="3" placeholder="Paste the official iframe code"></textarea></label><div class="library-embed-actions"><button class="button button-light" type="submit">Save embed</button><span class="library-embed-status" role="status"></span></div></form></details>`;
  }

  function engagement(row) {
    return [
      ["Likes", row.likes], ["Comments", row.comments], ["Shares", row.shares],
    ].filter(([, value]) => Number.isFinite(Number(value)) && Number(value) > 0)
      .map(([label, value]) => `<span>${escapeHtml(label)} ${Number(value).toLocaleString()}</span>`).join("");
  }

  function renderCard(row) {
    const kind = row.activity_kind || row.type || "other";
    const mediaType = row.is_video ? "Video" : row.is_photo ? "Photo" : "";
    const eventText = kind === "comment"
      ? row.comment_text || row.activity_text || row.text
      : kind === "like"
        ? row.reaction_type ? `Reacted: ${row.reaction_type}` : row.activity_text || row.text || "Reaction recorded"
        : kind === "share"
          ? row.activity_text || row.text || "Reposted or shared this activity"
          : row.post_content || row.original_post_text || row.text;
    const originalText = row.original_post_text || row.post_content || "";
    const showOriginalContext = ["like", "comment", "share"].includes(kind) && originalText && originalText !== eventText;
    const author = row.post_author || row.original_post_author || row.subject_name || "LinkedIn activity";
    const originalUrl = linkedInPostUrl(row.linkedin_post_url || row.original_post_url || row.post_url);
    const engagementHtml = engagement(row);
    const badges = `<span class="content-badge">${escapeHtml(eventLabel(kind))}</span>${mediaType ? `<span class="content-badge content-badge-media">${mediaType}</span>` : ""}`;

    return `<article class="content-card library-card">${renderMedia(row)}<div class="content-card-body"><div class="content-meta">${profileIdentity(row)}<span class="content-date">${escapeHtml(formatDate(row.date))}</span></div><div class="library-badges">${badges}</div><h3>${escapeHtml(author)}</h3><p class="content-text">${escapeHtml(eventText || "No event text was stored for this activity.")}</p>${showOriginalContext ? `<div class="library-original-post"><strong>Original post</strong><p>${escapeHtml(originalText)}</p></div>` : ""}${engagementHtml ? `<div class="library-engagement">${engagementHtml}</div>` : ""}${videoEmbedEditor(row, originalUrl)}${originalUrl ? `<div class="library-actions"><a href="${escapeHtml(originalUrl)}" target="_blank" rel="noopener noreferrer">Open Original Post on LinkedIn ↗</a></div>` : ""}</div></article>`;
  }

  function emptyMessage(result) {
    const labels = { video: "videos", photo: "photos", post: "posts", like: "likes or reactions", comment: "comments", repost: "reposts or shares" };
    const noun = labels[state.kind] || "content";
    const profileName = selectedProfileName();
    const range = byId("contentFrom").value || byId("contentTo").value ? " within the selected date range" : "";
    return `No ${noun} found for ${profileName}${range}.`;
  }

  function renderContent(result) {
    const items = result.items || [];
    byId("contentCount").textContent = `${Number(result.total_items || 0).toLocaleString()} ${Number(result.total_items || 0) === 1 ? "record" : "records"}`;
    byId("contentGrid").innerHTML = items.length
      ? items.map(renderCard).join("")
      : `<div class="empty">${escapeHtml(emptyMessage(result))}</div>`;
    byId("pageLabel").textContent = `Page ${result.page || 1} of ${result.total_pages || 1}`;
    byId("previousPage").disabled = (result.page || 1) <= 1;
    byId("nextPage").disabled = (result.page || 1) >= (result.total_pages || 1);
    bindMediaFallbacks();
  }

  function bindMediaFallbacks() {
    document.querySelectorAll(".library-avatar-image").forEach((image) => {
      image.addEventListener("error", () => {
        const fallback = document.createElement("span");
        fallback.className = "library-avatar library-avatar-fallback";
        fallback.setAttribute("aria-hidden", "true");
        fallback.textContent = image.dataset.initials || "?";
        image.replaceWith(fallback);
      }, { once: true });
    });
    document.querySelectorAll(".library-video").forEach((video) => {
      video.addEventListener("error", () => {
        const wrapper = video.closest(".content-media");
        if (!wrapper) return;
        const embed = wrapper.querySelector(".library-video-embed");
        if (embed) {
          video.hidden = true;
          embed.hidden = false;
          return;
        }
        const poster = safeUrl(video.getAttribute("poster"));
        wrapper.innerHTML = `${poster ? `<img class="library-preview-image" src="${escapeHtml(poster)}" alt="Video thumbnail">` : ""}<div class="library-media-error">${escapeHtml(videoUnavailableMessage({ video_url: video.querySelector("source")?.src }, true))}</div>`;
      }, { once: true });
    });
    document.querySelectorAll(".library-photo").forEach((image) => {
      image.addEventListener("error", () => {
        const wrapper = image.closest(".content-media");
        if (wrapper) wrapper.innerHTML = `<div class="library-media-error">The image is currently unavailable or its URL has expired.</div>`;
      }, { once: true });
    });
    document.querySelectorAll(".library-preview-image").forEach((image) => {
      image.addEventListener("error", () => image.remove(), { once: true });
    });
    document.querySelectorAll(".library-embed-form").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const button = form.querySelector('button[type="submit"]');
        const status = form.querySelector(".library-embed-status");
        const input = form.querySelector("textarea");
        button.disabled = true;
        status.textContent = "Validating embed…";
        try {
          const response = await fetch("/api/dashboard/video-embeds", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify({ profile_slug: form.dataset.profile, post_url: form.dataset.post, embed_code: input.value }),
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.error || `Save failed (${response.status})`);
          await loadContent();
          setStatus("Official LinkedIn embed saved for this profile and post.");
        } catch (error) {
          status.textContent = error.message || "Could not save this embed.";
          button.disabled = false;
        }
      });
    });
  }

  function applyFilters() { state.page = 1; loadContent(); }
  function bindControls() {
    byId("applyFilters").addEventListener("click", applyFilters);
    byId("contentSearch").addEventListener("input", () => {
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(applyFilters, 250);
    });
    byId("contentSearch").addEventListener("keydown", (event) => { if (event.key === "Enter") applyFilters(); });
    ["contentProfile", "contentFrom", "contentTo", "contentSort"].forEach((id) => byId(id).addEventListener("change", applyFilters));
    document.querySelectorAll(".content-type-tab").forEach((button) => button.addEventListener("click", () => {
      state.kind = button.dataset.kind || "";
      state.page = 1;
      document.querySelectorAll(".content-type-tab").forEach((tab) => tab.setAttribute("aria-pressed", String(tab === button)));
      loadContent();
    }));
    byId("previousPage").addEventListener("click", () => { state.page = Math.max(1, state.page - 1); loadContent(); });
    byId("nextPage").addEventListener("click", () => { state.page = Math.min(state.totalPages, state.page + 1); loadContent(); });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
