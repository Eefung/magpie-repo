#!/usr/bin/env python3
"""Render the magpie PKGBUILDs for upstream's latest release.

Upstream (yetone/magpie) ships dozens of builds a day; these are
prebuilt-binary packages, so a bump only needs the new version plus the
published checksums -- no compilation.

    ./scripts/bump.py --check            # packaged vs upstream
    ./scripts/bump.py --render           # write both PKGBUILDs for the latest
    ./scripts/bump.py --packages         # the package directories to build
    ./scripts/bump.py --render --version 0.1.3702

Checksums come from the release's own SHA256SUMS asset (which the site's
/api/latest agrees with); the LICENSE and the icon are hashed from the bytes
they serve, so a silent upstream change cannot slip in.

The network enters through an injected `fetch`, so the decisions -- which
version wins, which checksum goes where, whether a template and its values
agree -- are exercised offline: python3 -m unittest discover -s tests

Each mode ends with frozen lines a caller reads instead of re-deriving it from
the files: `--check` prints `upstream=`, `packaged <pkg>=`, `bump=` and
`behind=`; `--render` prints `ver=<version>`; `--packages` prints
`package <dir> <pkgname>`, one line per package. The workflow reads those
through pkg.sh and nothing else, so the wording is a contract -- change it
with the reader.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

ROOT = Path(__file__).resolve().parent.parent
RELEASES = "https://github.com/yetone/magpie-releases/releases"
GITHUB_LATEST = "https://api.github.com/repos/yetone/magpie-releases/releases/latest"
SOURCE_REPO = "https://raw.githubusercontent.com/yetone/magpie"
SITE_API = "https://usemagpie.ai/api/latest"
ICON_URL = "https://usemagpie.ai/img/icon-256.png"
UA = "magpie-repo-bump (+https://github.com/Eefung/magpie-repo)"
VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)+")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([\w-]+)\s*\}\}")
ARCH_LINE_RE = re.compile(r"^arch=\(([^)]*)\)\s*$", re.M)
# The package() case arm that turns $CARCH into the upstream asset suffix:
#     x86_64)  _suffix=amd64 ;;
CASE_ARM_RE = re.compile(r"^\s*(\w+)\)\s*_suffix=([\w-]+)\s*;;", re.M)

DEFAULT_MAINTAINER = "Eefung <eefung@tuta.io>"

# package dir -> upstream asset prefix
PACKAGES = {
    "magpie-bin": "magpie",
    "magpie-cli-bin": "magpie-cli",
}

# repo arch -> the suffix upstream puts in its asset names (magpie-linux-amd64).
# One table: each template's arch=() and its sha_* placeholders are held to it,
# so an arch cannot be added on one side only.
ARCHES = {
    "x86_64": "linux-amd64",
    "aarch64": "linux-arm64",
}


@dataclass(frozen=True)
class Layout:
    """What the renderer needs to know about this repository, handed in.

    Every one of these was reachable from inside render() as a module global,
    so a caller had to learn four globals to use two parameters, and a test
    could only point the render at a temporary tree by monkeypatching the
    module. The maintainer is read from the environment in main(), when the
    layout is built -- not when this file is imported.
    """

    templates: Path
    packages_dir: Path
    inventory: Mapping[str, str]
    maintainer: str
    root: Path


def layout_for(
    root: Path = ROOT,
    *,
    templates: Path | None = None,
    packages_dir: Path | None = None,
    maintainer: str = DEFAULT_MAINTAINER,
    inventory: Mapping[str, str] | None = None,
) -> Layout:
    """The one way a Layout is built, for production and for tests alike.

    A test that needs the output somewhere else, or a different template tree,
    overrides that one root and leaves the rest of the production defaults
    alone. Before this, tests hand-assembled a Layout, so every default lived
    twice and a field added here had to be added there too.
    """
    return Layout(
        templates=root / "templates" if templates is None else templates,
        packages_dir=root / "packages" if packages_dir is None else packages_dir,
        inventory=dict(PACKAGES if inventory is None else inventory),
        maintainer=maintainer,
        root=root,
    )


class BumpError(Exception):
    """A failure main() reports as one line, not as a traceback."""


class Fetch(Protocol):
    """The module's only door to the network; tests supply a fake here."""

    def __call__(self, url: str, *, missing_ok: bool = False) -> bytes | None: ...


