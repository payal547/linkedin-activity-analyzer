/**
 * Connections Page Controller - Real LinkedIn & Imported Connections
 * Manages optional OAuth profile status, importing official Connections.csv / export ZIP,
 * rendering real connections with legitimate photo or clean initials/avatar fallbacks,
 * full-dataset searching, pagination for large sets, and selecting connections for analysis.
 */

let selectedConnection = null;
let currentProfile = null;
let selfProfileData = null;
let allRealConnections = [];
let filteredConnections = [];
let currentPage = 1;
const PAGE_SIZE = 12;

document.addEventListener("DOMContentLoaded", () => {
    initConnectionsPage();
});

async function initConnectionsPage() {
    // 1. Check optional LinkedIn OAuth profile state
    await loadUserProfile();

    // 2. ALWAYS load real / imported connections (never gate behind OAuth)
    await loadConnections();
}

async function loadUserProfile() {
    const nameEl = document.getElementById("profileName");
    const emailEl = document.getElementById("profileEmail");
    const statusEl = document.getElementById("profileStatus");
    const avatarEl = document.getElementById("profileAvatar");
    const reconnectEl = document.getElementById("reconnectLink");

    try {
        const response = await fetch("/api/profile");
        const data = await response.json();

        if (!response.ok || !data.connected) {
            throw new Error(data.error || "LinkedIn profile not connected");
        }

        currentProfile = data;
        if (nameEl) nameEl.textContent = data.name || "LinkedIn User";
        if (emailEl) emailEl.textContent = data.email || "Email not available";

        if (statusEl) {
            statusEl.textContent = "LinkedIn Connected";
            statusEl.className = "status connected";
        }

        if (reconnectEl) {
            reconnectEl.style.display = "inline";
            reconnectEl.textContent = "Switch Account";
            reconnectEl.href = "/login";
        }

        if (avatarEl) {
            if (data.picture) {
                avatarEl.innerHTML = `<img src="${data.picture}" alt="Profile Photo">`;
            } else {
                avatarEl.textContent = (data.name || "U").charAt(0).toUpperCase();
            }
        }

        return true;
    } catch (error) {
        // LinkedIn OAuth is not connected - OAuth is OPTIONAL
        // Do NOT block the page or overwrite the connections list!
        if (nameEl) nameEl.textContent = "LinkedIn Account: Not Connected (Optional)";
        if (emailEl) emailEl.textContent = "You can connect your LinkedIn account for live profile features, or analyze your imported connections below.";

        if (statusEl) {
            statusEl.textContent = "Not Connected (Optional)";
            statusEl.className = "status optional";
        }
        if (reconnectEl) {
            reconnectEl.style.display = "inline";
            reconnectEl.textContent = "Connect with LinkedIn →";
            reconnectEl.href = "/login";
        }
        if (avatarEl) {
            avatarEl.textContent = "in";
        }

        return false;
    }
}

async function loadConnections() {
    const listEl = document.getElementById("connectionsList");
    if (!listEl) return;

    try {
        const response = await fetch("/api/connections");
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Failed to load connections.");
        }

        selfProfileData = data.self_profile || null;
        allRealConnections = data.real_connections || data.saved_connections || [];
        filteredConnections = [...allRealConnections];
        currentPage = 1;

        // Render current page
        renderPage();

        // API Notice (shown only if user authenticated with OAuth and live API was queried)
        renderApiNotice(data);
    } catch (error) {
        console.error("Error loading connections:", error);
        listEl.innerHTML = `
            <div class="message">
                <h3 style="color:#d93025; margin-bottom:8px;">Unable to load connections</h3>
                <p style="color:#666;">${escapeHTML(error.message)}</p>
            </div>
        `;
    }
}

function renderApiNotice(data) {
    const noticeEl = document.getElementById("apiNotice");
    if (!noticeEl) return;

    if (data.connected && !data.api_available) {
        noticeEl.style.display = "block";
        noticeEl.innerHTML = `
            <div>
                <h4 style="font-size:15px; color:#0a66c2; margin-bottom:6px;">
                    ℹ️ Note on LinkedIn Live Connections API
                </h4>
                <p style="font-size:13px; color:#444; line-height:1.5;">
                    LinkedIn restricts automated 1st-degree connection synchronization for standard third-party apps (HTTP 403 Forbidden). 
                    Your real connections are imported and analyzed from your official <strong><code>Connections.csv</code></strong> export below.
                </p>
            </div>
        `;
    } else {
        noticeEl.style.display = "none";
    }
}

