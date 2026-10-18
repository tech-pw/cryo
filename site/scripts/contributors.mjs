// Homepage contributor list.
//
// `site/_src/contributors.json` is a snapshot of
// https://api.github.com/repos/tech-pw/cryo/contributors, refreshed by
// `node site/scripts/contributors.mjs`. The docs build reads that file and
// never calls GitHub, so `make docs` and CI stay offline.
//
// GitHub's contributor list already excludes accounts whose commits were not
// linked to a user. This script also drops bots and AI agents: the `[bot]`
// login suffix, GitHub's Bot account type, and the patterns below. A human
// who wants a different public name sets `names` here — profile names on
// these accounts are often emails, which must not be published.

import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "_src");
const OUT = join(ROOT, "contributors.json");
const REPO = "tech-pw/cryo";

// Public name for a login. Absent logins fall back to the GitHub profile
// name, then the login. Never put an email here.
export const DISPLAY_NAMES = {
  "aamirpw306": "Aamir Siddiqui",
  "ShubhamMaurya9": "Shubham Maurya",
  "piyush-kumar20": "Piyush Kumar",
  "niteesh-pw": "Niteesh Kumar Pandey",
};

// Logins excluded even if GitHub counts their commits. Matched case-insensitively.
const AGENT_LOGIN = /(?:\[bot\]$|^dependabot|^renovate|^copilot|^github-actions|^cryo-agent|^cursoragent|^devin-ai|^coderabbit|^sweep-ai|^claude|^chatgpt|^openai|^gemini|^codex)/i;

export function isAgent(login, type = "User") {
  if (!login) return true;
  if (type === "Bot") return true;
  return AGENT_LOGIN.test(login);
}

export function displayName(login, profileName) {
  const mapped = DISPLAY_NAMES[login];
  if (mapped) return mapped;
  const profile = (profileName || "").trim();
  if (profile && !profile.includes("@")) return profile;
  return login;
}

function pad(n) {
  return String(n).padStart(2, "0");
}

export function contributorItems(rows) {
  return rows
    .filter((row) => row && row.login && !isAgent(row.login, row.type))
    .map((row) => ({
      login: row.login,
      name: displayName(row.login, row.name),
      href: row.html_url || `https://github.com/${row.login}`,
      contributions: Number(row.contributions) || 0,
    }))
    .sort((a, b) => b.contributions - a.contributions || a.name.localeCompare(b.name))
    .map((row, i) => ({ ...row, mark: pad(i + 1) }));
}

export function renderContributors(items) {
  const lis = items.map((person) => `                <li>
                    <span class="contrib-mark" aria-hidden="true">${person.mark}</span>
                    <a class="contrib-name" href="${person.href}">${person.name}</a>
                    <span class="contrib-role">Data Platform</span>
                </li>`).join("\n");
  return `<ol class="contributors">\n${lis}\n            </ol>`;
}

async function github(path) {
  const res = await fetch(`https://api.github.com${path}`, {
    headers: {
      accept: "application/vnd.github+json",
      "user-agent": "cryo-docs",
      "x-github-api-version": "2022-11-28",
    },
  });
  if (!res.ok) throw new Error(`GitHub ${path} returned ${res.status}`);
  return res.json();
}

async function refresh() {
  const listed = [];
  for (let page = 1; page < 20; page += 1) {
    const batch = await github(`/repos/${REPO}/contributors?per_page=100&page=${page}`);
    if (!Array.isArray(batch) || batch.length === 0) break;
    listed.push(...batch);
    if (batch.length < 100) break;
  }
  const people = [];
  for (const row of listed) {
    if (isAgent(row.login, row.type)) continue;
    const profile = await github(`/users/${row.login}`);
    people.push({
      login: row.login,
      contributions: row.contributions,
      type: row.type,
      html_url: row.html_url,
      name: profile.name || "",
    });
  }
  const body = {
    source: `https://api.github.com/repos/${REPO}/contributors`,
    fetched: new Date().toISOString().slice(0, 10),
    contributors: people,
  };
  writeFileSync(OUT, `${JSON.stringify(body, null, 2)}\n`);
  const shown = contributorItems(people);
  console.log(`wrote ${people.length} human contributor(s), showing ${shown.map((p) => p.name).join(", ")}`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  refresh().catch((err) => {
    console.error(err.message);
    process.exit(1);
  });
}
