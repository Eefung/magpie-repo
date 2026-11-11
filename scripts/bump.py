#!/usr/bin/env python3
"""Render the magpie AUR PKGBUILDs for upstream's latest release.

Upstream (yetone/magpie) ships dozens of builds a day; the AUR packages are
prebuilt-binary packages, so a bump only needs the new version plus the
published checksums -- no compilation.

    ./scripts/bump.py --check            # packaged vs upstream, and what the site serves
    ./scripts/bump.py --render           # write both PKGBUILDs for the latest
    ./scripts/bump.py --render --version 0.1.3702

Checksums come from the release's own SHA256SUMS asset (which the site's
/api/latest agrees with); the LICENSE and the icon are hashed from the bytes
they serve, so a silent upstream change cannot slip in.

Stdlib only: runs on any python3 in CI and on the user's machine alike.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES = "https://github.com/yetone/magpie-releases/releases"
SOURCE_REPO = "https://raw.githubusercontent.com/yetone/magpie"
SITE_API = "https://usemagpie.ai/api/latest"
ICON_URL = "https://usemagpie.ai/img/icon-256.png"
UA = "magpie-repo-bump (+https://github.com/Eefung/magpie-repo)"
# What pacman syncs; comparing it with packages/ is what makes a failed publish
# (committed PKGBUILD, gh-pages push never landed) repair itself on the next run.
REPO_DB = os.environ.get("REPO_DB", "https://eefung.github.io/magpie-repo/x86_64/magpie.db")
VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)+")

MAINTAINER = os.environ.get("AUR_MAINTAINER", "Eefung <eefung@tuta.io>")

# package dir -> upstream asset prefix
PACKAGES = {
    "magpie-bin": "magpie",
    "magpie-cli-bin": "magpie-cli",
}

ARCH_ASSETS = ("linux-amd64", "linux-arm64")


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch(url: str, *, missing_ok: bool = False) -> bytes | None:
    """GET with our UA; the CDN answers 403 to the default python one."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        # Only a real 404 means "upstream does not publish this". A 403 is a
        # rate limit or a policy block, and reporting it as a missing asset
        # sends the reader after the wrong problem.
        if missing_ok and exc.code == 404:
            return None
        raise


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def latest_version() -> str:
    """Upstream's newest version, taking the higher of GitHub and the site API.

    GitHub's /releases/latest skips prereleases, so a version upstream marks as
    pre-release would otherwise be invisible; the site reports whatever it
    ships. Either source alone can also be rate limited or stale.
    """
    candidates = []
    try:
        raw = fetch("https://api.github.com/repos/yetone/magpie-releases/releases/latest")
        tag = json.loads(raw or b"{}").get("tag_name", "")
        if not tag:
            raise ValueError("no tag_name in the GitHub answer")
        candidates.append(tag.lstrip("v"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log(f"warning: GitHub API unusable ({exc}); asking {SITE_API}")
    try:
        raw = fetch(SITE_API)
        site = json.loads(raw or b"{}").get("version", "")
        if site:
            candidates.append(site.lstrip("v"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log(f"warning: {SITE_API} unusable ({exc})")
    if not candidates:
        sys.exit("could not learn upstream's version from GitHub or the site API")
    return max(candidates, key=version_key)


def release_sums(version: str) -> dict[str, str]:
    """The release's SHA256SUMS, as {asset name: sha256}."""
    url = f"{RELEASES}/download/v{version}/SHA256SUMS"
    body = fetch(url, missing_ok=True)
    if body is None:
        sys.exit(f"v{version} publishes no {url.split('/')[-1]}; "
                 "pick a version whose release carries it (or bump to the latest)")
    body = body.decode()
    sums = {}
    for line in body.splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip("*")] = parts[0]
    if not sums:
        sys.exit(f"{url} lists no checksums")
    return sums


def packaged_versions() -> dict[str, str]:
    found = {}
    for pkg in PACKAGES:
        pkgbuild = ROOT / "packages" / pkg / "PKGBUILD"
        if not pkgbuild.exists():
            continue
        match = re.search(r"^pkgver=(\S+)", pkgbuild.read_text(), re.M)
        if match:
            found[pkg] = match.group(1).strip("'\"")
    return found


def stale_sha(pkg: str, key: str) -> str | None:
    """Reuse a checksum already in the PKGBUILD when upstream 404s for it."""
    pkgbuild = ROOT / "packages" / pkg / "PKGBUILD"
    if not pkgbuild.exists():
        return None
    text = pkgbuild.read_text()
    # Placeholders keep their order: license, icon, then amd64 and arm64.
    block = re.search(r"^sha256sums=\((.*?)\)", text, re.M | re.S)
    if not block:
        return None
    values = re.findall(r"'([0-9a-f]{64})'", block.group(1))
    if key in ("license", "icon") and len(values) >= 2:
        return values[0 if key == "license" else 1]
    return None


def published_versions() -> dict[str, str] | None:
    """{pkgname: version} as advertised by the published pacman database."""
    try:
        body = fetch(REPO_DB)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log(f"warning: cannot read {REPO_DB} ({exc})")
        return None
    if not body:
        return None
    found = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:*") as db:
            for member in db:
                if not member.name.endswith("/desc"):
                    continue
                handle = db.extractfile(member)
                if handle is None:
                    continue
                text = handle.read().decode("utf-8", "replace")
                name = re.search(r"^%NAME%\n(\S+)", text, re.M)
                version = re.search(r"^%VERSION%\n(\S+)", text, re.M)
                if name and version:
                    # %VERSION% carries pkgrel (0.1.3759-1); pkgver never does.
                    found[name.group(1)] = version.group(1).rsplit("-", 1)[0]
    except (tarfile.TarError, EOFError) as exc:
        log(f"warning: {REPO_DB} is not a pacman database ({exc})")
        return None
    return found or None


def render(version: str) -> None:
    # The version lands verbatim in pkgver= (and in the source URLs); a tag with
    # a dash or shell metacharacter would break makepkg or inject into it.
    if not VERSION_RE.fullmatch(version):
        sys.exit(f"refusing to render pkgver={version!r}: expected digits separated by dots")
    sums = release_sums(version)
    license_bytes = fetch(f"{SOURCE_REPO}/v{version}/LICENSE", missing_ok=True)
    icon_bytes = fetch(ICON_URL, missing_ok=True)
    if icon_bytes is None:
        sys.exit(f"could not download {ICON_URL}")
    if license_bytes is None:
        license_sha = stale_sha("magpie-bin", "license")
        log(f"warning: no LICENSE at v{version}; reusing the checksum already in "
            "packages/magpie-bin/PKGBUILD")
    else:
        license_sha = sha256_of(license_bytes)
    if not license_sha:
        sys.exit(f"no LICENSE at v{version} and none to reuse")

    for pkg, prefix in PACKAGES.items():
        template = ROOT / "templates" / f"{pkg}.PKGBUILD.in"
        values = {
            "pkgver": version,
            "maintainer": MAINTAINER,
            "sha_license": license_sha,
            "sha_icon": sha256_of(icon_bytes),
        }
        for arch in ARCH_ASSETS:
            asset = f"{prefix}-{arch}"
            if asset not in sums:
                sys.exit(f"{pkg}: the release has no {asset}")
            values["sha_" + arch] = sums[asset]
        out = ROOT / "packages" / pkg / "PKGBUILD"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(re.sub(r"\{\{([\w-]+)\}\}", lambda m: values[m.group(1)], template.read_text()))
        log(f"rendered packages/{pkg}/PKGBUILD at {version}")


def check() -> int:
    upstream = latest_version()
    packaged = packaged_versions()
    log(f"upstream={upstream}")
    for pkg, ver in sorted(packaged.items()):
        log(f"packaged {pkg}={ver}")
    published = published_versions()
    log("published=" + (" ".join(f"{pkg}={ver}" for pkg, ver in sorted(published.items()))
                        if published else "unknown"))

    if not packaged:
        log("warning: no rendered PKGBUILD yet")
        behind = sorted(PACKAGES)
        reason = "nothing rendered yet"
    else:
        behind = sorted(pkg for pkg, ver in packaged.items() if ver != upstream)
        reason = "upstream moved"
        if not behind and published is not None:
            # A run that committed the PKGBUILDs but died before pushing
            # gh-pages leaves the site behind forever: bumping on the gap is
            # what makes the next scheduled run publish again.
            behind = sorted(pkg for pkg, ver in packaged.items()
                            if published.get(pkg) != ver)
            if behind:
                reason = ("the published repository does not carry these builds ("
                          + " ".join(f"{pkg}={ver}" for pkg, ver in sorted(published.items()))
                          + "), republishing")
    log(f"bump={'1' if behind else '0'}")
    if behind:
        log(f"reason={reason}")
        log("behind=" + " ".join(behind))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare packaged and upstream versions")
    parser.add_argument("--render", action="store_true", help="rewrite the PKGBUILDs")
    parser.add_argument("--version", help="render this version instead of upstream's latest")
    args = parser.parse_args()

    try:
        if args.check and not args.render:
            return check()
        if args.render:
            render(args.version or latest_version())
            return 0
    except urllib.error.HTTPError as exc:
        sys.exit(f"HTTP {exc.code} {exc.reason} for {exc.url}")
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(f"network error: {exc}")
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
