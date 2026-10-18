/* Cryo docs site behaviour. No dependencies. Everything degrades: without JS the pages, nav,
 * tabs' first panel, code and diagrams all still read correctly. */
(function() {
    "use strict";

    var doc = document;
    var root = doc.body.getAttribute("data-root") || "./";
    var $ = function(s, c) { return (c || doc).querySelector(s); };
    var $$ = function(s, c) { return Array.prototype.slice.call((c || doc).querySelectorAll(s)); };
    var isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

    /* ---------- theme ---------- */
    var themeBtn = $(".theme-toggle");
    if (themeBtn) {
        themeBtn.addEventListener("click", function() {
            var next = doc.documentElement.dataset.theme === "dark" ? "light" : "dark";
            doc.documentElement.dataset.theme = next;
            try { localStorage.setItem("cryo-site-theme", next); } catch (e) { /* private mode */ }
        });
    }

    /* ---------- mobile nav ---------- */
    var menuBtn = $(".hdr-menu");
    var scrim = $(".scrim");

    function setNav(open) {
        doc.body.classList.toggle("nav-open", open);
        if (menuBtn) menuBtn.setAttribute("aria-expanded", String(open));
        if (scrim) scrim.hidden = !open;
    }
    if (menuBtn) {
        menuBtn.addEventListener("click", function() { setNav(!doc.body.classList.contains("nav-open")); });
        if (scrim) scrim.addEventListener("click", function() { setNav(false); });
        $$("#side a").forEach(function(a) { a.addEventListener("click", function() { setNav(false); }); });
        window.matchMedia("(min-width: 901px)").addEventListener("change", function(e) { if (e.matches) setNav(false); });
    }

    /* ---------- search ---------- */
    var dlg = $("#search");
    var input = dlg && $(".search-input", dlg);
    var list = dlg && $(".search-list", dlg);
    var index = null;
    var results = [];
    var sel = 0;

    function loadIndex() {
        if (index) return Promise.resolve(index);
        return fetch(root + "assets/search.json")
            .then(function(r) { return r.json(); })
            .then(function(j) { index = j.docs; return index; })
            .catch(function() { index = []; return index; });
    }

    function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

    function escapePipes(s) { return String(s).replace(/\\/g, "\\\\").replace(/\|/g, "\\|"); }

    function mark(text, terms) {
        var out = esc(text);
        terms.forEach(function(t) {
            if (!t) return;
            var re = new RegExp("(" + t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig");
            out = out.replace(re, "<mark>$1</mark>");
        });
        return out;
    }

    function score(d, terms) {
        var t = d.t.toLowerCase(),
            h = d.h.toLowerCase(),
            x = d.x.toLowerCase(),
            g = (d.g || "").toLowerCase(),
            k = (d.k || "").toLowerCase(),
            s = 0;
        for (var i = 0; i < terms.length; i++) {
            var q = terms[i],
                hit = 0;
            if (h.indexOf(q) === 0) hit = 12;
            else if (h.indexOf(q) > -1) hit = 9;
            else if (t.indexOf(q) > -1) hit = 6;
            else if (g.indexOf(q) > -1) hit = 5;
            else if (k.indexOf(q) > -1) hit = 4;
            else if (x.indexOf(q) > -1) hit = 3;
            if (!hit) return 0;
            s += hit;
        }
        return s - (d.h ? 0 : -2);
    }

    function snippet(d, terms) {
        var x = d.x;
        if (!x) return "";
        var low = x.toLowerCase(),
            at = -1;
        for (var i = 0; i < terms.length && at < 0; i++) at = low.indexOf(terms[i]);
        var start = Math.max(0, at - 40);
        return (start ? "\u2026" : "") + x.slice(start, start + 120) + (x.length > start + 120 ? "\u2026" : "");
    }

    function render(q) {
        var terms = q.toLowerCase().split(/\s+/).filter(Boolean);
        if (!terms.length) {
            results = (index || []).filter(function(d) { return !d.h; }).slice(0, 9).map(function(d) { return { d: d, s: 0 }; });
        } else {
            results = (index || [])
                .map(function(d) { return { d: d, s: score(d, terms) }; })
                .filter(function(r) { return r.s > 0; })
                .sort(function(a, b) { return b.s - a.s; })
                .slice(0, 8);
        }
        sel = 0;
        if (!results.length) {
            list.replaceChildren(emptyResult(q));
            return;
        }
        list.replaceChildren.apply(list, results.map(function(r, i) { return resultItem(r.d, terms, i); }));
    }

    function safeDocUrl(url) {
        var cleaned = String(url || "").replace(/[^a-z0-9/_-]/g, "");
        if (!cleaned || cleaned !== url) return root || "./";
        return (root || "./") + cleaned;
    }

    function emptyResult(q) {
        var item = document.createElement("li");
        item.className = "sr-empty";
        item.textContent = "No results for \u201c" + q + "\u201d";
        return item;
    }

    function resultItem(d, terms, i) {
        var title = d.h ? d.h : d.t;
        var path = d.h ? (d.g === d.t ? d.t : d.g + " \u203a " + d.t) : d.g;
        var item = document.createElement("li");
        item.setAttribute("role", "presentation");
        var link = document.createElement("a");
        link.setAttribute("role", "option");
        link.id = "sr-" + i;
        link.href = safeDocUrl(d.u);
        link.setAttribute("aria-selected", String(i === 0));
        link.appendChild(marked("span", "sr-t", title, terms));
        link.appendChild(plain("span", "sr-p", path));
        var sn = terms.length ? snippet(d, terms) : String(d.x || "").slice(0, 110);
        if (sn) link.appendChild(terms.length ? marked("span", "sr-x", sn, terms) : plain("span", "sr-x", sn));
        item.appendChild(link);
        return item;
    }

    function plain(tag, className, text) {
        var el = document.createElement(tag);
        el.className = className;
        el.textContent = text || "";
        return el;
    }

    function marked(tag, className, text, terms) {
        var el = plain(tag, className, "");
        var source = String(text || "");
        var lower = source.toLowerCase();
        var cursor = 0;
        terms.forEach(function(term) {
            if (!term) return;
            var at = lower.indexOf(term, cursor);
            if (at < 0) return;
            el.appendChild(document.createTextNode(source.slice(cursor, at)));
            var hit = document.createElement("mark");
            hit.textContent = source.slice(at, at + term.length);
            el.appendChild(hit);
            cursor = at + term.length;
        });
        el.appendChild(document.createTextNode(source.slice(cursor)));
        return el;
    }

    function select(i) {
        var items = $$('[role="option"]', list);
        if (!items.length) return;
        sel = (i + items.length) % items.length;
        items.forEach(function(el, k) { el.setAttribute("aria-selected", String(k === sel)); });
        input.setAttribute("aria-activedescendant", items[sel].id);
        items[sel].scrollIntoView({ block: "nearest" });
    }

    function openSearch() {
        if (!dlg || dlg.open) return;
        dlg.showModal();
        input.value = "";
        loadIndex().then(function() { render(""); });
        input.focus();
    }
    if (dlg) {
        $$(".search-open").forEach(function(b) { b.addEventListener("click", openSearch); });
        var kbd = $(".so-kbd");
        if (kbd && !isMac) kbd.textContent = "Ctrl K";
        input.addEventListener("input", function() { if (index) render(input.value); });
        input.addEventListener("keydown", function(e) {
            if (e.key === "ArrowDown") { e.preventDefault();
                select(sel + 1); } else if (e.key === "ArrowUp") { e.preventDefault();
                select(sel - 1); }
        });
        dlg.querySelector("form").addEventListener("submit", function(e) {
            e.preventDefault();
            var r = results[sel];
            if (r) window.location.href = safeDocUrl(r.d.u);
        });
        dlg.addEventListener("click", function(e) {
            if (e.target === dlg) dlg.close();
            var a = e.target.closest && e.target.closest("a");
            if (a) dlg.close();
        });
        list.addEventListener("mousemove", function(e) {
            var a = e.target.closest && e.target.closest('[role="option"]');
            if (a) select($$('[role="option"]', list).indexOf(a));
        });
    }
    doc.addEventListener("keydown", function(e) {
        var tag = (e.target && e.target.tagName) || "";
        var typing = /INPUT|TEXTAREA|SELECT/.test(tag) || (e.target && e.target.isContentEditable);
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault();
            openSearch(); } else if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault();
            openSearch(); }
    });

    /* ---------- copy ---------- */
    function copyText(text) {
        if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
        return new Promise(function(resolve, reject) {
            var ta = doc.createElement("textarea");
            ta.value = text;
            ta.style.position = "fixed";
            ta.style.opacity = "0";
            doc.body.appendChild(ta);
            ta.select();
            try { doc.execCommand("copy") ? resolve() : reject(); } catch (err) { reject(err); } finally { ta.remove(); }
        });
    }

    function flash(btn, label, doneLabel) {
        btn.classList.add("is-done");
        label.textContent = doneLabel;
        setTimeout(function() { btn.classList.remove("is-done");
            label.textContent = btn.dataset.label || "Copy"; }, 1600);
    }
    doc.addEventListener("click", function(e) {
        var btn = e.target.closest && e.target.closest(".copy");
        if (!btn) return;
        var label = $(".copy-label", btn) || btn;
        if (!btn.dataset.label) btn.dataset.label = label.textContent;
        copyText(btn.getAttribute("data-copy") || "").then(function() { flash(btn, label, "Copied"); }, function() { flash(btn, label, "Press \u2318C"); });
    });

    /* ---------- tabs (synced by group) ---------- */
    function selectTab(tab, focus) {
        var set = tab.closest(".tabs");
        $$('[role="tab"]', set).forEach(function(t) {
            var on = t === tab;
            t.setAttribute("aria-selected", String(on));
            t.tabIndex = on ? 0 : -1;
            var panel = doc.getElementById(t.getAttribute("aria-controls"));
            if (panel) panel.hidden = !on;
        });
        if (focus) tab.focus();
    }

    function syncGroup(tab) {
        var set = tab.closest(".tabs"),
            group = set.getAttribute("data-group"),
            label = tab.getAttribute("data-label");
        $$('.tabs[data-group="' + group + '"]').forEach(function(other) {
            if (other === set) return;
            var match = $$('[role="tab"]', other).filter(function(t) { return t.getAttribute("data-label") === label; })[0];
            if (match) selectTab(match, false);
        });
        try { localStorage.setItem("cryo-site-tab-" + group, label); } catch (e) { /* ignore */ }
    }
    $$(".tabs").forEach(function(set) {
        var group = set.getAttribute("data-group"),
            saved = null;
        try { saved = localStorage.getItem("cryo-site-tab-" + group); } catch (e) { /* ignore */ }
        if (saved) {
            var m = $$('[role="tab"]', set).filter(function(t) { return t.getAttribute("data-label") === saved; })[0];
            if (m) selectTab(m, false);
        }
    });
    doc.addEventListener("click", function(e) {
        var tab = e.target.closest && e.target.closest('[role="tab"]');
        if (!tab) return;
        selectTab(tab, false);
        syncGroup(tab);
    });
    doc.addEventListener("keydown", function(e) {
        var tab = e.target.closest && e.target.closest('[role="tab"]');
        if (!tab) return;
        var tabs = $$('[role="tab"]', tab.closest(".tablist")),
            i = tabs.indexOf(tab),
            n = null;
        if (e.key === "ArrowRight") n = tabs[(i + 1) % tabs.length];
        else if (e.key === "ArrowLeft") n = tabs[(i - 1 + tabs.length) % tabs.length];
        else if (e.key === "Home") n = tabs[0];
        else if (e.key === "End") n = tabs[tabs.length - 1];
        if (!n) return;
        e.preventDefault();
        selectTab(n, true);
        syncGroup(n);
    });

    /* ---------- table of contents scrollspy ---------- */
    var tocLinks = $$(".toc li a");
    if (tocLinks.length) {
        var heads = tocLinks.map(function(a) { return doc.getElementById(a.getAttribute("href").slice(1)); });
        var ticking = false;
        var spy = function() {
            ticking = false;
            var line = 96,
                current = 0;
            for (var i = 0; i < heads.length; i++) { if (heads[i] && heads[i].getBoundingClientRect().top <= line) current = i; }
            if (window.innerHeight + window.scrollY >= doc.documentElement.scrollHeight - 4) current = heads.length - 1;
            tocLinks.forEach(function(a, i) {
                var on = i === current;
                a.classList.toggle("is-active", on);
                if (on) a.setAttribute("aria-current", "location");
                else a.removeAttribute("aria-current");
            });
        };
        window.addEventListener("scroll", function() { if (!ticking) { ticking = true;
                requestAnimationFrame(spy); } }, { passive: true });
        window.addEventListener("resize", spy);
        spy();
    }

    /* ---------- diagrams: follow connections on hover or focus ---------- */
    function relate(svg, id) {
        var keep = {};
        keep[id] = true;
        $$(".edge", svg).forEach(function(el) {
            var hit = el.getAttribute("data-from") === id || el.getAttribute("data-to") === id;
            el.classList.toggle("is-hl", hit);
            if (hit) { keep[el.getAttribute("data-from")] = true;
                keep[el.getAttribute("data-to")] = true; }
        });
        $$(".node", svg).forEach(function(el) { el.classList.toggle("is-hl", !!keep[el.getAttribute("data-id")]); });
        svg.classList.add("is-focus");
    }

    function clear(svg) {
        svg.classList.remove("is-focus");
        $$(".is-hl", svg).forEach(function(el) { el.classList.remove("is-hl"); });
    }

    function nodeOf(e) { return e.target.closest && e.target.closest(".node"); }
    doc.addEventListener("mouseover", function(e) {
        var n = nodeOf(e);
        if (n) relate(n.closest(".dgm"), n.getAttribute("data-id"));
    });
    doc.addEventListener("mouseout", function(e) {
        var n = nodeOf(e);
        if (n && !(e.relatedTarget && n.contains(e.relatedTarget))) clear(n.closest(".dgm"));
    });
    doc.addEventListener("focusin", function(e) {
        var n = nodeOf(e);
        if (n) relate(n.closest(".dgm"), n.getAttribute("data-id"));
    });
    doc.addEventListener("focusout", function(e) {
        var n = nodeOf(e);
        if (n) clear(n.closest(".dgm"));
    });
    doc.addEventListener("click", function(e) {
        var lane = e.target.closest && e.target.closest("[data-href]");
        if (lane && !e.target.closest("a")) window.location.href = new URL(root + lane.getAttribute("data-href"), window.location.href).href;
    });

    /* ---------- figure expand ---------- */
    var expand = null;

    function ensureExpand() {
        if (expand) return expand;
        expand = doc.createElement("dialog");
        expand.className = "expand-dlg";
        expand.innerHTML = '<div class="expand-bar"><span class="expand-title"></span><button type="button">Close</button></div><div class="expand-body"></div>';
        doc.body.appendChild(expand);
        $("button", expand).addEventListener("click", function() { expand.close(); });
        expand.addEventListener("click", function(e) { if (e.target === expand) expand.close(); });
        return expand;
    }
    doc.addEventListener("click", function(e) {
        var btn = e.target.closest && e.target.closest(".fig-expand");
        if (!btn) return;
        var fig = btn.closest(".fig"),
            svg = $("svg.dgm", fig);
        if (!svg) return;
        var d = ensureExpand();
        $(".expand-title", d).textContent = $(".fig-title", fig).textContent;
        var clone = svg.cloneNode(true);
        clone.style.removeProperty("--minw");
        $(".expand-body", d).replaceChildren(clone);
        d.showModal();
    });

    /* ---------- copy page as Markdown ---------- */
    function md(node, ctx) {
        var out = "";
        node.childNodes.forEach(function(c) {
            if (c.nodeType === 3) { out += c.nodeValue.replace(/\s+/g, " "); return; }
            if (c.nodeType !== 1) return;
            var tag = c.tagName.toLowerCase();
            if (c.classList.contains("anchor") || c.classList.contains("copy") || c.classList.contains("fig-expand") || tag === "script") return;
            if (/^h[1-6]$/.test(tag)) { out += "\n\n" + new Array(+tag[1] + 1).join("#") + " " + inline(c).trim() + "\n\n"; return; }
            if (tag === "p") { out += "\n\n" + inline(c).trim() + "\n\n"; return; }
            if (tag === "pre") { out += "\n\n```\n" + c.textContent.replace(/\n+$/, "") + "\n```\n\n"; return; }
            if (tag === "ul" || tag === "ol") {
                var i = 0;
                Array.prototype.forEach.call(c.children, function(li) {
                    i++;
                    var body = md(li, ctx).trim().replace(/\n{2,}/g, "\n").replace(/\n/g, "\n   ");
                    out += "\n" + (tag === "ol" ? i + ". " : "- ") + body;
                });
                out += "\n\n";
                return;
            }
            if (tag === "table") {
                var rows = $$("tr", c).map(function(tr) { return $$("th,td", tr).map(function(cell) { return escapePipes(inline(cell).trim()); }); });
                if (rows.length) {
                    out += "\n\n| " + rows[0].join(" | ") + " |\n| " + rows[0].map(function() { return "---"; }).join(" | ") + " |\n";
                    rows.slice(1).forEach(function(r) { out += "| " + r.join(" | ") + " |\n"; });
                    out += "\n";
                }
                return;
            }
            if (tag === "dl") {
                Array.prototype.forEach.call(c.children, function(el) {
                    out += el.tagName.toLowerCase() === "dt" ? "\n- **" + inline(el).trim() + "**: " : inline(el).trim();
                });
                out += "\n\n";
                return;
            }
            if (tag === "figure" && c.classList.contains("fig")) {
                var t = $(".fig-title", c),
                    cap = $("figcaption", c),
                    alt = $$(".fig-alt li", c).map(function(li) { return "- " + li.textContent.trim(); });
                out += "\n\n**Figure: " + (t ? t.textContent : "") + "**" + (cap ? " " + cap.textContent.trim() : "") + "\n\n" + alt.join("\n") + "\n\n";
                return;
            }
            if (c.classList.contains("callout")) {
                out += "\n\n> **" + ($(".callout-title", c) || { textContent: "Note" }).textContent + ".** " + $$(".callout-body p:not(.callout-title)", c).map(function(p) { return inline(p).trim(); }).join(" ") + "\n\n";
                return;
            }
            if (c.classList.contains("pager") || c.classList.contains("toc-m") || c.classList.contains("doc-meta")) return;
            out += md(c, ctx);
        });
        return out;
    }

    function inline(node) {
        var s = "";
        node.childNodes.forEach(function(c) {
            if (c.nodeType === 3) { s += c.nodeValue.replace(/\s+/g, " "); return; }
            if (c.nodeType !== 1) return;
            var tag = c.tagName.toLowerCase();
            if (c.classList.contains("anchor") || c.classList.contains("copy")) return;
            if (tag === "code") s += "`" + c.textContent + "`";
            else if (tag === "strong" || tag === "b") s += "**" + inline(c) + "**";
            else if (tag === "em" || tag === "i") s += "*" + inline(c) + "*";
            else if (tag === "a") s += "[" + inline(c) + "](" + c.href + ")";
            else if (tag === "br") s += "\n";
            else s += inline(c);
        });
        return s;
    }
    var cp = $(".copy-page");
    if (cp) {
        cp.addEventListener("click", function() {
            var href = cp.getAttribute("data-md");
            var finish = function(text) {
                copyText(text).then(function() {
                    cp.classList.add("is-done");
                    cp.textContent = "Copied";
                    setTimeout(function() {
                        cp.classList.remove("is-done");
                        cp.textContent = "Copy page";
                    }, 1600);
                });
            };
            if (href) {
                fetch(href).then(function(r) {
                    if (!r.ok) throw new Error("markdown missing");
                    return r.text();
                }).then(finish).catch(function() {
                    var article = $(".doc");
                    var head = "# " + $(".doc h1").textContent + "\n\n" + $(".doc .lede").textContent.trim();
                    finish((head + "\n" + md($(".prose", article), {})).replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim() + "\n\nSource: " + window.location.href + "\n");
                });
                return;
            }
            var article = $(".doc");
            var head = "# " + $(".doc h1").textContent + "\n\n" + $(".doc .lede").textContent.trim();
            finish((head + "\n" + md($(".prose", article), {})).replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim() + "\n\nSource: " + window.location.href + "\n");
        });
    }

    /* ---------- dot fields ---------- */
    var still = window.matchMedia("(prefers-reduced-motion: reduce)");

    function h2(x, y) {
        var n = Math.sin(x * 127.1 + y * 311.7) * 43758.5453;
        return n - Math.floor(n);
    }

    function vnoise(x, y) {
        var xi = Math.floor(x),
            yi = Math.floor(y),
            xf = x - xi,
            yf = y - yi;
        var u = xf * xf * (3 - 2 * xf),
            v = yf * yf * (3 - 2 * yf);
        var a = h2(xi, yi),
            b = h2(xi + 1, yi),
            c = h2(xi, yi + 1),
            d = h2(xi + 1, yi + 1);
        return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v - 0.5;
    }

    function parseRgb(s) {
        s = (s || "").trim();
        if (s.charAt(0) === "#") {
            var h = s.slice(1);
            if (h.length === 3) h = h.charAt(0) + h.charAt(0) + h.charAt(1) + h.charAt(1) + h.charAt(2) + h.charAt(2);
            var n = parseInt(h, 16);
            return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
        }
        var m = s.match(/rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/);
        return m ? [+m[1], +m[2], +m[3]] : [42, 51, 64];
    }

    function waveField(host) {
        var cv = doc.createElement("canvas");
        cv.className = "dots";
        cv.setAttribute("aria-hidden", "true");
        host.insertBefore(cv, host.firstChild);
        var ctx = cv.getContext("2d");
        var W = 0,
            H = 0,
            pts = [],
            raf = 0,
            visible = true;
        var mouse = { x: -1e4, y: -1e4, tx: -1e4, ty: -1e4 };
        var col = { d: [42, 51, 64], h: [39, 122, 190] };
        var ROT = 0.12,
            PITCH = 0.42,
            DIST = 3.6;

        function readColors() {
            var cs = getComputedStyle(cv);
            col.d = parseRgb(cs.getPropertyValue("--dot"));
            col.h = parseRgb(cs.getPropertyValue("--dot-hot"));
        }

        function layout() {
            var dpr = Math.min(window.devicePixelRatio || 1, 2);
            W = host.clientWidth;
            H = host.clientHeight;
            cv.width = Math.max(1, W * dpr);
            cv.height = Math.max(1, H * dpr);
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            var N = W < 820 ? 2800 : 6200;
            var gw = Math.max(8, Math.round(Math.sqrt(N * 2.4)));
            var gh = Math.max(2, Math.floor(N / gw));
            pts = [];
            for (var i = 0; i < gw * gh; i++) {
                var ux = (i % gw) / (gw - 1) * 2 - 1;
                var uz = Math.floor(i / gw) / Math.max(1, gh - 1) * 2 - 1;
                pts.push({ x: ux * 2.35, z: uz * 1.05 });
            }
            readColors();
        }

        function frame(now) {
            var t = now / 1000;
            mouse.x += (mouse.tx - mouse.x) * 0.12;
            mouse.y += (mouse.ty - mouse.y) * 0.12;
            ctx.clearRect(0, 0, W, H);
            var cy = Math.cos(ROT),
                sy = Math.sin(ROT);
            var cx = Math.cos(PITCH),
                sx = Math.sin(PITCH);
            var f = 1 / Math.tan(0.42);
            var scale = Math.min(W / 4.7, H / 1.85) * 1.15;
            var cd = col.d,
                ch = col.h;
            for (var i = 0; i < pts.length; i++) {
                var p = pts[i],
                    X = p.x,
                    Z = p.z;
                var swell = Math.sin(X * 1.6 - t * 0.85);
                var Y = swell * 0.22 + Math.sin(Z * 2.1 + t * 0.62) * 0.12 + vnoise(X * 0.9 + t * 0.18, Z * 1.2) * 0.2;
                var mix = 0.25 + 0.50 * (0.5 + 0.5 * swell);
                var x1 = cy * X - sy * Z;
                var z1 = sy * X + cy * Z;
                var y2 = cx * Y + sx * z1;
                var z2 = -sx * Y + cx * z1 + DIST;
                if (z2 < 0.2) continue;
                var px = W * 0.5 + (x1 * f * scale) / z2;
                var py = H * 0.48 + (y2 * f * scale) / z2;
                var dx = px - mouse.x,
                    dy = py - mouse.y,
                    dist = Math.sqrt(dx * dx + dy * dy);
                var near = dist < 150 ? 1 - dist / 150 : 0;
                near *= near;
                if (near) { px += (dx / (dist || 1)) * near * 22;
                    py += (dy / (dist || 1)) * near * 22; }
                var sz = Math.max(0.7, (1.55 / z2) * 2.1);
                if (near) sz += near * 1.4;
                var a = Math.min(1, 0.28 + mix * 0.55 + near * 0.45);
                var r = cd[0] + (ch[0] - cd[0]) * (mix * 0.55 + near);
                var g = cd[1] + (ch[1] - cd[1]) * (mix * 0.55 + near);
                var b = cd[2] + (ch[2] - cd[2]) * (mix * 0.55 + near);
                ctx.fillStyle = "rgba(" + (r | 0) + "," + (g | 0) + "," + (b | 0) + "," + a.toFixed(3) + ")";
                ctx.fillRect(px - sz * 0.5, py - sz * 0.5, sz, sz);
            }
            if (!still.matches && visible) raf = requestAnimationFrame(frame);
        }

        function start() { cancelAnimationFrame(raf);
            raf = requestAnimationFrame(frame); }
        layout();
        start();
        var rt;
        window.addEventListener("resize", function() { clearTimeout(rt);
            rt = setTimeout(function() { layout();
                start(); }, 120); });
        host.addEventListener("pointermove", function(e) {
            var r = host.getBoundingClientRect();
            mouse.tx = e.clientX - r.left;
            mouse.ty = e.clientY - r.top;
            if (mouse.x < -1e3) { mouse.x = mouse.tx;
                mouse.y = mouse.ty; }
            if (still.matches) { mouse.x = mouse.tx;
                mouse.y = mouse.ty;
                start(); }
        });
        host.addEventListener("pointerleave", function() { mouse.tx = mouse.ty = mouse.x = mouse.y = -1e4; if (still.matches) start(); });
        if (window.IntersectionObserver) new IntersectionObserver(function(es) { visible = es[0].isIntersecting; if (visible) start(); }).observe(host);
        new MutationObserver(function() { readColors();
            start(); }).observe(doc.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    }

    function dotField(host) {
        if (host.getAttribute("data-dots") === "wave") { waveField(host); return; }
        var cv = doc.createElement("canvas");
        cv.className = "dots";
        cv.setAttribute("aria-hidden", "true");
        host.insertBefore(cv, host.firstChild);
        var ctx = cv.getContext("2d");
        var GAP = 24,
            W = 0,
            H = 0,
            pts = [],
            raf = 0,
            visible = true;
        var mouse = { x: -1e4, y: -1e4, tx: -1e4, ty: -1e4 };
        var col = { dot: "", hot: "" };

        function readColors() {
            var cs = getComputedStyle(cv);
            col.dot = cs.getPropertyValue("--dot").trim();
            col.hot = cs.getPropertyValue("--dot-hot").trim();
        }

        function layout() {
            var dpr = Math.min(window.devicePixelRatio || 1, 2);
            W = host.clientWidth;
            H = host.clientHeight;
            cv.width = W * dpr;
            cv.height = H * dpr;
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            pts = [];
            for (var y = GAP / 2; y < H; y += GAP) {
                for (var x = GAP / 2; x < W; x += GAP) pts.push({ x: x, y: y, p: Math.random() * 6.283 });
            }
            readColors();
        }

        function frame(t) {
            var s = t / 1000;
            mouse.x += (mouse.tx - mouse.x) * 0.12;
            mouse.y += (mouse.ty - mouse.y) * 0.12;
            ctx.clearRect(0, 0, W, H);
            for (var i = 0; i < pts.length; i++) {
                var d = pts[i];
                var wave = Math.sin(d.x * 0.01 + d.y * 0.007 + s * 0.8) + Math.sin(d.y * 0.013 - s * 0.55 + d.p * 0.25);
                var ox = Math.cos(s * 0.45 + d.p) * 2.4,
                    oy = wave * 3.2;
                var dx = d.x - mouse.x,
                    dy = d.y - mouse.y,
                    dist = Math.sqrt(dx * dx + dy * dy);
                var near = dist < 170 ? 1 - dist / 170 : 0;
                near *= near;
                if (near) { ox += (dx / (dist || 1)) * near * 16;
                    oy += (dy / (dist || 1)) * near * 16; }
                var lift = (wave + 2) / 4;
                ctx.globalAlpha = Math.min(1, 0.22 + 0.5 * lift * lift + near * 0.8);
                ctx.fillStyle = near > 0.08 ? col.hot : col.dot;
                ctx.beginPath();
                ctx.arc(d.x + ox, d.y + oy, 0.9 + lift * 0.9 + near * 1.8, 0, 6.283);
                ctx.fill();
            }
            ctx.globalAlpha = 1;
            if (!still.matches && visible) raf = requestAnimationFrame(frame);
        }

        function start() { cancelAnimationFrame(raf);
            raf = requestAnimationFrame(frame); }
        layout();
        start();
        var rt;
        window.addEventListener("resize", function() { clearTimeout(rt);
            rt = setTimeout(function() { layout();
                start(); }, 120); });
        host.addEventListener("pointermove", function(e) {
            var r = host.getBoundingClientRect();
            mouse.tx = e.clientX - r.left;
            mouse.ty = e.clientY - r.top;
            if (mouse.x < -1e3) { mouse.x = mouse.tx;
                mouse.y = mouse.ty; }
            if (still.matches) { mouse.x = mouse.tx;
                mouse.y = mouse.ty;
                start(); }
        });
        host.addEventListener("pointerleave", function() { mouse.tx = mouse.ty = mouse.x = mouse.y = -1e4; if (still.matches) start(); });
        if (window.IntersectionObserver) new IntersectionObserver(function(es) { visible = es[0].isIntersecting; if (visible) start(); }).observe(host);
        new MutationObserver(function() { readColors();
            start(); }).observe(doc.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    }
    if (window.HTMLCanvasElement) $$("[data-dots]").forEach(dotField);

    /* ---------- reveal on scroll ---------- */
    var reveals = $$(".reveal");
    if (reveals.length && window.IntersectionObserver && !still.matches) {
        doc.documentElement.classList.add("js-reveal");
        var ro = new IntersectionObserver(function(es) {
            es.forEach(function(e) { if (e.isIntersecting) { e.target.classList.add("is-in");
                    ro.unobserve(e.target); } });
        }, { rootMargin: "0px 0px -10% 0px" });
        reveals.forEach(function(el) { ro.observe(el); });
    }

    /* ---------- card spotlight ---------- */
    $$(".path, .guarantees > div, .window").forEach(function(el) {
        el.addEventListener("pointermove", function(e) {
            var r = el.getBoundingClientRect();
            el.style.setProperty("--mx", (e.clientX - r.left) + "px");
            el.style.setProperty("--my", (e.clientY - r.top) + "px");
        });
    });

    /* ---------- header on scroll ---------- */
    var hdr = $(".hdr");
    if (hdr) {
        var onScroll = function() { hdr.classList.toggle("is-scrolled", window.scrollY > 8); };
        window.addEventListener("scroll", onScroll, { passive: true });
        onScroll();
    }

    /* ---------- film: page chrome, click the picture to play, pause offscreen ---------- */
    $$(".film").forEach(function(figure) {
        var stage = $(".film-stage", figure);
        var video = $("video", figure);
        var play = $(".film-play", figure);
        var toggle = $(".film-toggle", figure);
        var mute = $(".film-mute", figure);
        var scrub = $(".film-scrub", figure);
        var time = $(".film-time", figure);
        var dur = $(".film-dur", figure);
        if (!stage || !video || !play || !toggle || !mute || !scrub || !time || !dur) return;
        video.controls = false;

        var clock = function(s) {
            s = Math.max(0, Math.floor(s || 0));
            return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
        };
        var paint = function() {
            var length = video.duration || 30;
            var at = video.currentTime || 0;
            var playing = !video.paused && !video.ended;
            stage.classList.toggle("is-playing", playing);
            stage.classList.toggle("is-muted", video.muted);
            play.hidden = playing;
            play.setAttribute("aria-label", video.ended ? "Replay" : "Play");
            toggle.setAttribute("aria-label", playing ? "Pause" : "Play");
            mute.setAttribute("aria-label", video.muted ? "Unmute" : "Mute");
            mute.setAttribute("aria-pressed", String(video.muted));
            time.textContent = clock(at);
            dur.textContent = clock(length);
            scrub.max = "1000";
            if (document.activeElement !== scrub) scrub.value = String(Math.round((at / length) * 1000));
            scrub.style.setProperty("--p", ((at / length) * 100) + "%");
            scrub.setAttribute("aria-valuemax", String(Math.round(length)));
            scrub.setAttribute("aria-valuenow", String(Math.round(at)));
            scrub.setAttribute("aria-valuetext", clock(at) + " of " + clock(length));
        };
        var start = function() {
            var pending = video.play();
            if (pending && pending.catch) pending.catch(function() {});
        };
        play.addEventListener("click", function() { if (video.ended) video.currentTime = 0; start(); });
        toggle.addEventListener("click", function() {
            if (!video.paused && !video.ended) video.pause();
            else { if (video.ended) video.currentTime = 0; start(); }
        });
        mute.addEventListener("click", function() { video.muted = !video.muted; paint(); });
        video.addEventListener("click", function() {
            if (!video.paused && !video.ended) video.pause();
            else { if (video.ended) video.currentTime = 0; start(); }
        });
        scrub.addEventListener("pointerdown", function() { stage.classList.add("is-scrubbing"); });
        var endScrub = function() { stage.classList.remove("is-scrubbing"); };
        scrub.addEventListener("pointerup", endScrub);
        scrub.addEventListener("pointercancel", endScrub);
        scrub.addEventListener("blur", endScrub);
        scrub.addEventListener("input", function() {
            var length = video.duration || 30;
            video.currentTime = (Number(scrub.value) / 1000) * length;
            paint();
        });
        ["play", "pause", "ended", "timeupdate", "loadedmetadata", "volumechange"].forEach(function(name) {
            video.addEventListener(name, paint);
        });
        stage.addEventListener("keydown", function(e) {
            if (e.target === scrub) return;
            if (e.key === " " || e.key === "k") { e.preventDefault(); toggle.click(); }
            else if (e.key === "m") mute.click();
            else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                e.preventDefault();
                video.currentTime = Math.max(0, Math.min(video.duration || 30, video.currentTime + (e.key === "ArrowRight" ? 5 : -5)));
            }
        });
        paint();
        if (!window.IntersectionObserver) return;
        new IntersectionObserver(function(es) {
            es.forEach(function(e) { if (!e.isIntersecting && !video.paused) video.pause(); });
        }, { threshold: 0.25 }).observe(stage);
        document.addEventListener("visibilitychange", function() {
            if (document.hidden && !video.paused) video.pause();
        });
    });

    /* ---------- home: maintenance flow ---------- */
    var mf = $("[data-maintenance-flow] [data-mf]");
    if (mf) {
        var mfSteps = [
            ["Small files piling up", 900],
            ["Scanning metadata", 2600],
            ["Planning the rewrite", 2600],
            ["Waiting for headroom", 2600],
            ["Rewriting one partition", 3400],
            ["Every action audited", 3200]
        ];
        var mfNodes = $$(".mf-node", mf);
        var mfPanels = $$(".mf-panel", mf);
        var mfButtons = $$("[data-go]", mf);
        var mfState = $("[data-mf-state]", mf);
        var mfPlay = $("[data-mf-play]", mf);
        var mfTiles = $(".mf-tiles", mf);
        var mfLast = mfSteps.length - 1;
        var mfAt = mfLast;
        var mfTimer = 0;
        var mfVisible = false;
        var mfStarted = false;
        var mfPaused = still.matches;
        $$("i", mfTiles).forEach(function(tile, i) {
            tile.style.setProperty("--i", i);
        });
        var mfMark = function(els, n) {
            els.forEach(function(el, i) {
                el.classList.toggle("is-done", i + 1 < n);
                el.classList.toggle("is-active", i + 1 === n);
            });
        };
        var mfShow = function(n) {
            mfAt = n;
            mf.setAttribute("data-step", n);
            if (n === 4) mf.classList.add("is-rewritten");
            else if (n < 4) mf.classList.remove("is-rewritten");
            mfMark(mfNodes, n);
            mfMark(mfPanels, n);
            mfMark(mfButtons, n);
            mfButtons.forEach(function(btn, i) {
                if (i + 1 === n) btn.setAttribute("aria-current", "step");
                else btn.removeAttribute("aria-current");
            });
            mfState.textContent = mfSteps[n][0];
            mf.style.setProperty("--mf-dur", mfSteps[n][1] + "ms");
            mf.style.setProperty("--mf-scan-h", Math.max(0, mfTiles.clientHeight - 24) + "px");
        };
        var mfTick = function() {
            mfShow((mfAt + 1) % mfSteps.length);
            mfTimer = setTimeout(mfTick, mfSteps[mfAt][1]);
        };
        var mfSync = function() {
            var run = mfVisible && !mfPaused;
            clearTimeout(mfTimer);
            mf.classList.toggle("is-playing", run);
            if (run) mfTimer = setTimeout(mfTick, mfSteps[mfAt][1]);
            mfPlay.textContent = !mfPaused ? "Pause" : mfAt === mfLast ? "Replay" : "Play";
        };
        mfButtons.forEach(function(btn) {
            btn.addEventListener("click", function() {
                mfPaused = true;
                mfShow(Number(btn.getAttribute("data-go")));
                mfSync();
            });
        });
        mfPlay.addEventListener("click", function() {
            mfPaused = !mfPaused;
            if (!mfPaused && mfAt === mfLast) mfShow(0);
            mfSync();
        });
        mf.classList.add("is-animated");
        mfPlay.hidden = false;
        mfShow(mfLast);
        var mfSeen = function(on) {
            mfVisible = on;
            if (on && !mfStarted && !mfPaused) {
                mfStarted = true;
                mfShow(0);
            }
            mfSync();
        };
        if (window.IntersectionObserver) {
            new IntersectionObserver(function(es) {
                mfSeen(es[0].isIntersecting);
            }, { threshold: 0.18 }).observe(mf);
        } else {
            mfSeen(true);
        }
    }

    /* ---------- home: table journey ---------- */
    var tj = $("[data-tj]");
    if (tj && window.IntersectionObserver) {
        // Must match the prices and counts written in table-journey.html.
        var TJ_GB_MONTH = 0.023,
            TJ_PER_GET = 0.0004 / 1000,
            TJ_QUERIES = 20000,
            TJ_LIVE_TILES = 48,
            TJ_TILES = 72,
            TJ_SVG = "http://www.w3.org/2000/svg";
        var tjBase = { live: 1000, del: 0, ghost: 0, orphan: 0, frag: 0, gets: 66, files: 1953, snaps: 1, manifests: 8, stats: 1, layers: 3 };
        var tjDecayed = { del: 40, ghost: 300, orphan: 100, frag: 1, gets: 9700, files: 250000, snaps: 8640, manifests: 640, stats: 0, layers: 14 };
        // [when, change, hot bill rows, what Cryo says, marker x, marker y]
        var tjStages = [
            ["Day 1", {}, ["live"], "Table looks healthy. Nothing to plan.", 240, 64],
            ["Day 30", tjDecayed, ["gets", "ghost", "del", "orphan"], "250,000 small files across 30 partitions. Planning a run.", 150, 64],
            ["Step 1 of 6", { del: 0, ghost: 440, gets: 8333 }, ["del", "gets"], "Folding 40,000 delete files into the data.", 96, 64],
            ["Step 2 of 6", { ghost: 1440, frag: 0, gets: 66, files: 1953 }, ["gets", "ghost"], "Rewriting 30 days into 512 MB files while Trino has headroom.", 240, 64],
            ["Step 3 of 6", { stats: 1 }, [], "Collecting distinct-value counts.", 500, 168],
            ["Step 4 of 6", { manifests: 12 }, [], "Merging 640 manifests into 12.", 428, 28],
            ["Step 5 of 6", { ghost: 0, snaps: 288, layers: 4 }, ["ghost"], "Expiring 8,352 snapshots older than 1 day.", 356, 190],
            ["Step 6 of 6", { orphan: 0 }, ["orphan"], "Deleting 100 GB that nothing references.", 420, 212],
            ["Done", {}, ["live"], "Run complete. 6 audit rows written.", 240, 64]
        ];
        var tjState = [];
        tjStages.reduce(function(prev, s) {
            var next = Object.assign({}, prev, s[1]);
            tjState.push(next);
            return next;
        }, tjBase);
        var tjCost = function(st) {
            return {
                live: st.live * TJ_GB_MONTH,
                del: st.del * TJ_GB_MONTH,
                ghost: st.ghost * TJ_GB_MONTH,
                orphan: st.orphan * TJ_GB_MONTH,
                gets: st.gets * TJ_QUERIES * TJ_PER_GET
            };
        };
        var tjSum = function(c) { return c.live + c.del + c.ghost + c.orphan + c.gets; };
        var tjMoney = function(n) { return "$" + n.toFixed(2); };
        var tjDay1 = tjSum(tjCost(tjState[0]));
        var tjDay30 = tjSum(tjCost(tjState[1]));

        var tjSeed = 11;
        var tjRnd = function() { tjSeed = (tjSeed * 16807) % 2147483647; return (tjSeed - 1) / 2147483646; };
        var tjEl = function(tag, attrs, parent) {
            var el = doc.createElementNS(TJ_SVG, tag);
            Object.keys(attrs).forEach(function(k) { el.setAttribute(k, attrs[k]); });
            parent.appendChild(el);
            return el;
        };
        var tjVars = function(el, vars) {
            Object.keys(vars).forEach(function(k) { el.style.setProperty("--" + k, vars[k]); });
        };

        // Shuffle the 12 x 6 cells so live and ghost tiles end up scattered through the core.
        var tjCells = [];
        for (var ci = 0; ci < TJ_TILES; ci++) tjCells.push(ci);
        for (var cj = tjCells.length - 1; cj > 0; cj--) {
            var ck = Math.floor(tjRnd() * (cj + 1)), cv = tjCells[cj];
            tjCells[cj] = tjCells[ck];
            tjCells[ck] = cv;
        }
        var tjTileBox = $("[data-tj-tiles]", tj), tjTiles = [];
        tjCells.forEach(function(cell, i) {
            var x = 62 + (cell % 12) * 32.3 + (tjRnd() - 0.5) * 12, y = 84 + Math.floor(cell / 12) * 14 + (tjRnd() - 0.5) * 3;
            var t = tjEl("rect", { class: "c-tile", x: -5.5, y: -5.5, width: 11, height: 11, rx: 1.5 }, tjTileBox);
            tjVars(t, { x: x.toFixed(1) + "px", y: y.toFixed(1) + "px", r: ((tjRnd() - 0.5) * 50).toFixed(0) + "deg",
                tx: (164 + ((x - 46) / 388) * 172).toFixed(1) + "px", ty: "198px", i: i });
            tjTiles.push(t);
        });
        var tjSlipBox = $("[data-tj-slips]", tj), tjSlips = [];
        for (var si = 0; si < 8; si++) {
            var sg = tjEl("g", { class: "c-slip" }, tjSlipBox);
            tjEl("rect", { x: -5, y: -3.5, width: 10, height: 7, rx: 1 }, sg);
            tjEl("path", { d: "M-3 2L3 -2" }, sg);
            tjVars(sg, { x: (70 + si * 46 + tjRnd() * 18).toFixed(0) + "px", y: (88 + tjRnd() * 60).toFixed(0) + "px", r: ((tjRnd() - 0.5) * 40).toFixed(0) + "deg", i: si });
            tjSlips.push(sg);
        }
        var tjStrayBox = $("[data-tj-strays]", tj), tjStrays = [];
        for (var oi = 0; oi < 6; oi++) {
            var o = tjEl("rect", { class: "c-stray", x: -5, y: -5, width: 10, height: 10, rx: 1.5 }, tjStrayBox);
            tjVars(o, { x: (22 + oi * 22 + tjRnd() * 10).toFixed(0) + "px", y: (190 + tjRnd() * 14).toFixed(0) + "px", r: ((tjRnd() - 0.5) * 60).toFixed(0) + "deg",
                px: (384 + (oi % 3) * 11) + "px", py: (206 - Math.floor(oi / 3) * 9) + "px", i: oi });
            tjStrays.push(o);
        }
        var tjLayerBox = $("[data-tj-layers]", tj), tjLayers = [];
        for (var li = 0; li < 16; li++) tjLayers.push(tjEl("line", { class: "c-layer", x1: 42, x2: 438, y1: 0, y2: 0 }, tjLayerBox));
        var tjCardBox = $("[data-tj-cards]", tj), tjCards = [];
        for (var di = 0; di < 18; di++) {
            var cd = tjEl("rect", { class: "c-card", x: -2, y: -20, width: 4, height: 20, rx: 1 }, tjCardBox);
            tjVars(cd, { jy: (tjRnd() * 8).toFixed(1) + "px", jr: ((tjRnd() - 0.5) * 34).toFixed(0) + "deg", i: di });
            tjCards.push(cd);
        }

        var tjCore = $(".tj-core", tj);
        var tjBlocks = $("[data-tj-blocks]", tj);
        var tjTag = $("[data-tj-tag]", tj);
        var tjDrawer = $("[data-tj-drawer]", tj);
        var tjMarker = $("[data-tj-marker]", tj);
        var tjScan = $("[data-tj-scan]", tj);
        var tjMark = $("[data-tj-mark]", tj);
        var tjSay = $("[data-tj-say]", tj);
        var tjWhen = $("[data-tj-when]", tj);
        var tjTotal = $("[data-tj-total]", tj);
        var tjDelta = $("[data-tj-delta]", tj);
        var tjRows = $$("[data-tj-row]", tj);
        var tjSegs = $$("[data-tj-seg]", tj);
        var tjMeters = $$("[data-tj-m]", tj);
        var tjChapters = $$("[data-tj-stage]", tj);
        var tjLong = $(".tj-ch-long", tj);
        var tjCard = $(".tj-card", tj);
        var tjMouth = $("[data-tj-mouth]", tj);
        var tjFace = $("[data-tj-face]", tj);
        var tjFMouth = $("[data-tj-fmouth]", tj);
        var tjBrow = $("[data-tj-brow]", tj);
        var tjTear = $("[data-tj-tear]", tj);
        var tjMoodWord = $("[data-tj-mood]", tj);
        var tjMult = $("[data-tj-mult]", tj);
        var tjX = $$("[data-tj-x]", tj);
        // Healthy reference per meter: what one maintained day looks like (288 commits, a dozen manifests).
        var tjHealthy = { files: 1953, snaps: 288, manifests: 12, gets: 66 };
        var tjClamp = function(v) { return Math.max(0, Math.min(1, v)); };
        var tjLogMood = function(v, good, bad) { return v <= good ? 0 : tjClamp(Math.log(v / good) / Math.log(bad / good)); };
        // 0 = green and smiling, 1 = red and crying; amber in the middle.
        var tjHue = function(m) { return m < 0.4 ? 145 - 250 * m : 45 - 68 * (m - 0.4); };
        var tjColor = function(m, l) { return "hsl(" + tjHue(m).toFixed(0) + " 78% " + (l || 58) + "%)"; };
        var tjPath = function(el, d) {
            el.setAttribute("d", d);
            el.style.d = 'path("' + d + '")';
        };
        var tjWord = function(m) { return m < 0.12 ? "Healthy" : m < 0.4 ? "Fine" : m < 0.7 ? "Pricey" : "Painful"; };
        var tjShown = tjDay1;
        var tjRaf = 0;
        var tjTick = 0;
        var tjAt = -1;

        var tjCount = function(to) {
            cancelAnimationFrame(tjRaf);
            var from = tjShown, t0 = performance.now();
            if (still.matches) { tjShown = to; tjTotal.textContent = tjMoney(to); return; }
            (function step(t) {
                var k = Math.min(1, (t - t0) / 650), ease = 1 - Math.pow(1 - k, 3);
                tjShown = from + (to - from) * ease;
                tjTotal.textContent = tjMoney(tjShown);
                if (k < 1) tjRaf = requestAnimationFrame(step);
            })(t0);
        };
        var tjReplay = function(el, cls) {
            if (still.matches) return;
            el.classList.remove(cls);
            void el.getBoundingClientRect();
            el.classList.add(cls);
        };
        var tjRender = function(st, n, when, instant) {
            var s = tjStages[n], cost = tjCost(st), total = tjSum(cost), hot = s[2];
            tjWhen.textContent = when;
            if (instant) {
                cancelAnimationFrame(tjRaf);
                tjShown = total;
                tjTotal.textContent = tjMoney(total);
            } else {
                tjCount(total);
            }
            var last = n === tjStages.length - 1;
            var diff = last ? total - tjDay30 : total - tjDay1;
            tjDelta.className = Math.abs(diff) < 0.005 ? "" : diff > 0 ? "is-up" : "is-down";
            tjDelta.textContent = Math.abs(diff) < 0.005 ? "" :
                (diff > 0 ? "+" : "−") + tjMoney(Math.abs(diff)) + (last ? " vs day 30" : " vs day 1");
            tjRows.forEach(function(row) {
                var key = row.getAttribute("data-tj-row");
                $("b", row).textContent = tjMoney(cost[key]);
                row.classList.toggle("is-hot", hot.indexOf(key) !== -1);
            });
            // Segments are sized against the day-30 bill, so the whole bar shrinks as the run pays off.
            tjSegs.forEach(function(seg) {
                var key = seg.getAttribute("data-tj-seg");
                seg.style.width = ((cost[key] / tjDay30) * 100).toFixed(2) + "%";
                seg.classList.toggle("is-hot", hot.indexOf(key) !== -1);
            });
            tjMeters.forEach(function(dd) {
                dd.textContent = Math.round(st[dd.getAttribute("data-tj-m")]).toLocaleString("en-US");
            });
            tjX.forEach(function(dd) {
                var key = dd.getAttribute("data-tj-x"), v = st[key], good = tjHealthy[key];
                var m = tjLogMood(v, good, tjDecayed[key]);
                var ratio = v / good;
                dd.textContent = ratio >= 1.5 ? Math.round(ratio).toLocaleString("en-US") + "× healthy" : "healthy";
                dd.style.color = tjColor(m, 66);
                dd.previousElementSibling.style.color = m < 0.12 ? "" : tjColor(m, 70);
            });

            var mood = tjClamp((total - tjDay1) / (tjDay30 - tjDay1));
            var moodColor = tjColor(mood);
            tjCard.style.setProperty("--mood", moodColor);
            tjTotal.style.color = tjColor(mood, 66);
            tjFace.style.setProperty("--face", moodColor);
            var y0 = 26 + 3 * mood, cy = y0 + 9 * (1 - 2 * mood);
            tjPath(tjFMouth, "M12 " + y0.toFixed(1) + "Q20 " + cy.toFixed(1) + " 28 " + y0.toFixed(1));
            var inner = (12 - 4 * mood).toFixed(1);
            tjPath(tjBrow, "M9 12.5L16 " + inner + "M24 " + inner + "L31 12.5");
            tjBrow.style.opacity = tjClamp((mood - 0.4) * 2.5).toFixed(2);
            tjTear.style.opacity = tjClamp((mood - 0.8) * 5).toFixed(2);
            tjPath(tjMouth, "M24.9 43.5Q28.8 " + (45.75 + 3.6 * (1 - 2 * mood)).toFixed(2) + " 32.7 48");
            tjMoodWord.textContent = tjWord(mood);
            tjMoodWord.style.color = tjColor(mood, 66);
            tjMult.textContent = (total / tjDay1).toFixed(1) + "× the day-1 bill";

            var loose = Math.round(st.frag * TJ_LIVE_TILES), ghosts = Math.round(st.ghost / 20);
            tjTiles.forEach(function(t, i) {
                var rank = i >= TJ_LIVE_TILES ? i - TJ_LIVE_TILES : i + (TJ_TILES - TJ_LIVE_TILES);
                var cls = i < TJ_LIVE_TILES && i < loose ? " is-loose" : rank < ghosts ? " is-ghost" : n >= 6 ? " is-gone" : "";
                t.setAttribute("class", "c-tile" + cls);
            });
            var slips = Math.round(st.del / 5);
            tjSlips.forEach(function(g, i) { g.classList.toggle("is-on", i < slips); });
            var strays = Math.round(st.orphan * 0.06);
            tjStrays.forEach(function(o, i) {
                o.classList.toggle("is-on", i < strays);
                o.classList.toggle("is-swept", i >= strays && n >= 7);
            });
            var layers = Math.round(st.layers);
            tjLayers.forEach(function(ln, k) {
                var on = k < layers;
                ln.style.setProperty("--y", (on ? 70 + ((k + 1) * 96) / (layers + 1) : 166).toFixed(1) + "px");
                ln.classList.toggle("is-on", on);
            });
            tjBlocks.style.setProperty("--on", (1 - st.frag).toFixed(3));
            var cards = st.manifests > 12 ? Math.round(5 + 13 * Math.min(1, (st.manifests - 12) / 628)) : 5;
            var messy = st.manifests > 60;
            tjDrawer.classList.toggle("is-messy", messy);
            tjCards.forEach(function(cd, k) {
                var on = k < cards;
                var x = messy ? 336 + k * 4.7 : 344 + (k * 64) / Math.max(1, cards - 1);
                cd.style.setProperty("--x", x.toFixed(1) + "px");
                cd.classList.toggle("is-on", on);
            });
            tjTag.classList.toggle("is-fresh", st.stats >= 0.5);
            tjCore.setAttribute("data-n", n);
            tjMarker.style.transform = "translate(" + s[4] + "px," + s[5] + "px)";
            if (tjSay.textContent !== s[3]) tjSay.textContent = s[3];
        };
        var tjScrub = function() {
            tjTick = 0;
            if (tjAt !== 1) return;
            var r = tjLong.getBoundingClientRect();
            var p = Math.max(0, Math.min(1, (innerHeight / 2 - r.top) / (r.height * 0.8)));
            var a = tjState[0], b = tjState[1], st = {};
            Object.keys(b).forEach(function(k) { st[k] = a[k] + (b[k] - a[k]) * p; });
            tjRender(st, 1, "Day " + (1 + Math.round(29 * p)), true);
            if (p < 0.98) tjSay.textContent = "Watching. " + Math.round(st.files).toLocaleString("en-US") + " data files and counting.";
        };
        var tjShow = function(n) {
            if (n === tjAt) return;
            var from = tjAt;
            tjAt = n;
            tjChapters.forEach(function(ch, j) {
                ch.classList.toggle("is-active", j === n);
                ch.classList.toggle("is-seen", j < n);
            });
            if (n === 1) {
                tjScrub();
            } else {
                tjRender(tjState[n], n, tjStages[n][0], false);
            }
            if (n >= 2 && n <= 7) tjReplay(tjScan, "is-on");
            if (n === 3 && from !== 3) tjReplay(tjMark, "is-fusing");
        };

        tj.classList.add("is-live");
        tjShow(0);
        var tjIo = new IntersectionObserver(function(es) {
            es.forEach(function(e) {
                if (e.isIntersecting) tjShow(Number(e.target.getAttribute("data-tj-stage")));
            });
        }, { rootMargin: "-50% 0px -50% 0px" });
        tjChapters.forEach(function(ch) { tjIo.observe(ch); });
        addEventListener("scroll", function() {
            if (tjAt === 1 && !tjTick) tjTick = requestAnimationFrame(tjScrub);
        }, { passive: true });

        var tjCalcN = $("[data-tj-calc-n]", tj);
        if (tjCalcN) {
            var tjCalcOut = $("[data-tj-calc-out]", tj);
            var tjCalcMo = $("[data-tj-calc-mo]", tj);
            var tjCalcYr = $("[data-tj-calc-yr]", tj);
            var tjFmt = function(v) { return "$" + v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
            var tjCalc = function() {
                var count = Number(tjCalcN.value), saved = (tjDay30 - tjDay1) * count;
                tjCalcOut.textContent = count + (count === 1 ? " table" : " tables");
                tjCalcMo.textContent = tjFmt(saved);
                tjCalcYr.textContent = tjFmt(saved * 12);
                tjCalcN.style.setProperty("--fill", (((count - 1) / 199) * 100).toFixed(1) + "%");
            };
            tjCalcN.addEventListener("input", tjCalc);
            tjCalc();
        }
    }

    /* ---------- home: stats count, receipt feed ---------- */
    if (window.IntersectionObserver && !still.matches) {
        var counters = $$("[data-count]");
        var co = new IntersectionObserver(function(es) {
            es.forEach(function(e) {
                if (!e.isIntersecting) return;
                co.unobserve(e.target);
                var el = e.target,
                    to = +el.dataset.count,
                    from = +(el.dataset.from || 0),
                    t0 = performance.now();
                (function step(t) {
                    var k = Math.min(1, (t - t0) / 1400),
                        ease = 1 - Math.pow(1 - k, 3);
                    el.textContent = Math.round(from + (to - from) * ease);
                    if (k < 1) requestAnimationFrame(step);
                })(t0);
            });
        }, { threshold: 0.6 });
        counters.forEach(function(el) { el.textContent = el.dataset.from || "0";
            co.observe(el); });
    }

    var feed = $(".receipt-feed");
    if (feed && !still.matches) {
        setInterval(function() {
            if (doc.hidden) return;
            var last = feed.lastElementChild;
            feed.insertBefore(last, feed.firstElementChild);
            last.classList.remove("is-new");
            void last.offsetWidth;
            last.classList.add("is-new");
        }, 2600);
    }

    /* ---------- docs: share, sidebar collapse ---------- */
    var share = $(".share-page");
    if (share) {
        share.addEventListener("click", function() {
            var url = window.location.href.split("#")[0],
                label = $("span", share);
            if (navigator.share) { navigator.share({ title: doc.title, url: url }).catch(function() {}); return; }
            copyText(url).then(function() { label.textContent = "Link copied";
                setTimeout(function() { label.textContent = "Share"; }, 1600); });
        });
    }

    var collapse = $(".side-collapse");
    if (collapse) {
        var setCollapsed = function(on) {
            doc.body.classList.toggle("side-collapsed", on);
            collapse.setAttribute("aria-pressed", String(on));
            collapse.setAttribute("aria-label", on ? "Expand sidebar" : "Collapse sidebar");
        };
        try { setCollapsed(localStorage.getItem("cryo-side") === "collapsed"); } catch (e) { /* private mode */ }
        collapse.addEventListener("click", function() {
            var on = !doc.body.classList.contains("side-collapsed");
            setCollapsed(on);
            try { localStorage.setItem("cryo-side", on ? "collapsed" : "open"); } catch (e) { /* private mode */ }
        });
    }

    /* ---------- screenshot zoom ---------- */
    var shots = $$(".shot-zoom");
    if (shots.length && window.HTMLDialogElement) {
        var zoom = doc.createElement("dialog");
        zoom.className = "zoom";
        zoom.setAttribute("aria-label", "Enlarged screenshot");
        zoom.innerHTML = '<button class="zoom-close" type="button" aria-label="Close">×</button>';
        doc.body.appendChild(zoom);
        var zImg = null;
        zoom.addEventListener("click", function(e) {
            if (e.target === zoom || e.target === zImg || (e.target.classList && e.target.classList.contains("zoom-close"))) zoom.close();
        });
        shots.forEach(function(btn) {
            btn.addEventListener("click", function() {
                var dark = doc.documentElement.dataset.theme === "dark";
                var img = $(dark ? ".shot-dark" : ".shot-light", btn);
                var src = img.currentSrc || img.src;
                if (!zImg) {
                    zImg = new Image();
                    zImg.src = src;
                    zImg.alt = img.alt || "Enlarged screenshot";
                    zoom.appendChild(zImg);
                } else {
                    zImg.src = src;
                    zImg.alt = img.alt || "Enlarged screenshot";
                }
                zoom.showModal();
            });
        });
    }
})();