def log(msg: str) -> None:
    print(msg, flush=True)


def http_fetch(url: str, *, missing_ok: bool = False) -> bytes | None:
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


def highest(versions: Sequence[str]) -> str:
    """The greatest dotted version here; blank and malformed entries drop out.

    Dotted versions compared as text rank 0.1.999 above 0.1.1000, so the parts
    are compared as numbers. A tag carrying a dash is a prerelease upstream has
    not shipped for real, so it is not a candidate.
    """
    usable = [v for v in versions if VERSION_RE.fullmatch(v)]
    if not usable:
        offered = ", ".join(repr(v) for v in versions) if versions else "nothing at all"
        raise BumpError(f"upstream offered no usable version: {offered}")
    return max(usable, key=lambda v: tuple(int(part) for part in v.split(".")))


def latest_version(fetch: Fetch = http_fetch) -> str:
    """Upstream's newest version, taking the higher of GitHub and the site API.

    GitHub's /releases/latest skips prereleases, so a version upstream marks as
    pre-release would otherwise be invisible; the site reports whatever it
    ships. Either source alone can also be rate limited or stale.
    """
    candidates = []
    try:
        raw = fetch(GITHUB_LATEST)
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
        raise BumpError("could not learn upstream's version from GitHub or the site API")
    return highest(candidates)


def release_sums(version: str, *, fetch: Fetch = http_fetch) -> dict[str, str]:
    """The release's SHA256SUMS, as {asset name: sha256}."""
    url = f"{RELEASES}/download/v{version}/SHA256SUMS"
    body = fetch(url, missing_ok=True)
    if body is None:
        raise BumpError(
            f"v{version} publishes no SHA256SUMS; "
            "pick a version whose release carries it (or bump to the latest)"
        )
    sums = {}
    for line in body.decode().splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip("*")] = parts[0]
    if not sums:
        raise BumpError(f"{url} lists no checksums")
    return sums


def placeholders(template: str) -> list[str]:
    """The {{names}} this template asks for, in order, each named once."""
    return list(dict.fromkeys(PLACEHOLDER_RE.findall(template)))


def template_arches(template: str) -> list[str]:
    """The repo arches a template declares in its arch=() line."""
    match = ARCH_LINE_RE.search(template)
    if match is None:
        raise BumpError("the template declares no arch=()")
    return re.findall(r"'([^']+)'", match.group(1))


def template_suffixes(template: str) -> dict[str, str]:
    """The arch -> source suffix map a template's package() case block declares."""
    return dict(CASE_ARM_RE.findall(template))


def check_arches(pkg: str, template: str, names: Sequence[str]) -> None:
    """Hold a template's every arch-shaped part to the table, before fetching.

    Every package targets the same repo arches; a package that does not is a
    change to ARCHES, not a template exception. Both directions bite, because
    the arch a package claims and the checksum and source it carries have to be
    about the same set: an arch added to the table with no template behind it,
    and a template arch the table never heard of.

    The table is restated in the template four ways, so all four are checked:
    arch=(), source_{arch}=, sha256sums_{arch}= with its {{sha_suffix}}
    placeholder, and the package() case block that turns $CARCH into the same
    suffix. Only the first, third and fourth used to be held; a table entry
    whose source_ array was forgotten rendered clean and failed inside the
    container, as a makepkg error pointing at something else.
    """
    declared = template_arches(template)
    unknown = sorted(set(declared) - set(ARCHES))
    if unknown:
        raise BumpError(
            f"{pkg}: the template declares {', '.join(unknown)}, "
            "which the arch table knows no upstream suffix for"
        )
    missing = sorted(set(ARCHES) - set(declared))
    if missing:
        raise BumpError(f"{pkg}: the template leaves out {', '.join(missing)}")

    suffixes = template_suffixes(template)
    strangers = sorted(set(suffixes) - set(ARCHES))
    if strangers:
        raise BumpError(
            f"{pkg}: package() has a case arm for {', '.join(strangers)}, "
            "which the arch table never heard of"
        )

    for arch, suffix in ARCHES.items():
        if f"source_{arch}=" not in template:
            raise BumpError(f"{pkg}: the template has no source_{arch}= array")
        if f"sha256sums_{arch}=" not in template:
            raise BumpError(f"{pkg}: the template has no sha256sums_{arch}= array")
        if f"sha_{suffix}" not in names:
            raise BumpError(
                f"{pkg}: the template has no {{{{sha_{suffix}}}}} placeholder for {arch}"
            )
        # The case block is the fourth copy of the mapping, stripped of the
        # `linux-` that only upstream's asset names carry.
        want = suffix.removeprefix("linux-")
        have = suffixes.get(arch)
        if have is None:
            raise BumpError(f"{pkg}: package() has no case arm for {arch}")
        if have != want:
            raise BumpError(
                f"{pkg}: package() maps {arch} to {have}, "
                f"but the arch table says {want} ({suffix})"
            )


