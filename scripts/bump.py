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

Three facts leave through frozen lines, because a caller reads them instead of
re-deriving them from the files: `--render` prints `ver=<version>`, `--check`
prints `upstream=<version>` (the 版本 the render is supposed to take, so the
render never asks 上游 a second time), and `--check` prints `bump=0|1`.
`--packages` prints `package <dir> <pkgname>`, one line per package, read
through pkglist_field. Those are contracts -- change one with its reader in
pkg.sh. The rest of `--check`'s output (`packaged <pkg>=`, `behind=`) is a log
for whoever reads the run: reword it freely, and let the tests assert what it
means rather than how it reads.

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
GITHUB_RELEASES = "https://api.github.com/repos/yetone/magpie-releases/releases?per_page=30"
SOURCE_REPO = "https://raw.githubusercontent.com/yetone/magpie"
SITE_API = "https://usemagpie.ai/api/latest"
ICON_URL = "https://usemagpie.ai/img/icon-256.png"
UA = "magpie-repo-bump (+https://github.com/Eefung/magpie-repo)"
VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)+")
PKGNAME_RE = re.compile(r"^pkgname=(\S+)", re.M)
PLACEHOLDER_RE = re.compile(r"\{\{\s*([\w-]+)\s*\}\}")
ARCH_LINE_RE = re.compile(r"^arch=\(([^)]*)\)\s*$", re.M)
# The package() case arm that turns $CARCH into the upstream asset suffix:
#     x86_64)  _suffix=amd64 ;;
CASE_ARM_RE = re.compile(r"^\s*(\w+)\)\s*_suffix=([\w-]+)\s*;;", re.M)
# A source_<arch>=() array, anchored at the line start so a commented-out
# array is not mistaken for a live one. Both shapes the templates use land
# here: one entry per line, and the whole array on one line.
SOURCE_ARRAY_RE = r"^source_{arch}=\((.*?)\)\s*$"

DEFAULT_MAINTAINER = "Eefung <eefung@tuta.io>"

# package dir -> upstream asset prefix
PACKAGES = {
    "magpie-bin": "magpie",
    "magpie-cli-bin": "magpie-cli",
}

# repo arch -> the suffix upstream puts in its asset names (magpie-linux-amd64).
# One table: each template's arch=() and its sha_* placeholders are held to it,
# so an arch cannot be added on one side only. It is the default layout_for
# hands to the render; nothing inside render() or check_arches reads it as a
# module global any more, which is what made "a different arch table" untestable
# without monkeypatching.
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
    module. The arch table is here for the same reason, one refactor later: it
    was the fifth global render() and check_arches both read. The maintainer is
    read from the environment in main(), when the layout is built -- not when
    this file is imported.
    """

    templates: Path
    packages_dir: Path
    inventory: Mapping[str, str]
    arches: Mapping[str, str]
    maintainer: str
    root: Path


def layout_for(
    root: Path = ROOT,
    *,
    templates: Path | None = None,
    packages_dir: Path | None = None,
    maintainer: str = DEFAULT_MAINTAINER,
    inventory: Mapping[str, str] | None = None,
    arches: Mapping[str, str] | None = None,
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
        arches=dict(ARCHES if arches is None else arches),
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


def version_key(version: str) -> tuple[int, ...]:
    """A 版本's sort key: the parts as numbers, so 0.1.1000 outranks 0.1.999."""
    return tuple(int(part) for part in version.split("."))


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
    return max(usable, key=version_key)


def offered_versions(fetch: Fetch = http_fetch) -> list[str]:
    """Every usable 版本 上游 offers, newest first.

    The release list is the source that knows more than "the latest": a tag
    exists before its assets do, so the release *before* the newest one can be
    the newest thing this run can 打包. The site API answers with a single
    version and is asked too -- either source can be rate limited or stale, and
    the two can disagree -- which is why the caller walks the list instead of
    taking the maximum of two readings.
    """
    candidates = []
    try:
        raw = fetch(GITHUB_RELEASES)
        releases = json.loads(raw or b"[]")
        if not isinstance(releases, list):
            raise ValueError("the GitHub answer is not a list of releases")
        candidates.extend(
            str(release.get("tag_name", "")).lstrip("v")
            for release in releases
            if isinstance(release, dict) and not release.get("draft")
        )
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
    highest(candidates)  # names 上游's junk when none of the candidates is usable
    usable = {v for v in candidates if VERSION_RE.fullmatch(v)}
    return sorted(usable, key=version_key, reverse=True)


