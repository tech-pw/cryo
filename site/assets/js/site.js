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
            s = 0;
        for (var i = 0; i < terms.length; i++) {
            var q = terms[i],
                hit = 0;
            if (h.indexOf(q) === 0) hit = 12;
            else if (h.indexOf(q) > -1) hit = 9;
            else if (t.indexOf(q) > -1) hit = 6;
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
            list.innerHTML = '<li class="sr-empty">No results for \u201c' + esc(q) + '\u201d</li>';
            return;
        }
        list.innerHTML = results.map(function(r, i) {
            var d = r.d;
            var title = d.h ? d.h : d.t;
            var path = d.h ? (d.g === d.t ? d.t : d.g + " \u203a " + d.t) : d.g;
            var sn = terms.length ? snippet(d, terms) : esc(d.x.slice(0, 110));
            return '<li role="presentation"><a role="option" id="sr-' + i + '" href="' + root + d.u + '" aria-selected="' + (i === 0) + '">' +
                '<span class="sr-t">' + mark(title, terms) + '</span><span class="sr-p">' + esc(path) + '</span>' +
                (sn ? '<span class="sr-x">' + (terms.length ? mark(sn, terms) : sn) + '</span>' : "") + "</a></li>";
        }).join("");
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
            if (r) window.location.href = root + r.d.u;
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
    var tocLinks = $$(".toc a");
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
                var rows = $$("tr", c).map(function(tr) { return $$("th,td", tr).map(function(cell) { return inline(cell).trim().replace(/\|/g, "\\|"); }); });
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

    /* ---------- home: maintenance flow ---------- */
    var maintenanceFlow = $("[data-maintenance-flow]");
    if (maintenanceFlow && !still.matches) {
        var flowSteps = [["", 280], ["at-scan", 700], ["at-admit", 520], ["at-rewrite", 1600], ["at-done", 1100]];
        var flowAt = 0;
        var flowTimer = 0;
        var flowAdvance = function() {
            var step = flowSteps[flowAt];
            if (step[0]) maintenanceFlow.classList.add(step[0]);
            else maintenanceFlow.classList.remove("at-scan", "at-admit", "at-rewrite", "at-done");
            flowAt = (flowAt + 1) % flowSteps.length;
            flowTimer = setTimeout(flowAdvance, step[1]);
        };
        var flowRun = function(on) {
            if (on === maintenanceFlow.classList.contains("is-running")) return;
            maintenanceFlow.classList.toggle("is-running", on);
            clearTimeout(flowTimer);
            if (on) {
                flowAt = 0;
                flowAdvance();
            }
        };
        maintenanceFlow.classList.add("is-animated");
        if (window.IntersectionObserver) {
            new IntersectionObserver(function(es) {
                flowRun(es[0].isIntersecting);
            }, { threshold: 0.18 }).observe(maintenanceFlow);
        } else {
            flowRun(true);
        }
    }

    /* ---------- home: stats count, ops progress, receipt feed ---------- */
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

    var opsBox = $(".home .ops");
    if (opsBox) {
        var opsItems = $$("li", opsBox);
        opsBox.classList.add("is-live");
        var paintOps = function() {
            var r = opsBox.getBoundingClientRect(),
                mid = window.innerHeight * 0.6;
            var p = Math.max(0, Math.min(1, (mid - r.top) / r.height));
            opsBox.style.setProperty("--p", p.toFixed(3));
            opsItems.forEach(function(li) { li.classList.toggle("is-lit", li.getBoundingClientRect().top < mid); });
        };
        window.addEventListener("scroll", paintOps, { passive: true });
        paintOps();
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