function renderPage() {
    const container = document.getElementById("connectionsList");
    if (!container) return;

    container.innerHTML = "";

    // 1. Update count badge & clear button
    const countBadge = document.getElementById("connectionsCountBadge");
    if (countBadge) {
        if (allRealConnections.length > 0) {
            countBadge.textContent = `${allRealConnections.length} imported connection${allRealConnections.length > 1 ? 's' : ''} available`;
        } else {
            countBadge.textContent = "0 available";
        }
    }

    const clearBtn = document.getElementById("clearConnectionsBtn");
    if (clearBtn) {
        clearBtn.style.display = allRealConnections.length > 0 ? "inline-block" : "none";
    }

    // 2. If authenticated, render self card at top of page 1
    if (selfProfileData && selfProfileData.name && currentPage === 1) {
        const selfCard = document.createElement("div");
        selfCard.className = "friend-card primary-card";
        const selfInitial = (selfProfileData.name || "U").charAt(0).toUpperCase();
        const selfAvatarHTML = selfProfileData.picture
            ? `<img src="${selfProfileData.picture}" alt="${escapeHTML(selfProfileData.name)}">`
            : selfInitial;

        selfCard.innerHTML = `
            <div class="card-top-row">
                <span class="badge-source badge-real">Your Real Profile</span>
            </div>
            <div class="friend-avatar">${selfAvatarHTML}</div>
            <h3>${escapeHTML(selfProfileData.name)} (You)</h3>
            <p class="headline">${escapeHTML(selfProfileData.headline || "Authenticated LinkedIn Account")}</p>
            <button class="select-btn select-primary" onclick="selectConnection('${selfProfileData.id}', '${escapeQuotes(selfProfileData.name)}', 'real_linkedin', this)">
                Analyze My Profile
            </button>
        `;
        container.appendChild(selfCard);
    }

    // 3. Check for empty state
    if (filteredConnections.length === 0) {
        const emptyDiv = document.createElement("div");
        emptyDiv.className = "message";

        if (allRealConnections.length === 0) {
            // Requirement 8: If no Connections.csv has been imported yet
            emptyDiv.innerHTML = `
                <h3 style="color:#333; margin-bottom:8px;">No imported connections yet</h3>
                <p style="color:#666; font-size:14px;">Upload your LinkedIn Connections.csv to continue.</p>
            `;
        } else {
            // Search query returned no matches
            const query = (document.getElementById("searchBox")?.value || "").trim();
            emptyDiv.innerHTML = `
                <h3 style="color:#333; margin-bottom:8px;">No matching connections found</h3>
                <p style="color:#666; font-size:14px;">No imported connection matched "<strong>${escapeHTML(query)}</strong>". Try searching for a different name, position, or company.</p>
            `;
        }

        container.appendChild(emptyDiv);
        updatePaginationControls(0, 0, 0);
        return;
    }

    // 4. Calculate pagination slice
    const totalItems = filteredConnections.length;
    const totalPages = Math.ceil(totalItems / PAGE_SIZE) || 1;
    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1) currentPage = 1;

    const startIdx = (currentPage - 1) * PAGE_SIZE;
    const endIdx = Math.min(startIdx + PAGE_SIZE, totalItems);
    const pageItems = filteredConnections.slice(startIdx, endIdx);

    // 5. Render connection cards for current page
    pageItems.forEach(conn => {
        const card = document.createElement("div");
        const isSelected = selectedConnection && selectedConnection.id === conn.id;
        card.className = `friend-card${isSelected ? ' selected-card' : ''}`;

        const initials = getInitials(conn.name);
        const avatarBg = getAvatarColor(conn.name);

        let avatarHTML = "";
        let avatarNoteHTML = "";

        if (conn.picture && (conn.picture.startsWith("http://") || conn.picture.startsWith("https://"))) {
            // Valid legitimate photo URL from data
            avatarHTML = `<div class="friend-avatar"><img src="${escapeHTML(conn.picture)}" alt="${escapeHTML(conn.name)}" onerror="this.onerror=null; this.parentElement.style.background='${avatarBg}'; this.parentElement.innerHTML='${initials}';"></div>`;
        } else {
            // Clean initials fallback with distinct deterministic color
            avatarHTML = `<div class="friend-avatar" style="background:${avatarBg};">${initials}</div>`;
            avatarNoteHTML = `<span class="avatar-fallback-tag" title="LinkedIn official data export does not include profile photo URLs">Initials Avatar (No photo in export)</span>`;
        }

        let headlineText = conn.headline || "";
        if (!headlineText && (conn.position || conn.company)) {
            if (conn.position && conn.company) {
                headlineText = `${conn.position} at ${conn.company}`;
            } else {
                headlineText = conn.position || conn.company;
            }
        }
        if (!headlineText) {
            headlineText = "LinkedIn Connection";
        }

        // Metadata: Company, Position, Connected On, Profile URL
        let metaHTML = "";
        if (conn.company && (!headlineText.includes(conn.company) || !conn.headline)) {
            metaHTML += `<span>Company: <strong>${escapeHTML(conn.company)}</strong></span>`;
        }
        if (conn.position && (!headlineText.includes(conn.position) || !conn.headline)) {
            metaHTML += `<span>Position: <strong>${escapeHTML(conn.position)}</strong></span>`;
        }
        if (conn.connected_on) {
            metaHTML += `<span>Connected on: <strong>${escapeHTML(conn.connected_on)}</strong></span>`;
        }
        if (conn.profile_url) {
            metaHTML += `<a href="${escapeHTML(conn.profile_url)}" target="_blank" rel="noopener noreferrer">View LinkedIn Profile ↗</a>`;
        }

        card.innerHTML = `
            <div class="card-top-row">
                <span class="badge-source badge-imported">Imported from LinkedIn Connections export</span>
                <button class="delete-conn-btn" title="Remove Connection" onclick="deleteConnection('${conn.id}', event)">✕</button>
            </div>
            ${avatarHTML}
            ${avatarNoteHTML}
            <h3>${escapeHTML(conn.name)}</h3>
            <p class="headline">${escapeHTML(headlineText)}</p>
            ${metaHTML ? `<div class="meta-details">${metaHTML}</div>` : ""}
            <button class="select-btn" onclick="selectConnection('${conn.id}', '${escapeQuotes(conn.name)}', 'imported', this)" style="${isSelected ? 'background:#218838;' : ''}">
                ${isSelected ? 'Selected ✓' : 'Select Connection'}
            </button>
        `;
        container.appendChild(card);
    });

    // 6. Update pagination controls
    updatePaginationControls(startIdx + 1, endIdx, totalItems);
}

