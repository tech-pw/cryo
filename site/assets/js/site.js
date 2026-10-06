/* Cryo site runtime: chrome, theme, search, on-this-page TOC, copy buttons.
 *
 * Loaded after charts.js. Each behaviour is encapsulated; nothing here
 * assumes a particular page layout beyond the [data-page] attribute on
 * <body> and the standard shell markup populated by this same script.
 */
(function () {
  const script = document.querySelector('script[src*="assets/js/site.js"]');
  const root = new URL("../../", script.src);
  const page = document.body.dataset.page || "";

  // --- nav definition -----------------------------------------------------
  const groups = [
    { label: "Start", items: [
      ["home", "Introduction", "index.html", "01"],
      ["quickstart", "Quickstart", "quickstart/index.html", "02"],
    ]},
    { label: "Concepts", items: [
      ["concepts", "How Cryo fits", "concepts/index.html", "03"],
      ["architecture", "Architecture", "architecture/index.html", "04"],
    ]},
    { label: "Operate", items: [
      ["maintenance", "Maintenance", "maintenance/index.html", "05"],
      ["health", "Health and cost", "health/index.html", "06"],
      ["engines", "Engines", "engines/index.html", "07"],
    ]},
    { label: "Run it", items: [
      ["deployment", "Deployment", "deployment/index.html", "08"],
      ["reference", "Reference", "reference/index.html", "09"],
    ]},
  ];

  // --- top bar ------------------------------------------------------------
  const top = document.getElementById("top");
  const side = document.getElementById("side");
  const foot = document.getElementById("foot");

  if (top) {
    top.className = "top";
    top.innerHTML = `
      <a class="wordmark" href="${new URL("index.html", root)}" aria-label="Cryo — home">
        Cryo<span class="wordmark-dot" aria-hidden="true"></span>
      </a>
      <nav class="top-links" aria-label="Site">
        <a href="${new URL("quickstart/index.html", root)}">Quickstart</a>
        <a href="${new URL("architecture/index.html", root)}">Architecture</a>
        <a href="${new URL("reference/index.html", root)}">Reference</a>
        <a class="gh-badge" href="https://github.com/tech-pw/cryo" aria-label="Cryo on GitHub">
          <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8a8 8 0 005.47 7.59c.4.07.55-.17.55-.38v-1.34c-2.23.48-2.7-1.07-2.7-1.07-.36-.92-.89-1.16-.89-1.16-.73-.5.06-.49.06-.49.8.06 1.22.83 1.22.83.72 1.23 1.88.87 2.34.67.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.13 0 0 .67-.22 2.2.82a7.6 7.6 0 014 0c1.53-1.04 2.2-.82 2.2-.82.44 1.11.16 1.93.08 2.13.51.56.82 1.28.82 2.15 0 3.07-1.87 3.74-3.65 3.94.29.25.54.74.54 1.5v2.22c0 .21.15.46.55.38A8 8 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
          GitHub
        </a>
        <button class="theme-toggle" type="button" aria-pressed="false" aria-label="Toggle theme">Dark</button>
        <button class="menu" type="button" aria-expanded="false" aria-controls="side">Docs</button>
      </nav>`;
    const menu = top.querySelector(".menu");
    menu.addEventListener("click", () => {
      const open = document.body.classList.toggle("nav-open");
      menu.setAttribute("aria-expanded", open ? "true" : "false");
    });

    const themeButton = top.querySelector(".theme-toggle");
    const stored = window.localStorage.getItem("cryo-site-theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const setTheme = (theme) => {
      document.body.dataset.theme = theme;
      themeButton.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
      themeButton.textContent = theme === "dark" ? "Light" : "Dark";
      window.localStorage.setItem("cryo-site-theme", theme);
      // Sync archify iframes if present, only if the URL would actually change.
      document.querySelectorAll("iframe.archify-frame").forEach((frame) => {
        try {
          const u = new URL(frame.src, window.location.href);
          if (u.searchParams.get("theme") === (theme === "dark" ? "dark" : "light")) return;
          u.searchParams.set("theme", theme === "dark" ? "dark" : "light");
          frame.src = u.toString();
        } catch (_) {}
      });
    };
    const initialTheme = stored === "dark" || stored === "light" ? stored : (prefersDark ? "dark" : "light");
    document.body.dataset.theme = initialTheme;
    // Set iframe themes up front (no double-load), so theme="light" prefilled iframes
    // don't request again on first paint.
    document.querySelectorAll("iframe.archify-frame").forEach((frame) => {
      try {
        const u = new URL(frame.src, window.location.href);
        u.searchParams.set("theme", initialTheme === "dark" ? "dark" : "light");
        if (frame.src !== u.toString()) frame.src = u.toString();
      } catch (_) {}
    });
    setTimeout(() => {
      themeButton.setAttribute("aria-pressed", initialTheme === "dark" ? "true" : "false");
      themeButton.textContent = initialTheme === "dark" ? "Light" : "Dark";
    }, 0);
    themeButton.addEventListener("click", () => setTheme(document.body.dataset.theme === "dark" ? "light" : "dark"));
  }

  // --- side nav -----------------------------------------------------------
  if (side) {
    side.className = "side";
    side.innerHTML = `
      <input class="search" type="search" placeholder="Search docs" aria-label="Search docs" autocomplete="off" spellcheck="false">
      <div class="search-results" role="listbox" aria-label="Search results"></div>`;
    groups.forEach((group) => {
      const block = document.createElement("div");
      block.className = "nav-group";
      const label = document.createElement("p");
      label.className = "nav-label";
      label.textContent = group.label;
      block.appendChild(label);
      group.items.forEach(([id, text, href, num]) => {
        const link = document.createElement("a");
        link.className = "nav-link";
        link.href = new URL(href, root).href;
        link.dataset.label = text.toLowerCase();
        link.dataset.section = id;
        const labelEl = document.createElement("span");
        labelEl.textContent = text;
        link.appendChild(labelEl);
        if (num) {
          const numEl = document.createElement("span");
          numEl.className = "num";
          numEl.textContent = num;
          link.appendChild(numEl);
        }
        if (id === page) link.setAttribute("aria-current", "page");
        block.appendChild(link);
      });
      side.appendChild(block);
    });

    const searchInput = side.querySelector(".search");
    const searchResults = side.querySelector(".search-results");
    if (searchInput && searchResults) {
      let indexPromise = null;
      const loadIndex = () => {
        if (!indexPromise) {
          indexPromise = fetch(new URL("assets/js/index.json", root).href).then((r) => r.json());
        }
        return indexPromise;
      };
      const score = (doc, tokens) => {
        const haystack = (doc.title + " " + doc.lede + " " + doc.headings.map((h) => h.text).join(" ")).toLowerCase();
        let s = 0;
        let allHit = true;
        for (const t of tokens) {
          if (!haystack.includes(t)) { allHit = false; break; }
          s += (haystack.match(new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g")) || []).length;
        }
        return allHit ? s : 0;
      };
      const highlight = (text, tokens) => {
        let out = text;
        tokens.forEach((t) => {
          out = out.replace(new RegExp(`(${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig"), "<mark>$1</mark>");
        });
        return out;
      };
      let activeIndex = 0;
      let currentResults = [];
      const render = (docs, tokens) => {
        searchResults.innerHTML = "";
        if (!docs.length) {
          searchResults.classList.remove("active");
          return;
        }
        searchResults.classList.add("active");
        docs.slice(0, 8).forEach((doc, index) => {
          const a = document.createElement("a");
          a.href = new URL(doc.url, root).href;
          a.setAttribute("role", "option");
          a.dataset.index = String(index);
          const title = document.createElement("strong");
          title.innerHTML = highlight(doc.title, tokens);
          const meta = document.createElement("div");
          meta.className = "result-meta";
          meta.innerHTML = highlight(doc.section + (doc.lede ? " — " + doc.lede.slice(0, 80) : ""), tokens);
          a.appendChild(title);
          a.appendChild(document.createElement("br"));
          a.appendChild(meta);
          if (index === activeIndex) a.classList.add("active");
          searchResults.appendChild(a);
        });
        currentResults = Array.from(searchResults.querySelectorAll("a"));
      };
      const closeResults = () => {
        searchResults.classList.remove("active");
        activeIndex = 0;
        currentResults = [];
      };
      searchInput.addEventListener("input", async (event) => {
        const raw = event.target.value.trim().toLowerCase();
        // hide nav links that don't match label
        side.querySelectorAll(".nav-link").forEach((link) => {
          link.classList.toggle("is-hidden", raw.length > 0 && !link.dataset.label.includes(raw));
        });
        if (!raw) { closeResults(); return; }
        const tokens = raw.split(/\s+/).filter(Boolean);
        try {
          const index = await loadIndex();
          const ranked = index.docs
            .map((d) => ({ d, s: score(d, tokens) }))
            .filter((x) => x.s > 0)
            .sort((a, b) => b.s - a.s)
            .map((x) => x.d);
          render(ranked, tokens);
        } catch (_) {
          closeResults();
        }
      });
      searchInput.addEventListener("keydown", (event) => {
        if (!currentResults.length) return;
        if (event.key === "ArrowDown") {
          event.preventDefault();
          activeIndex = (activeIndex + 1) % currentResults.length;
          currentResults.forEach((a, i) => a.classList.toggle("active", i === activeIndex));
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          activeIndex = (activeIndex - 1 + currentResults.length) % currentResults.length;
          currentResults.forEach((a, i) => a.classList.toggle("active", i === activeIndex));
        } else if (event.key === "Enter") {
          if (currentResults[activeIndex]) {
            event.preventDefault();
            window.location.href = currentResults[activeIndex].href;
          }
        } else if (event.key === "Escape") {
          closeResults();
        }
      });
      document.addEventListener("click", (event) => {
        if (!side.contains(event.target)) closeResults();
      });
    }
  }

  // --- footer -------------------------------------------------------------
  if (foot) {
    foot.className = "foot";
    foot.innerHTML = `
      <div class="foot-inner">
        <div>
          <a class="wordmark" href="${new URL("index.html", root)}" aria-label="Cryo">
            Cryo<span class="wordmark-dot" aria-hidden="true"></span>
          </a>
          <p style="margin-top:14px;color:var(--slate);font-size:14px;max-width:36ch">
            Apache-2.0, self-hosted Iceberg maintenance with receipts for every decision.
          </p>
        </div>
        <div>
          <h4>Docs</h4>
          <ul>
            <li><a href="${new URL("quickstart/index.html", root)}">Quickstart</a></li>
            <li><a href="${new URL("architecture/index.html", root)}">Architecture</a></li>
            <li><a href="${new URL("reference/index.html", root)}">Reference</a></li>
          </ul>
        </div>
        <div>
          <h4>Operate</h4>
          <ul>
            <li><a href="${new URL("maintenance/index.html", root)}">Maintenance</a></li>
            <li><a href="${new URL("health/index.html", root)}">Health and cost</a></li>
            <li><a href="${new URL("engines/index.html", root)}">Engines</a></li>
          </ul>
        </div>
        <div>
          <h4>Project</h4>
          <ul>
            <li><a href="https://github.com/tech-pw/cryo">GitHub repository</a></li>
            <li><a href="https://www.apache.org/licenses/LICENSE-2.0">Apache-2.0 license</a></li>
            <li><a href="https://github.com/tt-a1i/archify">Architecture diagrams: Archify</a></li>
          </ul>
        </div>
      </div>
      <div class="foot-meta">
        <p>Apache, Apache Iceberg, Iceberg and Apache Spark are either registered trademarks or trademarks of The Apache Software Foundation in the United States and/or other countries. Other names may be trademarks of their respective owners.</p>
        <p><a href="https://www.apache.org/licenses/LICENSE-2.0">Apache-2.0</a> · <a href="https://github.com/tech-pw/cryo">github.com/tech-pw/cryo</a> · Last updated 2026-09-30</p>
      </div>`;
  }

  // --- on-this-page TOC + body wrapper -----------------------------------
  const pageEl = document.querySelector("main#content .page");

  // wrap page-body around all top-level content children FIRST so the TOC
  // builder can find .page-body h2 nodes.
  if (pageEl) {
    const body = document.createElement("div");
    body.className = "page-body";
    while (pageEl.firstChild && !pageEl.firstChild.classList?.contains("page-toc")) {
      body.appendChild(pageEl.firstChild);
    }
    pageEl.insertBefore(body, pageEl.firstChild);
  }

  const h2s = pageEl ? Array.from(pageEl.querySelectorAll(".page-body h2")) : [];
  if (pageEl && h2s.length >= 2) {
    pageEl.classList.remove("page--no-toc");
    const wrap = document.createElement("aside");
    wrap.className = "page-toc";
    wrap.innerHTML = `<p class="page-toc-label">On this page</p><ul></ul>`;
    const ul = wrap.querySelector("ul");
    h2s.forEach((h2) => {
      if (!h2.id) h2.id = h2.textContent.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = `#${h2.id}`;
      a.textContent = h2.textContent;
      a.dataset.target = h2.id;
      li.appendChild(a);
      ul.appendChild(li);
    });
    pageEl.appendChild(wrap);

    // Scrollspy: highlight active section as user scrolls
    const tocLinks = Array.from(wrap.querySelectorAll("a"));
    const sections = tocLinks.map((a) => document.getElementById(a.dataset.target)).filter(Boolean);
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            tocLinks.forEach((a) => a.classList.toggle("active", a.dataset.target === entry.target.id));
          }
        });
      },
      { rootMargin: "-30% 0px -60% 0px", threshold: 0 }
    );
    sections.forEach((s) => observer.observe(s));
  } else if (pageEl) {
    pageEl.classList.add("page--no-toc");
  }

  // --- terminal copy buttons ----------------------------------------------
  document.querySelectorAll(".terminal").forEach((term) => {
    const copy = term.querySelector(".terminal-copy");
    const body = term.querySelector(".terminal-body");
    if (!copy || !body) return;
    copy.addEventListener("click", async () => {
      const text = body.innerText.replace(/\s+$/g, "");
      try {
        await navigator.clipboard.writeText(text);
        copy.textContent = "Copied";
        copy.classList.add("copied");
        setTimeout(() => {
          copy.textContent = "Copy";
          copy.classList.remove("copied");
        }, 1600);
      } catch (_) {
        copy.textContent = "Copy failed";
      }
    });
  });

  // --- mount all charts ---------------------------------------------------
  if (window.CryoChart) window.CryoChart.mountAll(document.getElementById("content") || document);
})();
