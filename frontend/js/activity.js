/**
 * Activity Filter Page Controller
 * Handles Step 3: displaying selected profile details, date filters, activity criteria,
 * and triggering analysis for the Dashboard.
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
        // Match DD/MM/YYYY
        const mDmy4 = s.match(/^(\d{1,2})[\/\.-](\d{1,2})[\/\.-](\d{4})$/);
        if (mDmy4) {
            return `${mDmy4[1].padStart(2, "0")}/${mDmy4[2].padStart(2, "0")}/${mDmy4[3]}`;
        }
        // Match DD/MM/YY
        const mDmy2 = s.match(/^(\d{1,2})[\/\.-](\d{1,2})[\/\.-](\d{2})$/);
        if (mDmy2) {
            return `${mDmy2[1].padStart(2, "0")}/${mDmy2[2].padStart(2, "0")}/20${mDmy2[3]}`;
        }
        // Match ISO YYYY-MM-DD
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

function formatLocalDateToDDMMYYYY(d) {
    if (!d || !(d instanceof Date) || isNaN(d.getTime())) return "";
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const yyyy = String(d.getFullYear());
    return `${dd}/${mm}/${yyyy}`;
}

function formatLocalDateToISO(d) {
    if (!d || !(d instanceof Date) || isNaN(d.getTime())) return "";
    const dd = String(d.getDate()).padStart(2, "0");
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const yyyy = String(d.getFullYear());
    return `${yyyy}-${mm}-${dd}`;
}

async function readActivityApiJson(response) {
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.toLowerCase().includes("application/json")) {
        throw new Error(`The analysis server returned an unexpected response (HTTP ${response.status}). Please retry; if it continues, check the Flask server log.`);
    }
    return response.json();
}

function parseDateStringToLocalDate(str) {
    if (!str) return null;
    const s = String(str).trim();
    // 1. DD/MM/YYYY
    const mDmy = s.match(/^(\d{1,2})[\/\.-](\d{1,2})[\/\.-](\d{4})$/);
    if (mDmy) {
        const day = parseInt(mDmy[1], 10);
        const month = parseInt(mDmy[2], 10) - 1;
        const year = parseInt(mDmy[3], 10);
        const d = new Date(year, month, day);
        if (!isNaN(d.getTime()) && d.getDate() === day && d.getMonth() === month && d.getFullYear() === year) {
            return d;
        }
        return null;
    }
    // 2. YYYY-MM-DD
    const mIso = s.match(/^(\d{4})[\/\.-](\d{1,2})[\/\.-](\d{1,2})$/);
    if (mIso) {
        const year = parseInt(mIso[1], 10);
        const month = parseInt(mIso[2], 10) - 1;
        const day = parseInt(mIso[3], 10);
        const d = new Date(year, month, day);
        if (!isNaN(d.getTime()) && d.getDate() === day && d.getMonth() === month && d.getFullYear() === year) {
            return d;
        }
        return null;
    }
    return null;
}

function convertUIValueToISO(val) {
    if (!val) return null;
    const d = parseDateStringToLocalDate(val);
    return d ? formatLocalDateToISO(d) : null;
}

function calculatePresetDates(preset) {
    const today = new Date();
    const end = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    let start = null;

    if (preset === "today") {
        // Today: 1 day inclusive (today to today)
        start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    } else if (preset === "7days") {
        // Last 7 Days: 7 days inclusive (today minus 6 days)
        start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6);
    } else if (preset === "10days") {
        // Last 10 Days: 10 days inclusive (today minus 9 days)
        start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 9);
    } else if (preset === "30days") {
        // Last 30 Days: 30 days inclusive (today minus 29 days, e.g. 04/11/2026 -> 06/10/2026)
        start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 29);
    } else if (preset === "1year" || preset === "365days") {
        // Last 1 Year: 365 days inclusive (today minus 364 days, up to 1 full previous year)
        start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 364);
    } else if (preset === "2years" || preset === "2year" || preset === "730days") {
        // Last 2 Years: 730 days inclusive (today minus 729 days, spanning the full 2-year LinkedIn visibility limit)
        start = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 729);
    } else if (preset === "older1year") {
        // The complete year before the most recent year.
        end.setFullYear(end.getFullYear() - 1);
        start = new Date(end);
        start.setFullYear(start.getFullYear() - 1);
    } else if (preset === "all") {
        return {
            start: null,
            end: null,
            startDDMMYYYY: "",
            endDDMMYYYY: "",
            startISO: null,
            endISO: null
        };
    } else {
        return {
            start: null,
            end: null,
            startDDMMYYYY: "",
            endDDMMYYYY: "",
            startISO: null,
            endISO: null
        };
    }

    return {
        start,
        end,
        startDDMMYYYY: formatLocalDateToDDMMYYYY(start),
        endDDMMYYYY: formatLocalDateToDDMMYYYY(end),
        startISO: formatLocalDateToISO(start),
        endISO: formatLocalDateToISO(end)
    };
}

let selectedProfile = {
    name: "LinkedIn Member",
    profile_url: "",
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

    const startInput = document.getElementById("startDate");
    const endInput = document.getElementById("endDate");
    if (startInput) {
        startInput.addEventListener("input", onCustomDateChange);
        startInput.addEventListener("change", onCustomDateChange);
    }
    if (endInput) {
        endInput.addEventListener("input", onCustomDateChange);
        endInput.addEventListener("change", onCustomDateChange);
    }
}

function updatePresetButtonUI(activePreset) {
    const buttons = document.querySelectorAll(".preset-btn");
    buttons.forEach(btn => {
        const presetCall = (btn.getAttribute("onclick") || "").match(/applyDatePreset\(['"]([^'"]+)['"]\)/);
        const match = Boolean(presetCall && presetCall[1] === activePreset);

        if (match) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });

    const radios = document.querySelectorAll('input[name="dateType"]');
    radios.forEach(r => {
        if (r.value === activePreset || (activePreset === "today" && r.value === "current")) {
            r.checked = true;
        } else {
            r.checked = false;
        }
    });
}

function onCustomDateChange() {
    const startVal = document.getElementById("startDate")?.value?.trim() || "";
    const endVal = document.getElementById("endDate")?.value?.trim() || "";
    const startNative = document.getElementById("startDate_native");
    const endNative = document.getElementById("endDate_native");

    const sIso = convertUIValueToISO(startVal);
    const eIso = convertUIValueToISO(endVal);
    if (startNative && sIso) startNative.value = sIso;
    if (endNative && eIso) endNative.value = eIso;

    let matchedPreset = "custom";
    if (!startVal && !endVal) {
        matchedPreset = "all";
    } else {
        const pToday = calculatePresetDates("today");
        const p7 = calculatePresetDates("7days");
        const p10 = calculatePresetDates("10days");
        const p30 = calculatePresetDates("30days");
        const p1year = calculatePresetDates("1year");
        const p2years = calculatePresetDates("2years");
        const pOlder1Year = calculatePresetDates("older1year");

        if (startVal === pToday.startDDMMYYYY && endVal === pToday.endDDMMYYYY) {
            matchedPreset = "today";
        } else if (startVal === p7.startDDMMYYYY && endVal === p7.endDDMMYYYY) {
            matchedPreset = "7days";
        } else if (startVal === p10.startDDMMYYYY && endVal === p10.endDDMMYYYY) {
            matchedPreset = "10days";
        } else if (startVal === p30.startDDMMYYYY && endVal === p30.endDDMMYYYY) {
            matchedPreset = "30days";
        } else if (startVal === p1year.startDDMMYYYY && endVal === p1year.endDDMMYYYY) {
            matchedPreset = "1year";
        } else if (startVal === p2years.startDDMMYYYY && endVal === p2years.endDDMMYYYY) {
            matchedPreset = "2years";
        } else if (startVal === pOlder1Year.startDDMMYYYY && endVal === pOlder1Year.endDDMMYYYY) {
            matchedPreset = "older1year";
        }
    }

    currentDatePreset = matchedPreset;
    updatePresetButtonUI(matchedPreset);
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
        urlEl.textContent = selectedProfile.profile_url || "Profile URL not specified";
        urlEl.href = selectedProfile.profile_url || "#";
    }

    if (headlineEl) {
        headlineEl.textContent = selectedProfile.headline || "LinkedIn Profile";
    }

    const photoEl = document.getElementById("profilePhotoDisplay");
    if (photoEl) {
        const pic = selectedProfile.picture;
        if (typeof pic === "string" && /^https?:\/\//.test(pic)) {
            photoEl.src = pic;
            photoEl.alt = `${selectedProfile.name || "LinkedIn member"} profile photo`;
            photoEl.style.display = "block";
            photoEl.onerror = () => { photoEl.style.display = "none"; };
        } else {
            photoEl.style.display = "none";
        }
    }

    if (badgeEl) {
        const src = selectedProfile.source || "real_linkedin";
        badgeEl.className = "badge-source " + (src === "imported" ? "badge-imported" : "badge-real");
        if (src === "imported") {
            badgeEl.textContent = "Imported from LinkedIn Connections export";
        } else if (selectedProfile.identity_source === "linkedin") {
            badgeEl.textContent = "Identified on LinkedIn";
        } else if (selectedProfile.identity_source === "url_guess") {
            badgeEl.textContent = "Name from URL (not verified)";
        } else {
            badgeEl.textContent = "Real LinkedIn Profile";
        }
    }
}

function applyDatePreset(preset) {
    currentDatePreset = preset;
    updatePresetButtonUI(preset);

    const startInput = document.getElementById("startDate");
    const endInput = document.getElementById("endDate");
    const startNative = document.getElementById("startDate_native");
    const endNative = document.getElementById("endDate_native");

    if (preset === "custom") {
        if (startInput) startInput.focus();
        return;
    }

    const dates = calculatePresetDates(preset);

    if (startInput) startInput.value = dates.startDDMMYYYY;
    if (endInput) endInput.value = dates.endDDMMYYYY;

    if (startNative) startNative.value = dates.startISO || "";
    if (endNative) endNative.value = dates.endISO || "";
}

function triggerCalendarPicker(inputId) {
    const textInput = document.getElementById(inputId);
    const nativePicker = document.getElementById(inputId + "_native");
    if (!nativePicker) return;

    if (textInput && textInput.value) {
        const iso = convertUIValueToISO(textInput.value);
        if (iso) nativePicker.value = iso;
    }

    try {
        if (typeof nativePicker.showPicker === "function") {
            nativePicker.showPicker();
        } else {
            nativePicker.focus();
            nativePicker.click();
        }
    } catch (err) {
        nativePicker.focus();
        nativePicker.click();
    }
}

function onNativeDatePicked(inputId, isoVal) {
    if (!isoVal) return;
    const parts = isoVal.split("-");
    if (parts.length === 3) {
        const formatted = `${parts[2]}/${parts[1]}/${parts[0]}`;
        const textInput = document.getElementById(inputId);
        if (textInput) {
            textInput.value = formatted;
        }
        onCustomDateChange();
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

    let startDateUI = document.getElementById("startDate")?.value?.trim() || "";
    let endDateUI = document.getElementById("endDate")?.value?.trim() || "";

    let startDateISO = null;
    let endDateISO = null;

    if (currentDatePreset === "all" || (!startDateUI && !endDateUI)) {
        startDateISO = null;
        endDateISO = null;
    } else {
        if (startDateUI) {
            startDateISO = convertUIValueToISO(startDateUI);
            if (!startDateISO) {
                showError("Please enter From Date in DD/MM/YYYY format (e.g. 04/11/2026).");
                return;
            }
        }
        if (endDateUI) {
            endDateISO = convertUIValueToISO(endDateUI);
            if (!endDateISO) {
                showError("Please enter To Date in DD/MM/YYYY format (e.g. 04/11/2026).");
                return;
            }
        }
    }

    if (startDateISO && endDateISO && startDateISO > endDateISO) {
        showError("From Date must be on or before To Date.");
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

    const targetUrl = (selectedProfile.profile_url || "").trim();
    if (!targetUrl || targetUrl === "https://www.linkedin.com/in/" || targetUrl === "https://www.linkedin.com/in" || targetUrl === "http://www.linkedin.com/in/") {
        showError("Incomplete LinkedIn profile URL. A profile URL with a valid username or slug is required.");
        return;
    }

    // Require a valid profile slug if it is a linkedin.com/in URL
    if (targetUrl.toLowerCase().includes("linkedin.com/in")) {
        const slugMatch = targetUrl.match(/linkedin\.com\/in\/([a-zA-Z0-9_\-\%]+)/i);
        if (!slugMatch || !slugMatch[1].trim() || slugMatch[1].trim().toLowerCase() === "in") {
            showError("Invalid LinkedIn profile URL. Must include a profile username or slug (e.g. https://www.linkedin.com/in/username).");
            return;
        }
    }

    const payload = {
        subject_id: selectedProfile.subject_id || selectedProfile.username || "self",
        subject_name: selectedProfile.name || "LinkedIn Member",
        subject_url: targetUrl,
        headline: selectedProfile.headline || "LinkedIn Profile",
        data_source: selectedProfile.source || "real_linkedin",
        dateType: currentDatePreset,
        startDate: startDateISO,
        endDate: endDateISO,
        activities: activities,
        page: 1,
        per_page: 20
    };

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Analyzing Activity...";
    }

    startYouTubeProgressBar("Connecting to Chrome (5%)...");

    try {
        const response = await fetch("/api/analyze", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(payload)
        });

        const result = await readActivityApiJson(response);

        if (!response.ok) {
            throw new Error(result.error || "Activity analysis failed.");
        }

        completeYouTubeProgressBar();

        try {
            sessionStorage.setItem("selectedProfile", JSON.stringify(selectedProfile));
            sessionStorage.setItem("currentAnalysis", JSON.stringify(result));
            localStorage.setItem("activityFilter", JSON.stringify(payload));
            sessionStorage.setItem("hasLatestAnalysis", "true");
        } catch (storageErr) {
            console.warn("Storage warning; relying on backend /api/analysis/latest", storageErr);
        }

        // Navigate to Dashboard
        setTimeout(() => {
            window.location.href = "/dashboard";
        }, 700);

    } catch (err) {
        const isNetworkError = (err instanceof TypeError && err.message &&
            (err.message.toLowerCase().includes("fetch") ||
             err.message.toLowerCase().includes("network") ||
             err.message.toLowerCase().includes("failed")));
        const msg = isNetworkError
            ? "⚠️ Could not reach the backend server. Please make sure the Flask server (python backend/app.py) is running, then try again."
            : err.message;
        errorYouTubeProgressBar(msg);
        showError(msg);
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