function updatePaginationControls(start, end, total) {
    const bar = document.getElementById("paginationBar");
    const info = document.getElementById("paginationInfo");
    const indicator = document.getElementById("pageIndicator");
    const prevBtn = document.getElementById("prevPageBtn");
    const nextBtn = document.getElementById("nextPageBtn");

    if (!bar) return;

    if (total <= PAGE_SIZE && total > 0) {
        bar.style.display = "flex";
        if (info) info.textContent = `Showing all ${total} connection${total > 1 ? 's' : ''}`;
        if (indicator) indicator.textContent = "Page 1 of 1";
        if (prevBtn) prevBtn.disabled = true;
        if (nextBtn) nextBtn.disabled = true;
        return;
    }

    if (total === 0) {
        bar.style.display = "none";
        return;
    }

    bar.style.display = "flex";
    const totalPages = Math.ceil(total / PAGE_SIZE);

    if (info) info.textContent = `Showing ${start}-${end} of ${total} connections`;
    if (indicator) indicator.textContent = `Page ${currentPage} of ${totalPages}`;
    if (prevBtn) prevBtn.disabled = (currentPage <= 1);
    if (nextBtn) nextBtn.disabled = (currentPage >= totalPages);
}

function changePage(delta) {
    const totalPages = Math.ceil(filteredConnections.length / PAGE_SIZE) || 1;
    const newPage = currentPage + delta;
    if (newPage >= 1 && newPage <= totalPages) {
        currentPage = newPage;
        renderPage();
        const headerEl = document.querySelector(".section-header");
        if (headerEl) {
            headerEl.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    }
}

function searchFriends() {
    const query = (document.getElementById("searchBox")?.value || "").toLowerCase().trim();

    if (!query) {
        filteredConnections = [...allRealConnections];
    } else {
        filteredConnections = allRealConnections.filter(c => {
            const name = (c.name || "").toLowerCase();
            const headline = (c.headline || "").toLowerCase();
            const company = (c.company || "").toLowerCase();
            const position = (c.position || "").toLowerCase();
            return name.includes(query) || headline.includes(query) || company.includes(query) || position.includes(query);
        });
    }

    currentPage = 1;
    renderPage();
}

function selectConnection(id, name, source, btnElement) {
    selectedConnection = { id, name, source };

    // Persist choice for Activity Filter & Dashboard
    localStorage.setItem("selectedSubjectId", id);
    localStorage.setItem("selectedFriend", name);
    localStorage.setItem("selectedSource", source);

    // Update all card borders and buttons
    const allCards = document.querySelectorAll(".friend-card");
    allCards.forEach(card => card.classList.remove("selected-card"));

    const allButtons = document.querySelectorAll(".friend-card .select-btn");
    allButtons.forEach(btn => {
        btn.textContent = btn.classList.contains("select-primary") ? "Analyze My Profile" : "Select Connection";
        btn.style.background = "#0a66c2";
    });

    if (btnElement) {
        btnElement.textContent = "Selected ✓";
        btnElement.style.background = "#218838";
        const parentCard = btnElement.closest(".friend-card");
        if (parentCard) parentCard.classList.add("selected-card");
    }

    // Reveal and configure bottom Continue button
    const continueBtn = document.getElementById("continueBtn");
    if (continueBtn) {
        continueBtn.classList.remove("hidden");
        continueBtn.style.display = "block";
        continueBtn.textContent = `Continue to Activity Filter (${name}) →`;
    }
}

function continueToActivity() {
    if (!selectedConnection) {
        alert("Please select a profile or connection to analyze.");
        return;
    }
    window.location.href = "/activity-filter";
}

async function handleConnectionsImport(event) {
    event.preventDefault();
    const fileInput = document.getElementById("connectionsImportFile");
    const statusMsg = document.getElementById("importStatusMsg");
    const submitBtn = document.getElementById("importSubmitBtn");

    if (!fileInput || !fileInput.files[0]) {
        alert("Please choose a Connections.csv or LinkedIn export ZIP file.");
        return;
    }

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Importing...";
    }

    if (statusMsg) {
        statusMsg.className = "import-status-msg info";
        statusMsg.textContent = "Validating and extracting your real LinkedIn connections...";
    }

    try {
        const res = await fetch("/api/connections/import", {
            method: "POST",
            body: formData
        });
        const data = await res.json();

        if (!res.ok) {
            throw new Error(data.error || "Failed to import connections.");
        }

        if (statusMsg) {
            statusMsg.className = "import-status-msg success";
            statusMsg.textContent = data.message || `Successfully loaded ${data.count || ''} real connections!`;
        }

        fileInput.value = "";
        await loadConnections();
    } catch (err) {
        if (statusMsg) {
            statusMsg.className = "import-status-msg error";
            statusMsg.textContent = err.message;
        }
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = "Import Connections";
        }
    }
}

