#!/usr/bin/env python3
"""Pin Cryo's release images by digest in a Helm values file, and check every pin is pullable.

Built after a production incident, 2026-11-12. Three hand edits in a row pasted image digests
into the wrong fields: the `cryo` app digest into `spark.k8s.image.digest`, a `cryo-spark`
digest into `web.image.digest`, and a value that exists in no registry into
`redis.image.digest`. Each digest was real somewhere, so nothing failed until Kubernetes
tried to pull. Redis, the UI and every Spark job went to ImagePullBackOff.

A digest only identifies an image inside the repository that built it. This script never
lets a value cross repositories:

  pin    Resolves the digest of ghcr.io/tech-pw/{cryo,cryo-spark,cryo-web} at one git ref and
         writes each one only under the block whose `repository:` names that image. Blocks for
         other repositories (redis, your own mirror) are left alone.
  check  For every `repository:` + `digest:` pair in the file, asks that repository's
         registry whether the digest exists. Exits 1 if any doesn't. Run it in CI before a
         deploy.

Text-based, not a YAML round-trip, so the comments in a values file survive.

    python deploy/pin_release_images.py pin --ref 84496ce... --file cryo-values-production.yaml
    python deploy/pin_release_images.py check --file cryo-values-production.yaml
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RELEASE_REPOS = (
    "ghcr.io/tech-pw/cryo",
    "ghcr.io/tech-pw/cryo-spark",
    "ghcr.io/tech-pw/cryo-web",
)
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPO_LINE = re.compile(r'^(\s*)repository:\s*"?([^"\s#]+)"?\s*(#.*)?$')
_DIGEST_LINE = re.compile(r'^(\s*)digest:\s*"?([^"\s#]*)"?\s*(#.*)?$')
_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


@dataclass(frozen=True)
class Pin:
    line: int  # 0-based index of the digest line
    path_hint: str  # the repository line's indentation-based owner, for messages
    repository: str
    digest: str


def find_pins(text: str) -> list[Pin]:
    """Every digest line paired with the repository declared in the same block."""
    lines = text.split("\n")
    pins: list[Pin] = []
    for i, line in enumerate(lines):
        repo = _REPO_LINE.match(line)
        if not repo:
            continue
        indent = len(repo.group(1))
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                continue
            nxt_indent = len(nxt) - len(nxt.lstrip(" "))
            if nxt_indent < indent:
                break
            if nxt_indent == indent:
                digest = _DIGEST_LINE.match(nxt)
                if digest:
                    pins.append(Pin(j, _owner(lines, i, indent), repo.group(2), digest.group(2)))
                    break
                if _REPO_LINE.match(nxt):
                    break
    return pins


def _owner(lines: list[str], repo_line: int, indent: int) -> str:
    keys: list[str] = []
    want = indent
    for k in range(repo_line - 1, -1, -1):
        line = lines[k]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        line_indent = len(line) - len(line.lstrip(" "))
        if line_indent < want and line.rstrip().endswith(":"):
            keys.append(line.strip()[:-1])
            want = line_indent
            if want == 0:
                break
    return ".".join(reversed(keys)) or "<top>"


def pin(text: str, digests: dict[str, str]) -> tuple[str, list[str]]:
    """Write each repository's digest only into blocks that name that repository."""
    lines = text.split("\n")
    changed: list[str] = []
    for p in find_pins(text):
        new = digests.get(p.repository)
        if new is None:
            continue
        m = _DIGEST_LINE.match(lines[p.line])
        assert m is not None
        comment = f"  {m.group(3)}" if m.group(3) else ""
        lines[p.line] = f'{m.group(1)}digest: "{new}"{comment}'
        changed.append(f"{p.path_hint} ({p.repository})")
    return "\n".join(lines), changed


def _split(repository: str) -> tuple[str, str]:
    first, _, rest = repository.partition("/")
    if "." in first or ":" in first or first == "localhost":
        host, name = first, rest
    else:
        host, name = "registry-1.docker.io", repository
    if host in {"docker.io", "index.docker.io"}:
        host = "registry-1.docker.io"
    if host == "registry-1.docker.io" and "/" not in name:
        name = f"library/{name}"
    return host, name


def _request(url: str, method: str, token: str | None) -> urllib.request.Request:
    req = urllib.request.Request(url, method=method, headers={"Accept": _ACCEPT})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def _token(challenge: str) -> str | None:
    params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    realm = params.pop("realm", None)
    if not realm:
        return None
    with urllib.request.urlopen(f"{realm}?{urllib.parse.urlencode(params)}", timeout=30) as r:
        body = json.load(r)
    token = body.get("token") or body.get("access_token")
    return str(token) if token else None


def registry_manifest(repository: str, reference: str, method: str = "HEAD") -> str | None:
    """The manifest digest for repository:reference (anonymous pull), or None if missing."""
    host, name = _split(repository)
    url = f"https://{host}/v2/{name}/manifests/{reference}"
    token = None
    for _ in range(2):
        try:
            with urllib.request.urlopen(_request(url, method, token), timeout=30) as r:
                return r.headers.get("Docker-Content-Digest") or reference
        except urllib.error.HTTPError as err:
            if err.code == 401 and token is None:
                token = _token(err.headers.get("WWW-Authenticate", ""))
                if token:
                    continue
            if err.code in (401, 403, 404):
                return None
            raise
    return None


def check(text: str, exists: Callable[[str, str], bool]) -> list[str]:
    problems: list[str] = []
    for p in find_pins(text):
        if not p.digest:
            continue
        if not _DIGEST_RE.match(p.digest):
            problems.append(f"{p.path_hint}: {p.digest!r} is not sha256:<64 hex>")
        elif not exists(p.repository, p.digest):
            problems.append(f"{p.path_hint}: {p.repository}@{p.digest} does not exist")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_pin = sub.add_parser("pin", help="pin cryo, cryo-spark and cryo-web to one git ref")
    p_pin.add_argument("--ref", required=True, help="git commit SHA or release tag (e.g. v0.2.1)")
    p_pin.add_argument("--file", required=True)
    p_pin.add_argument("--dry-run", action="store_true")
    p_check = sub.add_parser("check", help="verify every pinned digest exists in its repository")
    p_check.add_argument("--file", required=True)
    args = parser.parse_args()

    path = Path(args.file)
    text = path.read_text()

    if args.cmd == "check":
        problems = check(text, lambda repo, d: registry_manifest(repo, d) is not None)
        for msg in problems:
            print(f"error: {msg}", file=sys.stderr)
        if problems:
            return 1
        print(f"ok: {len(find_pins(text))} pinned image(s) exist in their repositories")
        return 0

    digests: dict[str, str] = {}
    for repo in RELEASE_REPOS:
        digest = registry_manifest(repo, args.ref, method="HEAD")
        if not digest or not _DIGEST_RE.match(digest):
            print(f"error: {repo}:{args.ref} not found", file=sys.stderr)
            return 1
        digests[repo] = digest
    updated, changed = pin(text, digests)
    if not changed:
        print(f"error: no block in {path} names {', '.join(RELEASE_REPOS)}", file=sys.stderr)
        return 1
    for repo, digest in digests.items():
        print(f"{repo}:{args.ref} -> {digest}")
    print(("would update: " if args.dry_run else "updated: ") + ", ".join(changed))
    if not args.dry_run:
        path.write_text(updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