def uploaded_sums(version: str, *, fetch: Fetch = http_fetch) -> dict[str, str] | None:
    """The release's SHA256SUMS, or None when 上游 has not uploaded it yet.

    A tag exists before its assets do: the release page appears first and the
    uploads land seconds to minutes later. That is not a broken repository and
    not a broken release -- it is a 版本 this run cannot 打包 yet.
    """
    url = f"{RELEASES}/download/v{version}/SHA256SUMS"
    body = fetch(url, missing_ok=True)
    if body is None:
        return None
    sums = {}
    for line in body.decode().splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip("*")] = parts[0]
    if not sums:
        raise BumpError(f"{url} lists no checksums")
    return sums


def release_sums(version: str, *, fetch: Fetch = http_fetch) -> dict[str, str]:
    """The release's SHA256SUMS, as {asset name: sha256}.

    Called with a version the caller asked for by hand, so an unuploaded one is
    that caller's mistake and says which version to pick instead.
    """
    sums = uploaded_sums(version, fetch=fetch)
    if sums is None:
        raise BumpError(
            f"v{version} publishes no SHA256SUMS; "
            "pick a version whose release carries it (or bump to the latest)"
        )
    return sums


def latest_version(fetch: Fetch = http_fetch) -> str:
    """Upstream's newest 版本 this run can 打包: its release is uploaded.

    The tag and the assets do not arrive together, so the newest 版本 上游
    advertises can be one whose SHA256SUMS is still a 404. Taking the maximum of
    the two sources and hoping was how a run holding a complete release
    published nothing: `--check` said bump, `--render` asked a second time, and
    the second reading landed on a tag with no assets yet.
    """
    for version in offered_versions(fetch=fetch):
        if uploaded_sums(version, fetch=fetch) is not None:
            return version
    raise BumpError(
        "upstream has tagged versions, but none has its SHA256SUMS uploaded yet"
    )


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


def source_entries(template: str, arch: str) -> list[tuple[str, str]] | None:
    """The (file name, url) pairs a template's source_<arch>=() array carries.

    None means the template has no such array at all. The array is read rather
    than probed for with a substring: `source_aarch64=` appears in a comment as
    readily as in the file, and the two facts inside the array -- what the file
    is called and where it comes from -- are both restatements of the arch
    table.
    """
    match = re.search(
        SOURCE_ARRAY_RE.format(arch=re.escape(arch)), template, re.S | re.M
    )
    if match is None:
        return None
    entries = []
    for line in match.group(1).splitlines():
        line = line.strip().rstrip(",").strip()
        if not line:
            continue
        name, _, url = line.partition("::")
        entries.append((name.strip().strip("'\""), url.strip().strip("'\"")))
    return entries


def check_pkgname(pkg: str, template: str) -> None:
    """The template's literal pkgname= is the inventory key it is filed under.

    That key is what the 包清单 publishes and, on the build side, what
    `makepkg --packagelist` puts in the 包文件 name. Nothing used to hold the
    two together: a template naming another package rendered clean, the build
    produced files under the template's name, and the release step -- asking
    for the inventory's -- found nothing, after gh-pages had already been
    pushed. Checked here, before the network, with the arch table.
    """
    match = PKGNAME_RE.search(template)
    if match is None:
        raise BumpError(f"{pkg}: the template declares no pkgname=")
    declared = match.group(1).strip("'\"")
    if declared != pkg:
        raise BumpError(
            f"{pkg}: the template names {declared}, "
            f"but the 包清单 files it as {pkg}"
        )


