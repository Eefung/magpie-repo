"""Render the magpie PKGBUILDs for upstream's latest release.

Upstream (yetone/magpie) ships dozens of builds a day; these are
prebuilt-binary packages, so a bump only needs the new version plus the
published checksums -- no compilation.

    ./scripts/bump.py --check            # packaged vs upstream
    ./scripts/bump.py --render           # write both PKGBUILDs for the latest
    ./scripts/bump.py --packages         # the package directories to build
    ./scripts/bump.py --render --version 0.1.3702

Checksums come from the release's own SHA256SUMS asset (which the site's
/api/latest agrees with). The LICENSE and the icon are hashed from the bytes
the URLs in the template serve -- the template decides what makepkg downloads,
so the digest is of those bytes -- and a silent upstream change cannot slip in.

The network enters through an injected `fetch`, so the decisions -- which
version wins, which checksum goes where, whether a template and its values
agree -- are exercised offline: python3 -m unittest discover -s tests

Three facts leave through frozen lines, because a caller reads them instead of
re-deriving them from the files: `--render` prints `ver=<version>`, `--check`
prints `upstream=<version>` (the 版本 the render is supposed to take, so the
render never asks 上游 a second time), and `--check` prints `bump=0|1`.
`--packages` prints `package <dir> <pkgname>`, one line per package, read
through pkglist_field. Those are contracts -- change one with its reader in
pkg.sh. The rest of `--check`'s output (`packaged <pkg>=`, `served <pkg>=`,
`behind <pkg>=<reason>`) is a log for whoever reads the run: reword it freely,
and let the tests assert what it means rather than how it reads.

Stdlib only: runs on any python3 in CI and on the user's machine alike.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

ROOT = Path(__file__).resolve().parent.parent
RELEASES = "https://github.com/yetone/magpie-releases/releases"
GITHUB_RELEASES = "https://api.github.com/repos/yetone/magpie-releases/releases?per_page=30"
SITE_API = "https://usemagpie.ai/api/latest"
UA = "magpie-repo-bump (+https://github.com/Eefung/magpie-repo)"
VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)+")
PKGNAME_RE = re.compile(r"^pkgname=(\S+)", re.M)
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
# so an arch cannot be added on one side only. It is the default layout_for
# hands to the render; nothing inside render() or read_template reads it as a
# module global any more, which is what made "a different arch table" untestable
# without monkeypatching.
#
# Only what this repository builds and 载荷核对s is here (ADR-0003). aarch64 used
# to be listed beside it while CI built x86_64 alone: the render asked upstream
# for an arm64 asset nobody built, so a release that stopped publishing one
# would have stopped x86_64 too -- an arch we never shipped holding the switch
# for the one we do.
ARCHES = {
    "x86_64": "linux-amd64",
}


@dataclass(frozen=True)
class Served:
    """Where this 软件源 is served: the three names the workflow declares.

    The entry check compares the tree against the database pacman reads, so it
    needs the URL, the section (which is the database's file name) and the
    served arch directory. They travel together -- the block a user pastes is
    the same three -- and a layout without them (a hand run that declares
    nothing) simply compares against 上游 alone.
    """

    url: str
    section: str
    arch: str

    @property
    def database(self) -> str:
        """The URL of the database: what pacman reads, so what was published."""
        return f"{self.url}/{self.arch}/{self.section}.db.tar.gz"


@dataclass(frozen=True)
class Layout:
    """What the renderer needs to know about this repository, handed in.

    Every one of these was reachable from inside render() as a module global,
    so a caller had to learn four globals to use two parameters, and a test
    could only point the render at a temporary tree by monkeypatching the
    module. The arch table is here for the same reason, one refactor later: it
    was the fifth global render() and read_template both read. The maintainer is
    read from the environment in main(), when the layout is built -- not when
    this file is imported.

    `served` is Optional because it is not a fact about the tree: a hand run
    that declares no address still answers "is this tree 落后", it just cannot
    answer "does what is served match it".
    """

    templates: Path
    packages_dir: Path
    inventory: Mapping[str, str]
    arches: Mapping[str, str]
    maintainer: str
    root: Path
    served: Served | None = None


def layout_for(
    root: Path = ROOT,
    *,
    templates: Path | None = None,
    packages_dir: Path | None = None,
    maintainer: str = DEFAULT_MAINTAINER,
    inventory: Mapping[str, str] | None = None,
    arches: Mapping[str, str] | None = None,
    served: Served | None = None,
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
        served=served,
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


def array_entries(template: str, key: str) -> list[str] | None:
    """The entries of a `key=(...)` array, in order and unquoted, or None.

    The one reader for every array a template declares. Anchored at the line
    start, so a commented-out array is not mistaken for a live one, and read to
    the closing paren.

    The body is tokenized the way bash reads it, not split on newlines: the
    templates write one entry per line *and* the whole array on one line, so a
    line count answers "how many entries" differently depending on which
    template was asked. The LICENSE's two checksums are two checksums in both
    spells, and the pairing check below has to see one answer.
    """
    match = re.search(rf"^{re.escape(key)}=\((.*?)\)\s*$", template, re.S | re.M)
    if match is None:
        return None
    return _words(match.group(1))


def _words(body: str) -> list[str]:
    """The shell words in an array body: whitespace or comma separated, a pair
    of quotes taken off, everything else kept verbatim."""
    words: list[str] = []
    word = ""
    quote = ""
    for char in body:
        if quote:
            if char == quote:
                quote = ""
            else:
                word += char
        elif char in "'\"":
            quote = char
        elif char.isspace() or char == ",":
            if word:
                words.append(word)
                word = ""
        else:
            word += char
    if word:
        words.append(word)
    return words


def source_entries(template: str, arch: str) -> list[tuple[str, str]] | None:
    """The (file name, url) pairs a template's source_<arch>= array carries.

    None means the template has no such array at all. The array is read rather
    than probed for with a substring: `source_aarch64=` appears in a comment as
    readily as in the file, and the two facts inside the array -- what the file
    is called and where it comes from -- are both restatements of the arch
    table.
    """
    entries = array_entries(template, f"source_{arch}")
    if entries is None:
        return None
    pairs = []
    for entry in entries:
        name, _, url = entry.partition("::")
        pairs.append((name, url))
    return pairs


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


def check_sums(pkg: str, template: str) -> None:
    """The package's own `source=` and `sha256sums=` arrays are the same length.

    The arch arrays are paired by the plan below. These two are everything else
    the package downloads -- the LICENSE, the icon -- and nothing read them at
    all: a third entry in `source=` with its checksum never added rendered
    clean, and makepkg refused the file inside the build container, long after
    this repository could have said so.
    """
    sources = array_entries(template, "source")
    sums = array_entries(template, "sha256sums")
    if sources is None and sums is None:
        return
    if sources is None:
        raise BumpError(f"{pkg}: the template has a sha256sums= array and no source= array")
    if sums is None:
        raise BumpError(f"{pkg}: the template has a source= array and no sha256sums= array")
    if len(sources) != len(sums):
        raise BumpError(
            f"{pkg}: source= carries {len(sources)} entries "
            f"but sha256sums= carries {len(sums)}"
        )


@dataclass(frozen=True)
class ArchAsset:
    """One arch's share of a template, worked out once.

    `asset` is the name 上游 publishes in its SHA256SUMS, and it is the join
    this module used to be missing: the renderer answered it from the 包清单's
    prefix while the guard answered it from whatever the template's URL ended
    in, and nothing compared the two. `placeholder` is the marker that receives
    the digest, so the render fills the array by name instead of spelling
    `sha_{suffix}` a second time.
    """

    arch: str
    suffix: str
    placeholder: str
    asset: str


@dataclass(frozen=True)
class SourceAsset:
    """One plain `source=` entry: the URL, and the marker that takes its digest.

    The template decides both, because the template is what makepkg reads: it
    names the URL that will be downloaded, and its `sha256sums=` entry is the
    slot that digest goes into. They used to live apart -- this module held
    `ICON_URL` and `SOURCE_REPO`, decided what to fetch and hash from them,
    while the template carried its own copy of both URLs, and the only guard on
    the plain arrays compared their *lengths*. So an upstream URL that moved
    (fixed in the template, say) got the *old* URL's digest written into the
    PKGBUILD, and the disagreement surfaced as a checksum error inside the
    container -- after the entry check had said `bump=1` and the render had
    already written.

    The pairing is positional because that is how makepkg pairs them: entry i
    of `sha256sums=` is the digest of entry i of `source=`.
    """

    url: str
    placeholder: str


@dataclass(frozen=True)
class Template:
    """A template read once: its text, the markers it asks for, and its assets.

    Every question a caller has about a template is answered here, in one pass,
    before the network is asked anything. The five readers this replaced each
    parsed one facet and threw the rest away, so the render re-derived the asset
    name it had just validated and a sixth facet -- the plain `source=` array --
    was read by nobody.
    """

    text: str
    markers: tuple[str, ...]
    assets: tuple[ArchAsset, ...]
    sources: tuple[SourceAsset, ...]


@dataclass(frozen=True)
class Rendered:
    """One package's template and the values its committed PKGBUILD carries.

    The entry check asks two questions of the same pair -- is this pair sound,
    and where does it sit against 上游 and the 软件源 -- and answered the second
    by matching the file against its template all over again. `values` is None
    for a package that was never rendered: 落后, but not drift.
    """

    template: Template
    values: dict[str, str] | None


def pair_sources(pkg: str, template: str) -> tuple[SourceAsset, ...]:
    """The plain `source=` entries, each paired with its `sha256sums=` marker.

    `check_sums` already holds the two arrays to the same length; this is what
    turns that length into the pairing the render needs, so a digest fetched
    from a URL lands in the marker that belongs to it. The sums entries must be
    `{{...}}` placeholders: a literal digest is a value nobody re-derives, and
    this repository has no such entry to keep working.
    """
    sources = array_entries(template, "source") or []
    sums = array_entries(template, "sha256sums") or []
    paired = []
    for index, entry in enumerate(sources):
        name, separator, url = entry.partition("::")
        if not separator:
            url = name  # a bare URL entry names no local file
        marker = sums[index]
        wanted = PLACEHOLDER_RE.fullmatch(marker)
        if wanted is None:
            raise BumpError(
                f"{pkg}: sha256sums= entry {index + 1} is {marker!r}, "
                "not a {{...}} placeholder the render can fill"
            )
        paired.append(SourceAsset(url=url, placeholder=wanted.group(1)))
    return tuple(paired)


def read_template(
    *, pkg: str, prefix: str, text: str, arches: Mapping[str, str]
) -> Template:
    """Read a template once and hold it to the 包清单 and the arch 表.

    Every package targets the same repo arches; a package that does not is a
    change to the arch table, not a template exception. Both directions bite,
    because the arch a package claims and the checksum and source it carries
    have to be about the same set: an arch added to the table with no template
    behind it, and a template arch the table never heard of.

    The mapping is restated in the template five ways, so all five are checked:
    arch=(), the source_<arch>= array (its key, the file name it gives the
    download, and the asset its URL ends in), sha256sums_<arch>= with its
    {{sha_suffix}} placeholder, and the package() case block that turns $CARCH
    into the same suffix.

    The asset name is compared *exactly* to the one the 包清单 publishes, not
    to the suffix the URL happens to end in: `magpieXY-linux-amd64` ends in
    `linux-amd64` like the right asset does, so a tail test passed it, the
    render wrote it down, and the 404 landed in the container -- or, on an arch
    CI never builds, never landed anywhere at all.
    """
    check_pkgname(pkg, text)
    check_sums(pkg, text)

    markers = placeholders(text)
    declared = template_arches(text)
    unknown = sorted(set(declared) - set(arches))
    if unknown:
        raise BumpError(
            f"{pkg}: the template declares {', '.join(unknown)}, "
            "which the arch table knows no upstream suffix for"
        )
    missing = sorted(set(arches) - set(declared))
    if missing:
        raise BumpError(f"{pkg}: the template leaves out {', '.join(missing)}")

    suffixes = template_suffixes(text)
    strangers = sorted(set(suffixes) - set(arches))
    if strangers:
        raise BumpError(
            f"{pkg}: package() has a case arm for {', '.join(strangers)}, "
            "which the arch table never heard of"
        )

    assets = []
    for arch, suffix in arches.items():
        entries = source_entries(text, arch)
        if entries is None:
            raise BumpError(f"{pkg}: the template has no source_{arch}= array")
        # One asset per arch is what the 包清单 models -- a package is a
        # directory and one upstream prefix -- so a second entry is a template
        # this renderer cannot answer for, not a value to pick between.
        if len(entries) != 1:
            raise BumpError(
                f"{pkg}: source_{arch} carries {len(entries)} entries; "
                "one asset per arch"
            )
        # The case block and the download's file name are the fifth and third
        # copies of the mapping, both stripped of the `linux-` that only
        # upstream's asset names carry.
        want = suffix.removeprefix("linux-")
        asset = f"{prefix}-{suffix}"
        for name, url in entries:
            if not name.endswith(want):
                raise BumpError(
                    f"{pkg}: source_{arch} names {name!r}, "
                    f"which does not end in {want!r} ({suffix})"
                )
            downloaded = url.rsplit("/", 1)[-1]
            if downloaded != asset:
                raise BumpError(
                    f"{pkg}: source_{arch} downloads {downloaded}, "
                    f"but the 包清单 files {pkg} as {asset}"
                )
        if array_entries(text, f"sha256sums_{arch}") is None:
            raise BumpError(f"{pkg}: the template has no sha256sums_{arch}= array")
        placeholder = f"sha_{suffix}"
        if placeholder not in markers:
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
        assets.append(
            ArchAsset(arch=arch, suffix=suffix, placeholder=placeholder, asset=asset)
        )

    return Template(
        text=text,
        markers=tuple(markers),
        assets=tuple(assets),
        sources=pair_sources(pkg, text),
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


def pkg_field(text: str, name: str) -> str | None:
    """One field of a rendered PKGBUILD, read the way pkg.sh's `pkg_field` reads it.

    First matching line wins, the value is its first token, and one matching
    pair of quotes comes off -- the shape the renderer writes. One table of
    shapes is fed to both readers (tests/test_pkg_sh.py), because two readers of
    one field that disagree is a split nothing would notice.
    """
    prefix = f"{name}="
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        rest = line[len(prefix):].split()
        return rest[0].strip("'\"") if rest else ""
    return None


def packaged_evr(layout: Layout) -> dict[str, str]:
    """What each package in this tree would publish: `<pkgver>-<pkgrel>`.

    That pair is the version a 包文件's name carries and the one a database
    entry states, so it is also the thing the 软件源 can be compared against.
    Reading `pkgver` alone made a rebuild -- the same 上游 version, a new
    pkgrel -- invisible: the render wrote it, every gate was green, and the
    entry check reported that there was nothing to do.
    """
    found = {}
    for pkg in layout.inventory:
        pkgbuild = layout.packages_dir / pkg / "PKGBUILD"
        if not pkgbuild.exists():
            continue
        text = pkgbuild.read_text()
        pkgver = pkg_field(text, "pkgver")
        pkgrel = pkg_field(text, "pkgrel")
        if pkgver and pkgrel:
            found[pkg] = f"{pkgver}-{pkgrel}"
    return found


def pkgver_of(evr: str | None) -> str:
    """The pkgver half of `<pkgver>-<pkgrel>`; blank when nothing is rendered."""
    return "" if evr is None else evr.split("-", 1)[0]


def served_evr(layout: Layout, *, fetch: Fetch = http_fetch) -> dict[str, str] | None:
    """The version each package is served at, read from the database pacman reads.

    None means "cannot be compared" -- either this layout declares no address
    (a hand run), or the 软件源 could not be read. The caller logs both, and
    neither may read as "nothing is published": a failed read that looked like
    an empty repository would rebuild and republish on a day Pages had a bad
    minute. A 404 is different, and is an empty map -- nothing published yet.
    """
    served = layout.served
    if served is None:
        return None
    try:
        body = fetch(served.database, missing_ok=True)
    except (urllib.error.URLError, OSError) as exc:
        log(f"warning: cannot read {served.database} ({exc}); comparing against 上游 alone")
        return None
    if body is None:
        return {}
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:*") as archive:
            return _desc_versions(archive)
    except tarfile.TarError as exc:
        log(f"warning: {served.database} is not a database ({exc}); comparing against 上游 alone")
        return None


def _desc_versions(archive: tarfile.TarFile) -> dict[str, str]:
    """{pkgname: <pkgver>-<pkgrel>} from the `desc` entries repo-add writes.

    One entry per package, holding `%NAME%` and `%VERSION%` blocks. The
    database lists only the current 版本 of each package, which is exactly the
    question: what does a client get when it installs today.
    """
    found = {}
    for member in archive.getmembers():
        if not member.name.endswith("/desc"):
            continue
        handle = archive.extractfile(member)
        if handle is None:
            continue
        name = version = ""
        for block in handle.read().decode().split("\n\n"):
            lines = block.splitlines()
            if len(lines) < 2:
                continue
            if lines[0] == "%NAME%":
                name = lines[1]
            elif lines[0] == "%VERSION%":
                version = lines[1]
        if name and version:
            found[name] = version
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


def template_text(layout: Layout, pkg: str) -> str:
    """A package's template as written, or the failure that names the package.

    Two modes read it -- the render, and the entry check's drift guard -- and
    both have to say the same thing when a layout points at a tree without one.
    Before this, the same five lines (and the same message) lived in both.
    """
    path = layout.templates / f"{pkg}.PKGBUILD.in"
    try:
        return path.read_text()
    except OSError as exc:
        raise BumpError(f"no template for {pkg}: {path} ({exc.strerror})") from exc


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

    # Read every template once, and hold it to the arch table, its own pkgname
    # and the assets the 包清单 publishes, before the network is asked anything:
    # a template that lags the table, or is filed under another name, is this
    # repository's problem, and it should not be reported as "the release has no
    # such asset" or found after the gh-pages push.
    plans = []
    for pkg, prefix in layout.inventory.items():
        text = template_text(layout, pkg)
        plans.append((pkg, read_template(pkg=pkg, prefix=prefix, text=text, arches=layout.arches)))

    sums = release_sums(version, fetch=fetch)

    # The plain `source=` URLs come from the template, and the render fetches
    # exactly those: the digest written into the PKGBUILD is then a digest of
    # the bytes the package will download, by construction. One cache, because
    # both templates name the same LICENSE and only one of them names the icon.
    digests: dict[str, str] = {}

    def digest_of(pkg: str, asset: SourceAsset) -> str:
        # `${pkgver}` is the only variable the templates put in a URL, and it is
        # this function's own value; anything else is a URL it cannot fetch.
        url = asset.url.replace("${pkgver}", version)
        if re.search(r"\$\{[^}]*\}", url):
            raise BumpError(
                f"{pkg}: source= names {url!r}, which this renderer cannot fetch"
            )
        if url not in digests:
            body = fetch(url, missing_ok=True)
            if body is None:
                raise BumpError(
                    f"{pkg}: {url} is not there; "
                    "upstream publishes nothing at the URL the template names"
                )
            digests[url] = sha256_of(body)
        return digests[url]

    # Render everything before writing anything: a template that disagrees with
    # its values then leaves the tree alone instead of half-updated.
    rendered = []
    for pkg, template in plans:
        values = {
            name: value
            for name, value in (("pkgver", version), ("maintainer", layout.maintainer))
            if name in template.markers
        }
        for asset in template.sources:
            values[asset.placeholder] = digest_of(pkg, asset)
        # The plan already decided which asset feeds which marker, compared
        # against the 包清单; the render only looks the digest up.
        for planned in template.assets:
            if planned.asset not in sums:
                raise BumpError(f"{pkg}: the release has no {planned.asset}")
            values[planned.placeholder] = sums[planned.asset]
        rendered.append((pkg, render_pkgbuild(template.text, values)))

    # Nothing is written until every render parses. The entry check asks the
    # same question of the committed file; asking it here means a template
    # with a broken body leaves the tree alone instead of half-updated.
    for pkg, text in rendered:
        problem = parse_failure(text)
        if problem:
            raise BumpError(f"{pkg}: the render is not a bash script: {problem}")

    for pkg, text in rendered:
        out = layout.packages_dir / pkg / "PKGBUILD"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        log(f"rendered {layout_path(layout, out)} at {version}")

    # The version this module just wrote, handed to the caller. Every later
    # step needs it, and re-deriving it from the PKGBUILD gave the pipeline a
    # second, untested reader of a file this one had already parsed.
    log(f"ver={version}")


def values_of(template: str, rendered: str) -> dict[str, str]:
    """The values `rendered` supplies for this template's markers.

    The inverse of render_pkgbuild: it turns the template into a capture-group
    regex, so the parts that must match exactly are its literal text and the
    parts that may vary are the markers. Two markers with one name must have
    been filled the same way both times.

    It lived in tests/test_templates.py -- 28 lines of production logic with its
    own exception class -- so the mode the pipeline actually runs could not ask
    the question the test job was asking.
    """
    pattern = ""
    names: list[str] = []
    cursor = 0
    for match in PLACEHOLDER_RE.finditer(template):
        pattern += re.escape(template[cursor:match.start()]) + "(.*?)"
        names.append(match.group(1))
        cursor = match.end()
    pattern += re.escape(template[cursor:])

    found = re.fullmatch(pattern, rendered, re.S)
    if found is None:
        raise BumpError(
            "the rendered file is not its template with values filled in; "
            "re-render it (scripts/bump.py --render) or revert the template edit"
        )

    values: dict[str, str] = {}
    for name, value in zip(names, found.groups()):
        if values.setdefault(name, value) != value:
            raise BumpError(f"{{{{{name}}}}} was filled two different ways")
    return values


def parse_failure(text: str) -> str | None:
    """What bash complains about in this PKGBUILD, or None when it parses.

    A PKGBUILD is a bash script, and nothing here used to read one as a script:
    `read_template` checks the five ways a template restates the arch table and
    `values_of` checks that the committed file is that template's render, but
    both read text. The only door that parsed it at all was
    `makepkg --printsrcinfo` in scripts/probe-repo-build.sh's case 6, and
    scripts/check.sh skips the probe on the machine CI runs on (no makepkg
    there) -- so a `package()` body with a stray quote got a green `bump=0`,
    and the failure waited inside the container for the next 上游 move.

    `bash -n` is that door without makepkg: it parses everything, runs nothing,
    and every script in this repository already assumes bash exists. Reading
    from stdin keeps the check off the disk -- `render()` asks before it writes.
    """
    try:
        result = subprocess.run(
            ["bash", "-n"], input=text, text=True, capture_output=True
        )
    except OSError as exc:  # a machine with no bash cannot answer, and says so
        raise BumpError(f"cannot run bash to parse a PKGBUILD: {exc}") from exc
    if result.returncode == 0:
        return None
    lines = (result.stderr or result.stdout).strip().splitlines()
    return lines[0] if lines else f"bash exited {result.returncode}"


def check_templates(layout: Layout) -> dict[str, Rendered]:
    """Is this tree sound? The template's own rules, then the drift guard.

    `--check` answered one question -- is the packaged 版本 behind 上游 -- and
    that is by design (GLOSSARY: 落后). The other edge of 渲染 went unasked in
    the mode the pipeline runs: a template edited without a re-render shipped
    later, invisibly bundled with the next 版本, because the run the edit
    started found `bump=0` and printed "nothing to publish". The test job caught
    it; the entry check did not, and it is the entry check that decides.

    It also asked only half of that: `values_of` proves the committed PKGBUILD
    is still some render of the template, while every rule about the template
    itself -- its `pkgname=`, its `arch=()` against the arch 表, its case arms,
    its source and checksum arrays -- lived in `read_template`, which only the
    render calls, and the render only runs when `bump=1`. So a template edited
    to name another package, or to drop an arch, left the tree reading sound
    until 上游 moved, and the run that finally rendered died at step 2. The two
    doors are one now: the entry check asks what the render asks.

    A package with no PKGBUILD is not drift -- it has never been rendered, and
    check() already reports that as 落后 -- but its template is still a template
    this repository will render, so it is still read. The templates come back
    because the caller needs the plan each one already worked out: which asset
    feeds which placeholder is what the checksum comparison below asks about.

    A third door sits beside those two, and it is the parser's own answer: the
    committed file must be a bash script bash can parse. `read_template` reads
    five facets of the template and `values_of` proves provenance; neither is a
    parser, so the only one in the pipeline was `makepkg --printsrcinfo` -- in a
    probe the runner skips for want of makepkg.
    """
    found: dict[str, Rendered] = {}
    for pkg, prefix in layout.inventory.items():
        text = template_text(layout, pkg)
        template = read_template(pkg=pkg, prefix=prefix, text=text, arches=layout.arches)

        pkgbuild = layout.packages_dir / pkg / "PKGBUILD"
        if not pkgbuild.exists():
            # Never rendered: 落后, which check() reports, but not drift -- and
            # the template above is still one this repository will render.
            found[pkg] = Rendered(template=template, values=None)
            continue
        committed = pkgbuild.read_text()
        try:
            values = values_of(text, committed)
        except BumpError as exc:
            raise BumpError(f"{pkg}: {exc}") from exc
        # The third door on this artifact, and it is the parser's own: the file
        # is its template's render, and it is a bash script bash can parse.
        # `read_template` reads five facets of the template; none of them is
        # whether the text is a script at all.
        problem = parse_failure(committed)
        if problem:
            raise BumpError(
                f"{pkg}: bash cannot parse {layout_path(layout, pkgbuild)}: {problem}"
            )
        found[pkg] = Rendered(template=template, values=values)
    return found


# The four directions 落后 can come from, as the words a run prints. GLOSSARY
# defines the union; these are its members.
UPSTREAM_MOVED = "upstream"
NOT_PUBLISHED = "unpublished"
UPSTREAM_REUPLOADED = "checksums"
RETIRED = "retired"


def behind(
    layout: Layout,
    *,
    upstream: str,
    packaged: Mapping[str, str],
    served: Mapping[str, str] | None,
    stale: set[str],
) -> dict[str, str]:
    """Every package that is 落后, and which direction says so.

    GLOSSARY defines 落后 as a union of directions. This used to be a five-line
    comprehension inside check() that answered "is anything behind" and threw
    the reason away, so a run left its reader to compare the `packaged` and
    `served` sections by hand to learn why it was publishing.

    The fourth direction had no home at all. A package that left the 包清单 is
    not in the inventory loop, so its files and its database entry kept being
    served -- and build-repo.sh's retire, which deletes them, only runs behind
    `bump=1`. Withdrawing a package therefore waited for an unrelated 上游 move,
    and forever if 上游 went quiet.

    `served` is None when the address could not be read. The second and fourth
    directions cannot be answered then, and served_evr has already logged why.
    """
    reasons: dict[str, str] = {}
    for pkg in layout.inventory:
        evr = packaged.get(pkg)
        if pkgver_of(evr) != upstream:
            reasons[pkg] = UPSTREAM_MOVED
        elif served is not None and served.get(pkg) != evr:
            reasons[pkg] = NOT_PUBLISHED
        elif pkg in stale:
            reasons[pkg] = UPSTREAM_REUPLOADED
    for pkg in sorted(served or {}):
        if pkg not in layout.inventory:
            reasons[pkg] = RETIRED
    return reasons


def check(*, layout: Layout, fetch: Fetch = http_fetch) -> int:
    # This repository's own problem first, before anything is fetched: the
    # render does the same, so a tree whose templates and PKGBUILDs disagree
    # fails here rather than being reported as "upstream has no such asset".
    rendered = check_templates(layout)
    upstream = latest_version(fetch=fetch)
    packaged = packaged_evr(layout)
    log(f"upstream={upstream}")
    for pkg, evr in sorted(packaged.items()):
        log(f"packaged {pkg}={evr}")
    if not packaged:
        log("warning: no rendered PKGBUILD yet")
    served = served_evr(layout, fetch=fetch)
    if served is not None:
        for pkg, evr in sorted(served.items()):
            log(f"served {pkg}={evr}")
    # The release's own checksums, which is the third way this tree can be
    # 落后: 上游 re-uploaded an asset under a tag that did not move. Nothing
    # else would ever notice -- the version matches, the render matches, and
    # `makepkg` would refuse the download the day something re-rendered.
    sums = uploaded_sums(upstream, fetch=fetch) or {}
    stale = set()
    for pkg, plan in rendered.items():
        # An unrendered package has no values to compare, and pkgver_of answers
        # "" for a build that is not there -- one guard, two real shapes (no
        # PKGBUILD at all; a PKGBUILD that yields no pkgver). Both mean the
        # render will take the new sums, so neither is a stale checksum.
        if plan.values is None or pkgver_of(packaged.get(pkg)) != upstream:
            continue
        for planned in plan.template.assets:
            upstream_sum = sums.get(planned.asset)
            if upstream_sum is not None and plan.values.get(planned.placeholder) != upstream_sum:
                stale.add(pkg)
    if stale:
        log("checksums differ from 上游's: " + " ".join(sorted(stale)))
    # The four directions, one answer. Walking the inventory rather than what is
    # on disk keeps a package whose PKGBUILD was never rendered 落后; walking
    # what the 软件源 serves as well is what lets a withdrawn package leave it.
    reasons = behind(
        layout, upstream=upstream, packaged=packaged, served=served, stale=stale
    )
    for pkg, reason in sorted(reasons.items()):
        log(f"behind {pkg}={reason}")
    log(f"bump={'1' if reasons else '0'}")
    return 0


def served_from_env() -> Served | None:
    """The 软件源's address, when this run declares one.

    The workflow declares all three for every publish job; a hand run that
    leaves them out gets the 上游-only comparison instead of a wrong URL.
    """
    url = os.environ.get("PAGES_URL")
    section = os.environ.get("REPO_NAME")
    arch = os.environ.get("ARCH")
    if not (url and section and arch):
        return None
    return Served(url=url, section=section, arch=arch)


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
            layout = layout_for(
                maintainer=os.environ.get("MAINTAINER", DEFAULT_MAINTAINER),
                served=served_from_env(),
            )
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
