// Shared page behaviour: marks the current section in the top navigation.
(function () {
    // Pages reached from the analyzer flow highlight "Analyzer".
    const sectionFor = {
        "/": "/",
        "/activity-filter": "/",
        "/dashboard": "/",
        "/friend": "/connections",
        "/login": "/connections",
    };

    function markCurrentNav() {
        const path = window.location.pathname.replace(/\/+$/, "") || "/";
        const current = sectionFor[path] || path;
        document.querySelectorAll(".topnav nav a").forEach((link) => {
            const target = new URL(link.href, window.location.origin).pathname.replace(/\/+$/, "") || "/";
            if (target === current) link.setAttribute("aria-current", "page");
            else link.removeAttribute("aria-current");
        });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", markCurrentNav);
    else markCurrentNav();
})();