def check_arches(pkg: str, template: str, arches: Mapping[str, str]) -> None:
    """Hold a template's every arch-shaped part to the table, before fetching.

    Every package targets the same repo arches; a package that does not is a
    change to the arch table, not a template exception. Both directions bite,
    because the arch a package claims and the checksum and source it carries
    have to be about the same set: an arch added to the table with no template
    behind it, and a template arch the table never heard of.

    The mapping is restated in the template five ways, so all five are checked:
    arch=(), the source_<arch>= array (its key, the file name it gives the
    download, and the asset its URL ends in), sha256sums_<arch>= with its
    {{sha_suffix}} placeholder, and the package() case block that turns $CARCH
    into the same suffix. The source array used to be probed for with a
    substring, so a commented-out array passed and an aarch64 entry pointing at
    the amd64 asset rendered clean -- it took a checksum mismatch inside the
    container to say so.

    The template's placeholders are read here rather than handed in: they follow
    from the template, so asking the caller for them was an interface that could
    disagree with the file it was describing.
    """
    names = placeholders(template)
    declared = template_arches(template)
    unknown = sorted(set(declared) - set(arches))
    if unknown:
        raise BumpError(
            f"{pkg}: the template declares {', '.join(unknown)}, "
            "which the arch table knows no upstream suffix for"
        )
    missing = sorted(set(arches) - set(declared))
    if missing:
        raise BumpError(f"{pkg}: the template leaves out {', '.join(missing)}")

    suffixes = template_suffixes(template)
    strangers = sorted(set(suffixes) - set(arches))
    if strangers:
        raise BumpError(
            f"{pkg}: package() has a case arm for {', '.join(strangers)}, "
            "which the arch table never heard of"
        )

    for arch, suffix in arches.items():
        entries = source_entries(template, arch)
        if entries is None:
            raise BumpError(f"{pkg}: the template has no source_{arch}= array")
        # The case block is the fifth copy of the mapping, stripped of the
        # `linux-` that only upstream's asset names carry. The source array's
        # file name is stripped the same way.
        want = suffix.removeprefix("linux-")
        for name, url in entries:
            if not name.endswith(want):
                raise BumpError(
                    f"{pkg}: source_{arch} names {name!r}, "
                    f"which does not end in {want!r} ({suffix})"
                )
            if not url.endswith(suffix):
                raise BumpError(
                    f"{pkg}: source_{arch} downloads {url}, "
                    f"which is not a {suffix} asset"
                )
        if f"sha256sums_{arch}=" not in template:
            raise BumpError(f"{pkg}: the template has no sha256sums_{arch}= array")
        if f"sha_{suffix}" not in names:
            raise BumpError(
                f"{pkg}: the template has no {{{{sha_{suffix}}}}} placeholder for {arch}"
            )
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

    # Read every template and hold it to the arch table and to its own pkgname
    # before the network is asked anything: a template that lags the table, or
    # is filed under another name, is this repository's problem, and it should
    # not be reported as "the release has no such asset" or found after the
    # gh-pages push.
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
        check_pkgname(pkg, template)
        check_arches(pkg, template, layout.arches)
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
        for suffix in layout.arches.values():
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


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch: Fetch = http_fetch,
    layout: Layout | None = None,
) -> int:
    """The entry point CI calls: argv in, 冻结行 and an exit code out.

    argv, fetch and the layout are parameters so a test can drive the same door
    the workflow drives. Before this, `fetch` stopped one level below: the
    tests went in through check() and render() directly, and the two things CI
    actually reads -- the frozen lines and the exit code -- were never run.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="compare packaged and upstream versions")
    mode.add_argument("--render", action="store_true", help="rewrite the PKGBUILDs")
    mode.add_argument("--packages", action="store_true", help="print the package directories to build")
    parser.add_argument("--version", help="render this version instead of upstream's latest")
    args = parser.parse_args(argv)
    # --version used to be accepted beside --check and silently ignored, which
    # reads as "check this version" and does nothing of the sort.
    if args.version and not args.render:
        parser.error("--version only applies to --render")

    try:
        if layout is None:
            layout = layout_for(maintainer=os.environ.get("MAINTAINER", DEFAULT_MAINTAINER))
        if args.packages:
            for directory, pkg in package_entries(layout):
                log(f"package {directory} {pkg}")
            return 0
        if args.check:
            return check(layout=layout, fetch=fetch)
        render(args.version or latest_version(fetch=fetch), layout=layout, fetch=fetch)
        return 0
    except BumpError as exc:
        sys.exit(str(exc))
    except urllib.error.HTTPError as exc:
        sys.exit(f"HTTP {exc.code} {exc.reason} for {exc.url}")
    except (urllib.error.URLError, OSError) as exc:
        sys.exit(f"network error: {exc}")


if __name__ == "__main__":
    sys.exit(main())