def render_pkgbuild(template: str, values: Mapping[str, str]) -> str:
    """Fill a template, holding both sides to the same contract.

    A template may not ask for what nothing supplies, a value nothing asks for
    is the caller's mistake, and a marker left behind (a typo like ``{{pkgver}``)
    must not reach makepkg.
    """
    wanted = placeholders(template)
    unsupplied = [name for name in wanted if name not in values]
    if unsupplied:
        raise BumpError(
            "the template wants "
            + ", ".join(f"{{{{{name}}}}}" for name in unsupplied)
            + ", which nothing supplies"
        )

    rendered = PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], template)
    # Check for a stranded marker before the unused values: a typo like
    # ``{{pkgver}`` never registers as a placeholder, so the value it was meant
    # to receive would otherwise be reported as "nothing uses this".
    stray = rendered.find("{{")
    if stray != -1:
        raise BumpError(f"unrendered marker near {rendered[stray:stray + 40]!r}")

    unused = sorted(name for name in values if name not in wanted)
    if unused:
        raise BumpError("nothing in the template uses " + ", ".join(unused))
    return rendered


def packaged_versions(layout: Layout) -> dict[str, str]:
    found = {}
    for pkg in layout.inventory:
        pkgbuild = layout.packages_dir / pkg / "PKGBUILD"
        if not pkgbuild.exists():
            continue
        match = re.search(r"^pkgver=(\S+)", pkgbuild.read_text(), re.M)
        if match:
            found[pkg] = match.group(1).strip("'\"")
    return found


def layout_path(layout: Layout, path: Path) -> str:
    """A path named the way the layout names it, not the way the caller's cwd does.

    The 包清单 leaves this module and is read by a container whose cwd happens
    to be the repository root. Deriving the answer from the caller's cwd made
    the value true only when it was run from there, and a test had to chdir to
    make the strings line up -- an interface that quietly required a working
    directory.
    """
    try:
        return str(path.relative_to(layout.root))
    except ValueError:
        return str(path)


def package_entries(layout: Layout) -> list[tuple[str, str]]:
    """The 包清单 as (directory, pkgname), in inventory order.

    The build step used to spell the directories out, so a package added to the
    inventory was rendered and committed but never packaged, and nothing
    compared the two lists: the next `--check` saw it current and printed
    `bump=0` for good. The first fix put only the directories on the line, so
    both consumers read the pkgname back out of the PKGBUILD -- a second source
    for a fact this module already holds as its keys. The line carries both now.
    """
    return [
        (layout_path(layout, layout.packages_dir / pkg), pkg)
        for pkg in layout.inventory
    ]


