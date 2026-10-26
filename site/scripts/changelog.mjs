// Render CHANGELOG.md (Keep a Changelog subset) into page HTML.
// Supports headings, paragraphs, bullet lists with wrapped lines, inline code, links and bold.
export function renderChangelog(md, repo) {
  const escH = (s) => s.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  const refs = {};
  const lines = [];
  for (const line of md.split("\n")) {
    const m = line.match(/^\[([^\]]+)\]:\s*(\S+)\s*$/);
    if (m) refs[m[1].toLowerCase()] = m[2];
    else lines.push(line);
  }
  const href = (u) => (/^(https?:|mailto:|#)/.test(u) ? u : `${repo}/blob/main/${u.replace(/^\.?\//, "")}`);
  const inline = (s) => {
    const codes = [];
    s = s.replace(/`([^`]+)`/g, (_, c) => `\u0001${codes.push(c) - 1}\u0001`);
    s = escH(s);
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, t, u) => `<a href="${escH(href(u))}">${t}</a>`);
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    return s.replace(/\u0001(\d+)\u0001/g, (_, i) => `<code>${escH(codes[Number(i)])}</code>`);
  };
  const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const out = [];
  let para = [];
  let item = null;
  let list = [];
  let version = "";
  const flushPara = () => {
    if (para.length) out.push(`<p>${inline(para.join(" "))}</p>`);
    para = [];
  };
  const flushList = () => {
    if (item !== null) list.push(item);
    item = null;
    if (list.length) out.push(`<ul>\n${list.map((li) => `  <li>${inline(li)}</li>`).join("\n")}\n</ul>`);
    list = [];
  };
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    let m;
    if (/^#\s/.test(line)) {
      flushPara();
      flushList();
    } else if ((m = line.match(/^(#{2,3})\s+(.*)$/))) {
      flushPara();
      flushList();
      const level = m[1].length;
      let text = m[2];
      const ref = text.match(/^\[([^\]]+)\](.*)$/);
      let label = inline(text);
      if (ref) {
        const url = refs[ref[1].toLowerCase()];
        label = url ? `<a href="${escH(href(url))}">${escH(ref[1])}</a>${inline(ref[2])}` : inline(ref[1] + ref[2]);
        text = ref[1];
      }
      // Ids must not start with a digit, or `#0-0-0` is not a valid CSS selector.
      if (level === 2) version = slug(text).replace(/^(\d)/, "v$1");
      const id = level === 2 ? version : `${version}-${slug(text)}`;
      out.push(`<h${level} id="${id}">${label}</h${level}>`);
    } else if ((m = line.match(/^[-*]\s+(.*)$/))) {
      flushPara();
      if (item !== null) list.push(item);
      item = m[1];
    } else if (item !== null && /^\s+\S/.test(line)) {
      item += ` ${line.trim()}`;
    } else if (line === "") {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushPara();
  flushList();
  return out.join("\n");
}
