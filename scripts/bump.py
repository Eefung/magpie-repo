#!/usr/bin/env python3
"""Render the magpie AUR PKGBUILDs for upstream's latest release.

Upstream (yetone/magpie) ships dozens of builds a day; the AUR packages are
prebuilt-binary packages, so a bump only needs the new version plus the
published checksums -- no compilation.

    ./scripts/bump.py --check            # compare packaged vs upstream version
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
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES = "https://github.com/yetone/magpie-releases/releases"
SOURCE_REPO = "https://raw.githubusercontent.com/yetone/magpie"
SITE_API = "https://usemagpie.ai/api/latest"
ICON_URL = "https://usemagpie.ai/img/icon-256.png"
UA = "magpie-aur-bump (+https://github.com/Eefung/magpie-aur)"

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
        if missing_ok and exc.code in (403, 404):
            return None
        raise


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def latest_version() -> str:
    """Newest release with assets; the site API is the fallback."""
    try:
        raw = fetch("https://api.github.com/repos/yetone/magpie-releases/releases/latest")
        tag = json.loads(raw or b"{}").get("tag_name", "")
        if tag:
            return tag.lstrip("v")
        raise ValueError("no tag_name in the GitHub answer")
    except (urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        log(f"warning: GitHub API unusable ({exc}); asking {SITE_API}")
        raw = fetch(SITE_API) or b"{}"
        version = json.loads(raw).get("version", "")
        if not version:
            sys.exit("could not learn upstream's version")
        return version.lstrip("v")


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


def render(version: str) -> None:
    sums = release_sums(version)
    license_bytes = fetch(f"{SOURCE_REPO}/v{version}/LICENSE", missing_ok=True)
    icon_bytes = fetch(ICON_URL, missing_ok=True)
    if icon_bytes is None:
        sys.exit(f"could not download {ICON_URL}")
    license_sha = sha256_of(license_bytes) if license_bytes else stale_sha("magpie-bin", "license")
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
    behind = sorted(pkg for pkg, ver in packaged.items() if ver != upstream)
    if not packaged:
        log("warning: no rendered PKGBUILD yet")
        behind = sorted(PACKAGES)
    log(f"bump={'1' if behind else '0'}")
    if behind:
        log("behind=" + " ".join(behind))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare packaged and upstream versions")
    parser.add_argument("--render", action="store_true", help="rewrite the PKGBUILDs")
    parser.add_argument("--version", help="render this version instead of upstream's latest")
    args = parser.parse_args()

    if args.check and not args.render:
        return check()
    if args.render:
        render(args.version or latest_version())
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