def render(
    version: str,
    *,
    layout: Layout,
    fetch: Fetch = http_fetch,
) -> None:
    # The version lands verbatim in pkgver= (and in the source URLs); a tag with
    # a dash or shell metacharacter would break makepkg or inject into it.
    if not VERSION_RE.fullmatch(version):
        raise BumpError(f"refusing to render pkgver={version!r}: expected digits separated by dots")

    # Read every template and hold it to the arch table before the network is
    # asked anything: a template that lags the table is this repository's
    # problem, and it should not be reported as "the release has no such asset".
    plans = []
    for pkg, prefix in layout.inventory.items():
        path = layout.templates / f"{pkg}.PKGBUILD.in"
        try:
            template = path.read_text()
        except OSError as exc:
            # A layout is handed in now, so it can point at a tree that has no
            # template for a package in its own inventory. Say which.
            raise BumpError(f"no template for {pkg}: {path} ({exc.strerror})") from exc
        names = placeholders(template)
        check_arches(pkg, template, names)
        plans.append((pkg, prefix, template, names))

    sums = release_sums(version, fetch=fetch)

    # Fetch by what the templates ask for, not by what upstream offers: a
    # package that ships no icon must not be held up by an icon it never
    # installs.
    wanted = {name for _, _, _, names in plans for name in names}

    license_bytes = None
    if "sha_license" in wanted:
        license_bytes = fetch(f"{SOURCE_REPO}/v{version}/LICENSE", missing_ok=True)
        if license_bytes is None:
            raise BumpError(f"v{version} publishes no LICENSE; pick a tag that has one")

    icon_bytes = None
    if "sha_icon" in wanted:
        icon_bytes = fetch(ICON_URL, missing_ok=True)
        if icon_bytes is None:
            raise BumpError(f"could not download {ICON_URL}")

    # Render everything before writing anything: a template that disagrees with
    # its values then leaves the tree alone instead of half-updated.
    rendered = []
    for pkg, prefix, template, names in plans:
        supplied = {
            "pkgver": version,
            "maintainer": layout.maintainer,
            "sha_license": sha256_of(license_bytes) if license_bytes is not None else None,
            "sha_icon": sha256_of(icon_bytes) if icon_bytes is not None else None,
        }
        values = {
            name: value for name, value in supplied.items()
            if name in names and value is not None
        }
        for suffix in ARCHES.values():
            asset = f"{prefix}-{suffix}"
            if asset not in sums:
                raise BumpError(f"{pkg}: the release has no {asset}")
            values[f"sha_{suffix}"] = sums[asset]
        rendered.append((pkg, render_pkgbuild(template, values)))

    for pkg, text in rendered:
        out = layout.packages_dir / pkg / "PKGBUILD"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        log(f"rendered {layout_path(layout, out)} at {version}")

    # The version this module just wrote, handed to the caller. Every later
    # step needs it, and re-deriving it from the PKGBUILD gave the pipeline a
    # second, untested reader of a file this one had already parsed.
    log(f"ver={version}")


def check(*, layout: Layout, fetch: Fetch = http_fetch) -> int:
    upstream = latest_version(fetch=fetch)
    packaged = packaged_versions(layout)
    log(f"upstream={upstream}")
    for pkg, ver in sorted(packaged.items()):
        log(f"packaged {pkg}={ver}")

    if not packaged:
        log("warning: no rendered PKGBUILD yet")
    # Walk the inventory, not what happens to be on disk: a package whose
    # PKGBUILD was never rendered, or was lost, is behind just as much as a
    # stale one. Reading `packaged` alone would report the tree current
    # forever.
    behind = sorted(pkg for pkg in layout.inventory if packaged.get(pkg) != upstream)
    log(f"bump={'1' if behind else '0'}")
    if behind:
        log("behind=" + " ".join(behind))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="compare packaged and upstream versions")
    mode.add_argument("--render", action="store_true", help="rewrite the PKGBUILDs")
    mode.add_argument("--packages", action="store_true", help="print the package directories to build")
    parser.add_argument("--version", help="render this version instead of upstream's latest")
    args = parser.parse_args(argv)

    try:
        layout = layout_for(maintainer=os.environ.get("MAINTAINER", DEFAULT_MAINTAINER))
        if args.packages:
            for directory, pkg in package_entries(layout):
                log(f"package {directory} {pkg}")
            return 0
        if args.check:
            return check(layout=layout)
        render(args.version or latest_version(), layout=layout)
        return 0
    except BumpError as exc:
        sys.exit(str(exc))
    except urllib.error.HTTPError as exc:
        sys.exit(f"HTTP {exc.code} {exc.reason} for {exc.url}")
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(f"network error: {exc}")


if __name__ == "__main__":
    sys.exit(main())