async function clearAllConnections() {
    if (!confirm("Are you sure you want to clear all imported connections?")) {
        return;
    }

    try {
        const res = await fetch("/api/connections/clear", { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            const statusMsg = document.getElementById("importStatusMsg");
            if (statusMsg) {
                statusMsg.className = "import-status-msg info";
                statusMsg.textContent = "All imported connections cleared.";
            }
            selectedConnection = null;
            localStorage.removeItem("selectedSubjectId");
            localStorage.removeItem("selectedFriend");
            localStorage.removeItem("selectedSource");
            const continueBtn = document.getElementById("continueBtn");
            if (continueBtn) continueBtn.style.display = "none";
            await loadConnections();
        }
    } catch (err) {
        alert("Failed to clear connections: " + err.message);
    }
}

async function deleteConnection(connId, event) {
    event.stopPropagation();
    if (!confirm("Remove this connection from the list?")) return;

    try {
        const res = await fetch(`/api/connections/${connId}`, { method: "DELETE" });
        if (res.ok) {
            if (selectedConnection && selectedConnection.id === connId) {
                selectedConnection = null;
                const continueBtn = document.getElementById("continueBtn");
                if (continueBtn) continueBtn.style.display = "none";
            }
            await loadConnections();
        }
    } catch (err) {
        console.error("Failed to delete connection", err);
    }
}

function getInitials(name) {
    if (!name) return "IN";
    const parts = name.trim().split(/\s+/).filter(Boolean);
    if (parts.length >= 2) {
        const first = parts[0][0];
        const last = parts[parts.length - 1][0];
        return (first + last).toUpperCase();
    }
    return name.slice(0, 2).toUpperCase();
}

function getAvatarColor(name) {
    const palette = [
        "#0a66c2", "#057642", "#7c3aed", "#b45309", 
        "#0284c7", "#c026d3", "#0d9488", "#334155",
        "#4338ca", "#b91c1c", "#15803d", "#0369a1"
    ];
    let hash = 0;
    const str = name || "";
    for (let i = 0; i < str.length; i++) {
        hash = (hash << 5) - hash + str.charCodeAt(i);
        hash |= 0;
    }
    const idx = Math.abs(hash) % palette.length;
    return palette[idx];
}

function escapeQuotes(str) {
    return (str || "").replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function escapeHTML(str) {
    return (str || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}
