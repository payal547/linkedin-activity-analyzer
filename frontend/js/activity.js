/**
 * Activity Filter Page Controller
 * Handles Step 3: displaying selected profile details, date filters, activity criteria,
 * and triggering analysis for the Dashboard.
 */

let selectedProfile = {
    name: "LinkedIn Member",
    profile_url: "https://www.linkedin.com/in/",
    headline: "LinkedIn Profile",
    username: "",
    subject_id: "self",
    source: "real_linkedin"
};

let currentDatePreset = "30days";

document.addEventListener("DOMContentLoaded", () => {
    initActivityFilter();
});

async function initActivityFilter() {
    // 1. Try loading profile from sessionStorage
    const stored = sessionStorage.getItem("selectedProfile");
    if (stored) {
        try {
            selectedProfile = { ...selectedProfile, ...JSON.parse(stored) };
        } catch (e) {
            console.error("Error parsing stored profile:", e);
        }
    }

    // 2. Supplement with localStorage
    const friendName = localStorage.getItem("selectedFriend");
    const profileUrl = localStorage.getItem("selectedProfileUrl");
    const headline = localStorage.getItem("selectedHeadline");
    const subjectId = localStorage.getItem("selectedSubjectId");
    const source = localStorage.getItem("selectedSource");

    if (friendName) selectedProfile.name = friendName;
    if (profileUrl) selectedProfile.profile_url = profileUrl;
    if (headline) selectedProfile.headline = headline;
    if (subjectId) selectedProfile.subject_id = subjectId;
    if (source) selectedProfile.source = source;

    // 3. If still missing details, attempt to fetch from backend selected profile
    if (!profileUrl && !friendName) {
        try {
            const res = await fetch("/api/profile/selected");
            if (res.ok) {
                const data = await res.json();
                selectedProfile = { ...selectedProfile, ...data };
            }
        } catch (e) {
            console.warn("Could not fetch selected profile from backend:", e);
        }
    }

    // Render profile details
    renderSelectedProfile();

    // Default to Last 30 Days preset
    applyDatePreset("30days");
}

function renderSelectedProfile() {
    const nameEl = document.getElementById("profileNameDisplay");
    const friendNameHidden = document.getElementById("friendName");
    const urlEl = document.getElementById("profileUrlDisplay");
    const headlineEl = document.getElementById("profileHeadlineDisplay");
    const badgeEl = document.getElementById("sourceBadge");

    if (nameEl) nameEl.textContent = selectedProfile.name || "LinkedIn Member";
    if (friendNameHidden) friendNameHidden.textContent = selectedProfile.name || "LinkedIn Member";

    if (urlEl) {
        urlEl.textContent = selectedProfile.profile_url || "https://www.linkedin.com/";
        urlEl.href = selectedProfile.profile_url || "#";
    }

    if (headlineEl) {
        headlineEl.textContent = selectedProfile.headline || "LinkedIn Profile";
    }

    if (badgeEl) {
        const src = selectedProfile.source || "real_linkedin";
        badgeEl.className = "badge-source " + (src === "imported" ? "badge-imported" : "badge-real");
        badgeEl.textContent = src === "imported" ? "Imported from LinkedIn Connections export" : "Real LinkedIn Profile";
    }
}

function applyDatePreset(preset) {
    currentDatePreset = preset;

    // Update preset button active state
    const buttons = document.querySelectorAll(".preset-btn");
    buttons.forEach(btn => {
        const text = btn.textContent.toLowerCase();
        if ((preset === "all" && text.includes("all")) ||
            (preset === "today" && text.includes("today")) ||
            (preset === "7days" && text.includes("7")) ||
            (preset === "30days" && text.includes("30"))) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });

    // Update hidden radio inputs for test compatibility
    const radios = document.querySelectorAll('input[name="dateType"]');
    radios.forEach(r => {
        if (r.value === preset || (preset === "today" && r.value === "current")) {
            r.checked = true;
        }
    });

    const startInput = document.getElementById("startDate");
    const endInput = document.getElementById("endDate");

    const today = new Date();
    const todayStr = today.toISOString().slice(0, 10);

    if (preset === "all") {
        if (startInput) startInput.value = "";
        if (endInput) endInput.value = "";
    } else if (preset === "today") {
        if (startInput) startInput.value = todayStr;
        if (endInput) endInput.value = todayStr;
    } else if (preset === "7days") {
        const d = new Date();
        d.setDate(d.getDate() - 7);
        if (startInput) startInput.value = d.toISOString().slice(0, 10);
        if (endInput) endInput.value = todayStr;
    } else if (preset === "30days") {
        const d = new Date();
        d.setDate(d.getDate() - 30);
        if (startInput) startInput.value = d.toISOString().slice(0, 10);
        if (endInput) endInput.value = todayStr;
    }
}

function toggleAllActivities(masterCheckbox) {
    const isChecked = masterCheckbox.checked;
    const checkboxes = document.querySelectorAll(".activity-checkbox");
    checkboxes.forEach(cb => {
        cb.checked = isChecked;
    });
}

function onSingleActivityChange() {
    const checkboxes = document.querySelectorAll(".activity-checkbox");
    const master = document.getElementById("checkAllActivities");
    const allChecked = Array.from(checkboxes).every(cb => cb.checked);
    if (master) {
        master.checked = allChecked;
    }
}

async function analyzeActivity() {
    const errorEl = document.getElementById("errorMessage");
    const submitBtn = document.getElementById("analyzeBtn");
    if (errorEl) errorEl.style.display = "none";

    const startDate = document.getElementById("startDate")?.value || null;
    const endDate = document.getElementById("endDate")?.value || null;

    if (startDate && endDate && startDate > endDate) {
        showError("Ending date must be on or after the starting date.");
        return;
    }

    // Gather checked activities
    const masterAll = document.getElementById("checkAllActivities")?.checked;
    let activities = [];

    if (masterAll) {
        activities = ["all"];
    } else {
        const checked = document.querySelectorAll(".activity-checkbox:checked");
        activities = Array.from(checked).map(cb => cb.value);
    }

    if (activities.length === 0) {
        showError("Please select at least one activity type (posts, comments, etc.).");
        return;
    }

    const payload = {
        subject_id: selectedProfile.subject_id || selectedProfile.username || "self",
        subject_name: selectedProfile.name || "LinkedIn Member",
        subject_url: selectedProfile.profile_url || null,
        headline: selectedProfile.headline || "LinkedIn Profile",
        data_source: selectedProfile.source || "real_linkedin",
        dateType: currentDatePreset,
        startDate: startDate,
        endDate: endDate,
        activities: activities
    };

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Analyzing Activity...";
    }

    try {
        const response = await fetch("/api/analyze", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(payload)
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || "Activity analysis failed.");
        }

        // Store analysis result in sessionStorage for instant rendering on Dashboard
        sessionStorage.setItem("currentAnalysis", JSON.stringify(result));
        sessionStorage.setItem("selectedProfile", JSON.stringify(selectedProfile));
        localStorage.setItem("activityFilter", JSON.stringify(payload));

        // Navigate to Dashboard
        window.location.href = "/dashboard";

    } catch (err) {
        showError(err.message);
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = "Analyze Activity →";
        }
    }
}

function showError(msg) {
    const errorEl = document.getElementById("errorMessage");
    if (errorEl) {
        errorEl.textContent = msg;
        errorEl.style.display = "block";
    }
}
