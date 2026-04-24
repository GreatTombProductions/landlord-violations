/**
 * Philadelphia Landlord Violations — Client-side search and display
 *
 * Architecture:
 *   - stats.json loaded on page init (~10KB)
 *   - search/{letter}.json loaded on demand when user types (~50-200KB each)
 *   - owners/{hash}.json loaded on demand when user clicks a result (~1-50KB)
 *   - All data cached in memory after first fetch
 */

(function () {
    "use strict";

    // --- State ---
    const cache = {
        search: {},    // letter -> owner array
        owners: {},    // hash -> owner profile
        stats: null,
    };

    let currentResults = [];
    let debounceTimer = null;

    // --- DOM refs ---
    const searchInput = document.getElementById("search-input");
    const searchStatus = document.getElementById("search-status");
    const resultsEl = document.getElementById("results");
    const ownerDetailEl = document.getElementById("owner-detail");
    const backBtn = document.getElementById("back-btn");
    const ownerHeader = document.getElementById("owner-header");
    const propertyList = document.getElementById("property-list");
    const statsPanel = document.getElementById("stats-panel");
    const statsGrid = document.getElementById("stats-grid");
    const topViolatorsEl = document.getElementById("top-violators");
    const violationTypesEl = document.getElementById("violation-types");

    // --- Data fetching ---
    const BASE = new URL("data/", window.location.href).href;

    async function fetchJSON(path) {
        const url = new URL(path, BASE).href;
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`Failed to fetch ${path}: ${resp.status}`);
        return resp.json();
    }

    async function loadSearchChunk(letter) {
        const key = letter.toUpperCase();
        if (cache.search[key]) return cache.search[key];
        const data = await fetchJSON(`search/${key}.json`);
        cache.search[key] = data;
        return data;
    }

    async function loadOwnerProfile(hash) {
        if (cache.owners[hash]) return cache.owners[hash];
        // Owner profiles are chunked by first 2 chars of hash
        const prefix = hash.slice(0, 2);
        const chunkKey = "owner_chunk_" + prefix;
        if (!cache[chunkKey]) {
            const chunk = await fetchJSON(`owners/${prefix}.json`);
            cache[chunkKey] = chunk;
            // Cache individual profiles from this chunk
            for (const [h, profile] of Object.entries(chunk)) {
                cache.owners[h] = profile;
            }
        }
        return cache.owners[hash];
    }

    // --- Formatting ---
    function fmtNumber(n) {
        if (!n) return "0";
        return n.toLocaleString("en-US");
    }

    function fmtDate(d) {
        if (!d) return "";
        const parts = d.split("-");
        if (parts.length >= 3) return parts[1] + "/" + parts[2] + "/" + parts[0];
        return d;
    }

    function fmtMoney(n) {
        if (!n || n === 0) return "$0";
        if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
        if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K";
        return "$" + n.toLocaleString("en-US", { maximumFractionDigits: 0 });
    }

    function escapeHtml(s) {
        if (!s) return "";
        const div = document.createElement("div");
        div.textContent = s;
        return div.innerHTML;
    }

    // --- Search ---
    function getSearchKey(query) {
        const first = query.trim().charAt(0).toUpperCase();
        if (first >= "A" && first <= "Z") return first;
        if (first >= "0" && first <= "9") return "0";
        return "_";
    }

    function matchScore(name, query) {
        const nameLower = (name || "").toLowerCase();
        const queryLower = query.toLowerCase();

        if (nameLower === queryLower) return 1000;
        if (nameLower.startsWith(queryLower)) return 100;

        // Word-start matching
        const words = nameLower.split(/\s+/);
        const queryWords = queryLower.split(/\s+/);
        let wordMatches = 0;
        for (const qw of queryWords) {
            for (const w of words) {
                if (w.startsWith(qw)) { wordMatches++; break; }
            }
        }
        if (wordMatches === queryWords.length) return 50 + wordMatches;
        if (wordMatches > 0) return 10 + wordMatches;

        // Substring
        if (nameLower.includes(queryLower)) return 5;

        return 0;
    }

    function highlightMatch(text, query) {
        const idx = text.toLowerCase().indexOf(query.toLowerCase());
        if (idx === -1) return escapeHtml(text);
        const before = escapeHtml(text.slice(0, idx));
        const match = escapeHtml(text.slice(idx, idx + query.length));
        const after = escapeHtml(text.slice(idx + query.length));
        return `${before}<mark>${match}</mark>${after}`;
    }

    async function doSearch(query) {
        query = query.trim();
        if (query.length < 2) {
            showStats();
            return;
        }

        searchStatus.textContent = "Searching...";
        statsPanel.classList.add("hidden");
        ownerDetailEl.classList.add("hidden");
        resultsEl.innerHTML = "";

        try {
            const key = getSearchKey(query);
            const chunk = await loadSearchChunk(key);

            // Search entries: [display_name, hash, property_count, violation_count, flags?]
            const scored = [];
            for (const entry of chunk) {
                const score = matchScore(entry[0], query);
                if (score > 0) {
                    scored.push({ entry, score });
                }
            }

            scored.sort((a, b) => {
                if (b.score !== a.score) return b.score - a.score;
                return (b.entry[3] || 0) - (a.entry[3] || 0);  // by violations desc
            });

            currentResults = scored.slice(0, 100);
            renderResults(query);

        } catch (err) {
            searchStatus.textContent = "Search error. Try again.";
            console.error("Search error:", err);
        }
    }

    function parseFlags(flagStr) {
        if (!flagStr) return {};
        const flags = {};
        for (const part of flagStr.split("|")) {
            if (part === "g") { flags.govt = true; continue; }
            const [k, v] = part.split(":");
            if (k === "o") flags.open = parseInt(v);
            if (k === "i") flags.imminent = parseInt(v);
            if (k === "h") flags.hazardous = parseInt(v);
        }
        return flags;
    }

    function renderResults(query) {
        const count = currentResults.length;

        if (count === 0) {
            searchStatus.textContent = "No owners found matching that name.";
            resultsEl.innerHTML = "";
            return;
        }

        searchStatus.textContent = `${count} owner${count !== 1 ? "s" : ""} found`;
        resultsEl.innerHTML = "";

        for (const { entry } of currentResults) {
            const [name, hash, propCount, violCount, flagStr] = entry;
            const flags = parseFlags(flagStr);

            const card = document.createElement("div");
            card.className = "result-card" + (flags.govt ? " govt" : "");
            card.setAttribute("role", "button");
            card.setAttribute("tabindex", "0");

            const displayName = highlightMatch(name, query);

            // Severity badges
            let badges = "";
            if (flags.imminent) {
                badges += `<span class="badge danger">${flags.imminent} imminent danger</span>`;
            }
            if (flags.hazardous) {
                badges += `<span class="badge warning">${flags.hazardous} hazardous</span>`;
            }
            if (flags.open) {
                badges += `<span class="badge open">${flags.open} open</span>`;
            }
            if (flags.govt) {
                badges += `<span class="badge govt-badge">Government</span>`;
            }

            card.innerHTML = `
                <div class="result-name">${displayName}</div>
                <div class="result-meta">
                    <span class="violations">${fmtNumber(violCount)} violation${violCount !== 1 ? "s" : ""}</span>
                    <span class="properties">${fmtNumber(propCount)} propert${propCount !== 1 ? "ies" : "y"}</span>
                </div>
                ${badges ? `<div class="result-badges">${badges}</div>` : ""}
            `;

            card.addEventListener("click", () => showOwnerDetail(hash));
            card.addEventListener("keydown", (e) => {
                if (e.key === "Enter") showOwnerDetail(hash);
            });

            resultsEl.appendChild(card);
        }
    }

    // --- Owner Detail ---
    async function showOwnerDetail(hash) {
        resultsEl.innerHTML = "";
        statsPanel.classList.add("hidden");
        ownerDetailEl.classList.remove("hidden");
        ownerHeader.innerHTML = '<div class="loading">Loading owner profile...</div>';
        propertyList.innerHTML = "";

        try {
            const profile = await loadOwnerProfile(hash);
            renderOwnerProfile(profile);
        } catch (err) {
            ownerHeader.innerHTML = '<p class="error">Error loading owner profile.</p>';
            console.error("Profile error:", err);
        }
    }

    function renderOwnerProfile(profile) {
        const name = profile.n;
        const isGovt = profile.g;

        // Summary stats
        ownerHeader.innerHTML = `
            <h2>${escapeHtml(name)}${isGovt ? ' <span class="badge govt-badge">Government Entity</span>' : ""}</h2>
            <div class="owner-summary">
                <div class="summary-stat">
                    <div class="value">${fmtNumber(profile.tv)}</div>
                    <div class="label">Total Violations</div>
                </div>
                <div class="summary-stat">
                    <div class="value">${fmtNumber(profile.pc)}</div>
                    <div class="label">Properties</div>
                </div>
                ${profile.ov ? `
                <div class="summary-stat">
                    <div class="value open-value">${fmtNumber(profile.ov)}</div>
                    <div class="label">Open Violations</div>
                </div>` : ""}
                ${profile.ic ? `
                <div class="summary-stat">
                    <div class="value danger-value">${fmtNumber(profile.ic)}</div>
                    <div class="label">Imminent Danger</div>
                </div>` : ""}
                ${profile.hc ? `
                <div class="summary-stat">
                    <div class="value warning-value">${fmtNumber(profile.hc)}</div>
                    <div class="label">Hazardous</div>
                </div>` : ""}
            </div>
            ${profile.fd && profile.ld ? `
            <div class="owner-dates">
                Violations from ${fmtDate(profile.fd)} to ${fmtDate(profile.ld)}
            </div>` : ""}
        `;

        // Property list
        propertyList.innerHTML = "";
        const props = profile.props || [];

        for (const prop of props) {
            const propCard = document.createElement("div");
            propCard.className = "property-card";

            // Property header
            let propMeta = [];
            if (prop.z) propMeta.push(prop.z);
            if (prop.pt) propMeta.push(prop.pt);
            if (prop.mv) propMeta.push("Market value: " + fmtMoney(prop.mv));
            if (prop.sf) propMeta.push(fmtNumber(prop.sf) + " sqft");
            if (prop.bd) propMeta.push(prop.bd + " bed");

            // Severity indicators for this property
            let propBadges = "";
            if (prop.i) propBadges += `<span class="badge danger">${prop.i} imminent</span>`;
            if (prop.h) propBadges += `<span class="badge warning">${prop.h} hazardous</span>`;
            if (prop.o) propBadges += `<span class="badge open">${prop.o} open</span>`;

            // Violations list (collapsed by default)
            const violations = prop.vl || [];
            let violationsHtml = "";
            if (violations.length > 0) {
                violationsHtml = `
                    <div class="violations-toggle" data-expanded="false">
                        <button class="toggle-btn">Show ${violations.length} violation${violations.length !== 1 ? "s" : ""}</button>
                    </div>
                    <div class="violations-list hidden">
                        ${violations.map(v => `
                            <div class="violation-row${v.p === "IMMINENTLY DANGEROUS" ? " danger-row" : v.p === "HAZARDOUS" ? " warning-row" : ""}">
                                <span class="v-date">${fmtDate(v.d)}</span>
                                <span class="v-desc">${escapeHtml(v.t || v.c || "")}</span>
                                ${v.p && v.p !== "NON HAZARDOUS" ? `<span class="v-priority badge ${v.p === "IMMINENTLY DANGEROUS" ? "danger" : "warning"}">${escapeHtml(v.p)}</span>` : ""}
                                ${v.s ? `<span class="v-status badge open">${escapeHtml(v.s)}</span>` : ""}
                            </div>
                        `).join("")}
                    </div>
                `;
            }

            propCard.innerHTML = `
                <div class="property-header">
                    <div class="property-address">${escapeHtml(prop.a || "Unknown address")}</div>
                    <div class="property-violations">${fmtNumber(prop.v)} violation${prop.v !== 1 ? "s" : ""}</div>
                </div>
                ${propMeta.length ? `<div class="property-meta">${propMeta.map(m => escapeHtml(m)).join(" &middot; ")}</div>` : ""}
                ${propBadges ? `<div class="property-badges">${propBadges}</div>` : ""}
                ${prop.fd && prop.ld ? `<div class="property-dates">${fmtDate(prop.fd)} &ndash; ${fmtDate(prop.ld)}</div>` : ""}
                ${violationsHtml}
            `;

            // Toggle violations
            const toggle = propCard.querySelector(".violations-toggle");
            if (toggle) {
                const btn = toggle.querySelector(".toggle-btn");
                const list = propCard.querySelector(".violations-list");
                btn.addEventListener("click", () => {
                    const expanded = toggle.dataset.expanded === "true";
                    toggle.dataset.expanded = !expanded;
                    list.classList.toggle("hidden");
                    btn.textContent = expanded
                        ? `Show ${violations.length} violation${violations.length !== 1 ? "s" : ""}`
                        : "Hide violations";
                });
            }

            propertyList.appendChild(propCard);
        }

        // Map link if coordinates available
        const propsWithCoords = props.filter(p => p.ll);
        if (propsWithCoords.length > 0) {
            const firstCoord = propsWithCoords[0].ll;
            const mapLink = document.createElement("div");
            mapLink.className = "map-link";
            mapLink.innerHTML = `<a href="https://www.google.com/maps?q=${firstCoord[0]},${firstCoord[1]}" target="_blank" rel="noopener">View on Google Maps</a>`;
            ownerHeader.appendChild(mapLink);
        }
    }

    // --- Stats (Landing Page) ---
    function showStats() {
        resultsEl.innerHTML = "";
        ownerDetailEl.classList.add("hidden");
        searchStatus.textContent = "";

        if (!cache.stats) {
            statsPanel.classList.add("hidden");
            return;
        }

        statsPanel.classList.remove("hidden");

        const s = cache.stats;
        statsGrid.innerHTML = `
            <div class="stat-card">
                <div class="stat-value">${fmtNumber(s.total_violations)}</div>
                <div class="stat-label">Housing Code Violations</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${fmtNumber(s.private_owners)}</div>
                <div class="stat-label">Property Owners</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${fmtNumber(s.total_properties)}</div>
                <div class="stat-label">Properties</div>
            </div>
            <div class="stat-card priority-card">
                <div class="stat-value danger-value">${fmtNumber(s.priority?.["IMMINENTLY DANGEROUS"] || 0)}</div>
                <div class="stat-label">Imminently Dangerous</div>
            </div>
        `;

        // Top violators
        if (s.top_violators && s.top_violators.length) {
            let html = '<h3>Top Private Violators</h3>';
            html += '<div class="violators-table">';
            for (const v of s.top_violators.slice(0, 10)) {
                const hash = v.hash;
                html += `
                    <div class="violator-row" data-hash="${escapeHtml(hash)}" role="button" tabindex="0">
                        <span class="vr-name">${escapeHtml(v.n)}</span>
                        <span class="vr-violations">${fmtNumber(v.v)} violations</span>
                        <span class="vr-properties">${fmtNumber(v.p)} properties</span>
                        ${v.i ? `<span class="badge danger">${v.i} imminent</span>` : ""}
                    </div>
                `;
            }
            html += '</div>';
            topViolatorsEl.innerHTML = html;

            // Click handlers for top violators
            topViolatorsEl.querySelectorAll(".violator-row").forEach(row => {
                row.addEventListener("click", () => {
                    const hash = row.dataset.hash;
                    if (hash) showOwnerDetail(hash);
                });
                row.addEventListener("keydown", (e) => {
                    if (e.key === "Enter") {
                        const hash = row.dataset.hash;
                        if (hash) showOwnerDetail(hash);
                    }
                });
            });
        }

        // Violation types
        if (s.violation_types && s.violation_types.length) {
            let html = '<h3>Most Common Violations</h3>';
            const maxCount = s.violation_types[0].c;
            for (const vt of s.violation_types.slice(0, 10)) {
                const pct = Math.round((vt.c / maxCount) * 100);
                html += `
                    <div class="vtype-row">
                        <div class="vtype-name">${escapeHtml(vt.t)}</div>
                        <div class="vtype-bar-container">
                            <div class="vtype-bar" style="width:${pct}%"></div>
                            <span class="vtype-count">${fmtNumber(vt.c)}</span>
                        </div>
                    </div>
                `;
            }
            violationTypesEl.innerHTML = html;
        }
    }

    // --- Event handlers ---
    searchInput.addEventListener("input", () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => doSearch(searchInput.value), 200);
    });

    backBtn.addEventListener("click", () => {
        ownerDetailEl.classList.add("hidden");
        if (searchInput.value.trim().length >= 2) {
            renderResults(searchInput.value);
        } else {
            showStats();
        }
    });

    // --- Init ---
    async function init() {
        try {
            cache.stats = await fetchJSON("stats.json");
            showStats();
        } catch (err) {
            console.error("Failed to load stats:", err);
            searchStatus.textContent = "Loading data...";
        }
    }

    init();
})();
