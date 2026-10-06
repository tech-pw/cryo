// Tiny build-time code highlighter. Only the languages the docs actually use.
// Output uses the .tk-* classes in site.css, which map onto the brand palette.

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const wrap = (cls, s) => `<span class="${cls}">${esc(s)}</span>`;

const SQL_KW = new Set(
  "ALTER TABLE EXECUTE WHERE CALL TIMESTAMP SELECT FROM AND OR NOT AS SET WITH ON INSERT INTO DELETE UPDATE MERGE USING DROP CREATE SHOW".split(" "),
);

function tokenize(line, rules) {
  let out = "";
  let rest = line;
  while (rest.length) {
    let hit = null;
    for (const [re, cls, fn] of rules) {
      const m = re.exec(rest);
      if (m && m.index === 0 && m[0].length) {
        hit = { text: m[0], cls, fn };
        break;
      }
    }
    if (hit) {
      out += hit.fn ? hit.fn(hit.text) : wrap(hit.cls, hit.text);
      rest = rest.slice(hit.text.length);
    } else {
      out += esc(rest[0]);
      rest = rest.slice(1);
    }
  }
  return out;
}

const sqlRules = [
  [/^--.*/, "tk-c"],
  [/^'(?:[^'\\]|\\.)*'/, "tk-s"],
  [/^=>/, "tk-o"],
  [/^\d+(?:\.\d+)?/, "tk-n"],
  [/^[A-Za-z_][\w.]*/, "", (t) => (SQL_KW.has(t.toUpperCase()) ? wrap("tk-k", t) : t.includes(".") ? wrap("tk-i", t) : esc(t))],
];

const bashRules = [
  [/^#.*/, "tk-c"],
  [/^"(?:[^"\\]|\\.)*"/, "tk-s"],
  [/^'[^']*'/, "tk-s"],
  [/^https?:\/\/[^\s"']+/, "tk-u"],
  [/^--?[A-Za-z][\w-]*/, "tk-o"],
];

export function highlight(code, lang, opts = {}) {
  const lines = code.replace(/\n+$/, "").split("\n");
  const copy = [];
  const html = lines
    .map((line) => {
      if (lang === "bash" || lang === "shell") {
        const m = /^\$ (.*)$/.exec(line);
        if (m) {
          copy.push(m[1]);
          const cmd = m[1];
          const first = /^(\S+)/.exec(cmd);
          const head = first ? wrap("tk-cmd", first[1]) : "";
          const tail = first ? tokenize(cmd.slice(first[1].length), bashRules) : tokenize(cmd, bashRules);
          return `<span class="ln"><span class="tk-p" aria-hidden="true">$ </span>${head}${tail}</span>`;
        }
        return `<span class="ln tk-out">${esc(line)}</span>`;
      }
      if (lang === "sql") return `<span class="ln">${tokenize(line, sqlRules)}</span>`;
      return `<span class="ln">${esc(line)}</span>`;
    })
    .join("\n");
  return { html, copy: lang === "bash" || lang === "shell" ? copy.join("\n") : code.replace(/\n+$/, ""), ...opts };
}
