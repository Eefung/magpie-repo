"""Unit tests for scripts/bump.py.

No network, no makepkg: the network enters through the injected `fetch`, and
rendering targets a temporary directory. Run with

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Mapping, Sequence
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import bump  # noqa: E402  (the module lives beside the scripts, not on the path)
import frozen  # noqa: E402  (the production reader of the frozen lines)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# The digest the fake release publishes for the arch asset, and the one
# `staged_root` renders into the PKGBUILD. One value on purpose: `--check`
# compares the two, so a fixture that publishes one number and renders another
# would read as "上游 re-uploaded this asset" in every test that expects a
# quiet tree.
ASSET_SUM = "2" * 64


class FetchSpy:
    """A fake `fetch`: answers from a table and records what was asked for.

    Explicit None means "404" -- `missing_ok` decides whether that comes back
    as None or raises. An Exception instance in the table is raised. A URL the
    table does not mention is a test bug, not a network call.
    """

    def __init__(self, table: Mapping[str, bytes | None | BaseException]):
        self.table = dict(table)
        self.urls: list[str] = []

    def __call__(self, url: str, *, missing_ok: bool = False) -> bytes | None:
        self.urls.append(url)
        if url not in self.table:
            raise AssertionError(f"unexpected fetch: {url}")
        value = self.table[url]
        if isinstance(value, BaseException):
            if isinstance(value, urllib.error.HTTPError):
                value.close()  # Python hands HTTPError a BytesIO; don't leak it
            raise value
        if value is None:
            if missing_ok:
                return None
            error = urllib.error.HTTPError(url, 404, "Not Found", Message(), None)
            error.close()
            raise error
        return value


def sums_url(version: str) -> str:
    return f"{bump.RELEASES}/download/v{version}/SHA256SUMS"


def license_url(version: str) -> str:
    """The LICENSE URL the templates name -- the fixture's copy of it.

    Production has no constant for this any more: the template's `source=` entry
    is what the render fetches. A fixture still has to spell what it pretends to
    serve, so this line moves when a template's URL moves -- which is the
    coupling the render now makes visible instead of hiding behind a constant
    both sides happened to agree with.
    """
    return f"https://raw.githubusercontent.com/yetone/magpie/v{version}/LICENSE"


# The icon URL the desktop template names, for the same reason.
ICON_URL = "https://usemagpie.ai/img/icon-256.png"


def planned_assets() -> list[str]:
    """Every asset this repository's templates plan, read from them.

    The 版本 decision asks a release's table for exactly these, so a fixture
    table that published only some of them would be describing a release this
    run cannot 打包 -- and every test using such a table would then be asserting
    against a state the pipeline treats as "skip this one".
    """
    return bump.required_assets(
        template for _, template in bump.plan(bump.layout_for(REPO))
    )


def version_sources(
    github: str | Sequence[str] | None, site: str | None
) -> dict[str, bytes | None]:
    """A fetch table for the two version sources; None means "that one 404s".

    `github` is the list of tags GitHub's release endpoint carries, newest
    first. A version this table mentions is a 版本 whose release is fully
    uploaded: the version 上游 advertises and the assets it serves do not arrive
    together, so a table naming a version without its SHA256SUMS would be
    describing a release no run can package.
    """
    tags = [] if github is None else ([github] if isinstance(github, str) else list(github))
    table: dict[str, bytes | None] = {}
    for version in [*tags, site]:
        if version is not None:
            table.setdefault(
                sums_url(version),
                sums_body({asset: ASSET_SUM for asset in planned_assets()}),
            )
    if github is not None:
        table[bump.GITHUB_RELEASES] = json.dumps(
            [{"tag_name": f"v{tag}", "draft": False} for tag in tags]
        ).encode()
    if site is not None:
        table[bump.SITE_API] = json.dumps({"version": site}).encode()
    return table


def uploaded(*versions: str) -> dict[str, bytes]:
    """The SHA256SUMS of these 版本, and of nothing else."""
    assets = {asset: ASSET_SUM for asset in planned_assets()}
    return {sums_url(version): sums_body(assets) for version in versions}


def sums_body(entries: dict[str, str]) -> bytes:
    return "".join(f"{digest}  {name}\n" for name, digest in entries.items()).encode()


def staged_root(root: Path, versions: dict[str, str], *, templates: Path | None = None) -> Path:
    """A fake checkout whose packages/<pkg>/PKGBUILD are their templates' renders.

    Rendered, not hand-written: `--check` now holds every committed PKGBUILD to
    its template, so a three-line stub would be reported as drift.
    """
    templates = REPO / "templates" if templates is None else templates
    for pkg, version in versions.items():
        template = (templates / f"{pkg}.PKGBUILD.in").read_text()
        supplied = {
            "pkgver": version,
            "maintainer": "Test Maintainer <test@example.invalid>",
            "sha_license": "0" * 64,
            "sha_icon": "1" * 64,
            "sha_linux-amd64": ASSET_SUM,
        }
        wanted = bump.placeholders(template)
        path = root / "packages" / pkg / "PKGBUILD"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            bump.render_pkgbuild(
                template, {n: v for n, v in supplied.items() if n in wanted}
            )
        )
    return root


def staged_checkout(root: Path, versions: dict[str, str]) -> Path:
    """A tree the production constructor can read: templates, then packages.

    The templates are the repository's, copied in: a staged tree is a checkout,
    and the check reads `root/templates` for its plans before it reads anything
    else.
    """
    templates = root / "templates"
    templates.mkdir(parents=True, exist_ok=True)
    for pkg in bump.PACKAGES:
        (templates / f"{pkg}.PKGBUILD.in").write_text(
            (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()
        )
    return staged_root(root, versions, templates=templates)


def layout_at(
    root: Path,
    *,
    templates: Path | None = None,
    inventory: dict[str, str] | None = None,
    arches: dict[str, str] | None = None,
) -> bump.Layout:
    """A layout pointing the renderer at a temporary tree.

    The layout is rooted at that tree -- the root is where the check looks for
    `PINNED` beside the PKGBUILDs -- while the templates and the inventory are
    the repository's own unless a test overrides them, and unless the tree
    staged its own `templates/`.
    That last clause is the one that matters: `staged_root` renders a PKGBUILD
    from the templates it was handed, and this function builds the layout the
    code under test reads, so a caller that names a tree to one and not the
    other points two fixtures at two trees. The failure does not look like a
    fixture bug -- `values_of` reports the committed file as "not its
    template's render", which is the drift message.

    This goes through the production constructor, so the defaults under test
    are the ones that ship. Two things are not: the 软件源 address is a fact
    about the run, declared in the environment, and the pin is one line in this
    tree -- so the layout is rooted at the tree, not at the repository it was
    copied from. Rooting it at the repository was what the pin's old sentinel
    papered over: the file the check reads is `root/PINNED`, and this is `root`.
    """
    if templates is None:
        staged = root / "templates"
        templates = staged if staged.is_dir() else REPO / "templates"
    return bump.layout_for(
        root,
        templates=templates,
        packages_dir=root / "packages",
        inventory=inventory,
        arches=arches,
        maintainer="Test Maintainer <test@example.invalid>",
    )


def served_at(url: str = "https://example.invalid/repo") -> bump.Served:
    """The address a test's fake 软件源 is served at."""
    return bump.Served(url=url, section="magpie", arch="x86_64")


# What a run declares: the three names of its 软件源, and the version a human
# asked for. None is a fact about the tree.
DECLARED_NAMES = ("PAGES_URL", "REPO_NAME", "ARCH", "WANTED")


def address_env() -> dict[str, str]:
    """The three names a run declares for the test's fake 软件源."""
    served = served_at()
    return {"PAGES_URL": served.url, "REPO_NAME": served.section, "ARCH": served.arch}


def nothing_declared():
    """The run a fixture describes: nothing said, so 上游 alone.

    The check reads its run's facts out of the environment now, which is
    production's shape -- and means a shell that exports them would decide a
    test that never said anything. Only those are cleared: the rest of the
    environment is not this fixture's business.
    """
    kept = {name: value for name, value in os.environ.items() if name not in DECLARED_NAMES}
    return mock.patch.dict(os.environ, kept, clear=True)


def database_body(entries: dict[str, str]) -> bytes:
    """A repo-add database: one `<pkgname>-<evr>/desc` member per package."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, evr in entries.items():
            payload = (
                f"%FILENAME%\n{name}-{evr}-x86_64.pkg.tar.zst\n\n"
                f"%NAME%\n{name}\n\n%VERSION%\n{evr}\n"
            ).encode()
            info = tarfile.TarInfo(f"{name}-{evr}/desc")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def release_url(version: str, repo: str = "owner/repo") -> str:
    """The one Release this run may need to know about."""
    return f"https://api.github.com/repos/{repo}/releases/tags/v{version}"


def release_table(version: str) -> dict[str, bytes]:
    """A fetch table carrying one release: its sums, its LICENSE, its icon.

    The assets are the plans' own answer, so a fixture cannot describe a
    release this run would skip as incomplete -- and an arch the 表 dropped
    cannot live on in a table nobody notices.
    """
    return {
        sums_url(version): sums_body({asset: ASSET_SUM for asset in planned_assets()}),
        license_url(version): b"license",
        ICON_URL: b"icon",
    }


def run_main(
    *argv: str,
    fetch,
    layout: bump.Layout | None = None,
    root: Path | None = None,
    env: dict | None = None,
) -> tuple[object, str]:
    """Drive the entry point the workflow drives; return its code and output.

    One helper for both doors: a test that hands a whole layout in, and one
    that points the production constructor at a staged tree. The exit code is
    returned even when it is the error string `main()` exits with -- a
    swallowed code is what this seam exists to catch.
    """
    buffer = io.StringIO()
    code: object = 0
    extra = {} if root is None else {"root": root}
    declared = nothing_declared() if env is None else mock.patch.dict(os.environ, env, clear=True)
    with declared:
        with contextlib.redirect_stdout(buffer):
            try:
                code = bump.main(list(argv), fetch=fetch, layout=layout, **extra)
            except SystemExit as exc:
                code = exc.code
    return code, buffer.getvalue()


class LayoutForTest(unittest.TestCase):
    """The production constructor's defaults, exercised rather than assumed."""

    def test_both_trees_come_from_the_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout = bump.layout_for(Path(tmp))
            self.assertEqual(layout.templates, Path(tmp) / "templates")
            self.assertEqual(layout.packages_dir, Path(tmp) / "packages")
        self.assertEqual(layout.inventory, bump.PACKAGES)
        self.assertEqual(layout.arches, bump.ARCHES)
        self.assertEqual(layout.maintainer, bump.DEFAULT_MAINTAINER)

    def test_an_override_moves_one_tree_only(self):
        # What the tests need: the repository's templates, the output elsewhere.
        with tempfile.TemporaryDirectory() as tmp:
            layout = bump.layout_for(Path(tmp), packages_dir=Path(tmp) / "out")
            self.assertEqual(layout.templates, Path(tmp) / "templates")
            self.assertEqual(layout.packages_dir, Path(tmp) / "out")

    def test_the_tree_takes_no_address(self):
        # Where this run publishes is not a fact about the tree: the constructor
        # takes no address, so no exit can inherit a failure it never asked
        # about. The value itself needs no test -- a field nothing can set is a
        # field nothing may read.
        with self.assertRaises(TypeError):
            bump.layout_for(REPO, served=served_at())

    def test_the_tree_takes_no_pin(self):
        # The pin is one line in the tree, read at the question that needs it;
        # hand it in and "this tree has no pin" becomes a second way to say a
        # thing the file already says.
        with self.assertRaises(TypeError):
            bump.layout_for(REPO, pinned="0.1.9")

    def test_the_tree_constructs_over_a_broken_pin(self):
        # Construction reads nothing, so a pin nobody can parse cannot stop a
        # tree from being named. It is still refused -- by the check, by name.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / bump.PIN_FILE).write_text("not-a-version\n")
            with mock.patch.dict(os.environ, {}, clear=True):
                layout = bump.layout_for(root)
        self.assertEqual(layout.root, root)


class LayoutAtTest(unittest.TestCase):
    """The fixture's layout and the fixture's tree have to be the same tree.

    `staged_root` renders a PKGBUILD from the templates it is handed; `layout_at`
    builds the layout the code under test reads. Two calls, two chances to name
    different trees -- and the failure does not read as a fixture bug: `values_of`
    says the committed file "is not its template's render", which is the drift
    message. It cost two detours in one session, both times because the tree
    carried its own `templates/` and the layout was built without them.
    """

    def staged_with_marker(self, root: Path) -> Path:
        """A staged tree whose own templates differ from the repository's."""
        templates = root / "templates"
        shutil.copytree(REPO / "templates", templates)
        desktop = templates / "magpie-bin.PKGBUILD.in"
        desktop.write_text(desktop.read_text() + "\n# the staged tree\n")
        staged_root(
            root,
            {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"},
            templates=templates,
        )
        return templates

    def test_a_tree_that_carries_its_own_templates_is_read_against_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            templates = self.staged_with_marker(root)

            layout = layout_at(root)

            self.assertEqual(layout.templates, templates)
            # And the question the repository asks of a tree answers clean:
            # read against the repository's templates this tree is drift.
            bump.check_templates(layout)

    def test_a_tree_without_one_still_reads_the_repository_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged_root(root, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
            self.assertEqual(layout_at(root).templates, REPO / "templates")


class HighestTest(unittest.TestCase):
    def test_compares_parts_as_numbers_not_text(self):
        self.assertEqual(bump.highest(["0.1.999", "0.1.1000"]), "0.1.1000")

    def test_drops_blank_and_prerelease_entries(self):
        self.assertEqual(bump.highest(["", "0.1.4-beta", "0.1.3"]), "0.1.3")

    def test_names_the_junk_when_nothing_is_usable(self):
        with self.assertRaises(bump.BumpError) as caught:
            bump.highest(["0.1.4-beta", "latest"])
        self.assertIn("0.1.4-beta", str(caught.exception))


class LatestReleaseTest(unittest.TestCase):
    """Which 版本 this run can 打包, and the table it decided on.

    Two widths of "not yet": the tag before its assets (a 404) and the table
    before the uploads are done (a subset). Only the first was handled, so the
    second passed the decision and died in the render -- while an older complete
    release was in hand. `assets` is the plans' answer, so the walk asks the
    question the render will ask.
    """

    def latest(self, fetch, assets=None) -> str:
        """The 版本 this table yields, with the repository's own planned assets."""
        chosen = planned_assets() if assets is None else assets
        return bump.latest_release(assets=chosen, fetch=fetch).version

    def test_site_ahead_of_github_wins(self):
        fetch = FetchSpy(version_sources(github="0.1.9", site="0.1.10"))
        self.assertEqual(self.latest(fetch), "0.1.10")

    def test_github_ahead_of_site_wins(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.9"))
        self.assertEqual(self.latest(fetch), "0.1.10")

    def test_github_agrees_with_site(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        self.assertEqual(self.latest(fetch), "0.1.10")

    def test_github_rate_limit_falls_back_to_the_site(self):
        fetch = FetchSpy(
            {
                bump.GITHUB_RELEASES: urllib.error.HTTPError(bump.GITHUB_RELEASES, 403, "rate limited", Message(), None),
                bump.SITE_API: json.dumps({"version": "0.1.9"}).encode(),
                **uploaded("0.1.9"),
            }
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.latest(fetch), "0.1.9")

    def test_connection_error_falls_back_to_the_site(self):
        fetch = FetchSpy(
            {
                bump.GITHUB_RELEASES: urllib.error.URLError("no route to host"),
                bump.SITE_API: json.dumps({"version": "0.1.9"}).encode(),
                **uploaded("0.1.9"),
            }
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.latest(fetch), "0.1.9")

    def test_the_table_it_decided_on_comes_back_with_the_version(self):
        # The entry check compares the committed render against this table. It
        # used to fetch the same URL a second time, so the 版本 and the
        # comparison came from two readings.
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        release = bump.latest_release(assets=planned_assets(), fetch=fetch)
        self.assertEqual(release.version, "0.1.10")
        self.assertEqual(fetch.urls.count(sums_url("0.1.10")), 1)
        self.assertEqual(release.sums["magpie-linux-amd64"], ASSET_SUM)

    def test_a_tag_without_its_assets_falls_back_to_the_uploaded_release(self):
        # 上游的 tag 与资产不一起到：先有 release 页面，上传在后面几秒到几分钟。
        # 取两个源里较大的那个、再指望资产齐备，就是那次「手里有完整的 0.1.10，
        # 却因为第二个读数撞上只打了 tag 的 0.1.11 而什么都不发」。
        fetch = FetchSpy(
            {
                **version_sources(github=["0.1.11", "0.1.10"], site="0.1.11"),
                sums_url("0.1.11"): None,
            }
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.latest(fetch), "0.1.10")

    def test_a_table_carrying_a_subset_is_not_a_candidate(self):
        # The cheap door: a SHA256SUMS that landed before every upload did, or
        # one upstream regenerated after renaming an asset. Presence answered
        # 200, so the run decided on a 版本 the render then refused -- with the
        # complete 0.1.10 in hand the whole time.
        assets = planned_assets()
        self.assertGreater(len(assets), 1, "one asset cannot show a subset; reword this test")
        partial = {asset: ASSET_SUM for asset in assets[1:]}
        fetch = FetchSpy(
            {
                **version_sources(github=["0.1.11", "0.1.10"], site="0.1.11"),
                sums_url("0.1.11"): sums_body(partial),
            }
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.latest(fetch), "0.1.10")

    def test_no_complete_candidate_names_what_each_one_lacks(self):
        # A release to report, not a reason to wait: every candidate uploaded a
        # table and every table lists a different subset.
        assets = planned_assets()
        self.assertGreater(len(assets), 1, "one asset cannot show a subset; reword this test")
        fetch = FetchSpy(
            {
                **version_sources(github=["0.1.11", "0.1.10"], site="0.1.11"),
                sums_url("0.1.11"): sums_body({asset: ASSET_SUM for asset in assets[1:]}),
                sums_url("0.1.10"): sums_body({assets[0]: ASSET_SUM}),
            }
        )
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(bump.BumpError) as caught:
                self.latest(fetch)
        self.assertIn("every asset this repository packages", str(caught.exception))
        self.assertIn(f"v0.1.11 lacks {assets[0]}", str(caught.exception))
        self.assertIn(f"v0.1.10 lacks {assets[1]}", str(caught.exception))

    def test_nothing_uploaded_is_one_line(self):
        fetch = FetchSpy(
            {
                **version_sources(github=["0.1.11"], site="0.1.11"),
                sums_url("0.1.11"): None,
            }
        )
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(bump.BumpError) as caught:
                self.latest(fetch)
        self.assertIn("none has its SHA256SUMS", str(caught.exception))

    def test_no_answer_from_anywhere_is_one_line(self):
        fetch = FetchSpy({bump.GITHUB_RELEASES: None, bump.SITE_API: None})
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(bump.BumpError) as caught:
                self.latest(fetch)
        self.assertIn("could not learn upstream's version", str(caught.exception))


class ReleaseSumsTest(unittest.TestCase):
    def test_maps_asset_names_to_checksums(self):
        fetch = FetchSpy(
            {sums_url("0.1.2"): sums_body({"magpie-linux-amd64": "a" * 64, "magpie-cli-linux-arm64": "b" * 64})}
        )
        self.assertEqual(
            bump.release_sums("0.1.2", fetch=fetch),
            {"magpie-linux-amd64": "a" * 64, "magpie-cli-linux-arm64": "b" * 64},
        )

    def test_strips_the_binary_marker_from_names(self):
        fetch = FetchSpy({sums_url("0.1.2"): f"{'c' * 64} *magpie-linux-amd64\n".encode()})
        self.assertEqual(bump.release_sums("0.1.2", fetch=fetch), {"magpie-linux-amd64": "c" * 64})

    def test_a_missing_asset_is_one_line_not_a_traceback(self):
        fetch = FetchSpy({sums_url("0.1.2"): None})
        with self.assertRaises(bump.BumpError) as caught:
            bump.release_sums("0.1.2", fetch=fetch)
        self.assertIn("SHA256SUMS", str(caught.exception))

    def test_an_empty_list_is_refused(self):
        fetch = FetchSpy({sums_url("0.1.2"): b"\n\n"})
        with self.assertRaises(bump.BumpError):
            bump.release_sums("0.1.2", fetch=fetch)

    def test_a_403_is_not_dressed_up_as_a_missing_asset(self):
        fetch = FetchSpy({sums_url("0.1.2"): urllib.error.HTTPError(sums_url("0.1.2"), 403, "blocked", Message(), None)})
        with self.assertRaises(urllib.error.HTTPError):
            bump.release_sums("0.1.2", fetch=fetch)


class RenderPkgbuildTest(unittest.TestCase):
    def test_fills_every_marker(self):
        out = bump.render_pkgbuild("a={{x}} b={{y}}", {"x": "1", "y": "2"})
        self.assertEqual(out, "a=1 b=2")

    def test_accepts_padding_inside_the_braces(self):
        self.assertEqual(bump.render_pkgbuild("pkgver={{ pkgver }}", {"pkgver": "0.1.2"}), "pkgver=0.1.2")

    def test_names_the_placeholder_nothing_supplies(self):
        with self.assertRaises(bump.BumpError) as caught:
            bump.render_pkgbuild("a={{pkgver}} b={{nope}}", {"pkgver": "1"})
        self.assertIn("{{nope}}", str(caught.exception))

    def test_refuses_a_value_the_template_never_asks_for(self):
        with self.assertRaises(bump.BumpError) as caught:
            bump.render_pkgbuild("plain text", {"sha_icon": "0" * 64})
        self.assertIn("sha_icon", str(caught.exception))

    def test_refuses_a_marker_left_behind(self):
        with self.assertRaises(bump.BumpError) as caught:
            bump.render_pkgbuild("pkgver={{pkgver}", {"pkgver": "1"})
        self.assertIn("unrendered marker", str(caught.exception))


class RenderTest(unittest.TestCase):
    VERSION = "0.1.2"
    ASSETS = {
        "magpie-linux-amd64": "1" * 64,
        "magpie-linux-arm64": "2" * 64,
        "magpie-cli-linux-amd64": "3" * 64,
        "magpie-cli-linux-arm64": "4" * 64,
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out_root = Path(self._tmp.name)
        self.layout = layout_at(self.out_root)

    def table(self, *, license_bytes: bytes | None = b"license", icon_bytes: bytes | None = b"icon", assets: dict[str, str] | None = None):
        return {
            sums_url(self.VERSION): sums_body(self.ASSETS if assets is None else assets),
            license_url(self.VERSION): license_bytes,
            ICON_URL: icon_bytes,
        }

    def pkgbuild(self, pkg: str) -> str:
        return (self.out_root / "packages" / pkg / "PKGBUILD").read_text()

    def test_writes_both_pkgbuilds_with_the_new_version(self):
        fetch = FetchSpy(self.table())
        bump.render(self.VERSION, layout=self.layout, fetch=fetch)

        for pkg in bump.PACKAGES:
            text = self.pkgbuild(pkg)
            self.assertIn(f"pkgver={self.VERSION}", text)
            self.assertNotIn("{{", text)

    def test_puts_the_hashed_bytes_where_the_template_expects_them(self):
        fetch = FetchSpy(self.table(license_bytes=b"the license", icon_bytes=b"the icon"))
        bump.render(self.VERSION, layout=self.layout, fetch=fetch)

        self.assertIn(sha(b"the license"), self.pkgbuild("magpie-bin"))
        self.assertIn(sha(b"the icon"), self.pkgbuild("magpie-bin"))
        self.assertIn(self.ASSETS["magpie-linux-amd64"], self.pkgbuild("magpie-bin"))
        self.assertIn(self.ASSETS["magpie-cli-linux-amd64"], self.pkgbuild("magpie-cli-bin"))

    def test_a_package_that_ships_no_icon_never_asks_for_one(self):
        # magpie-cli-bin's template has no {{sha_icon}}; the icon URL is absent
        # from the table, so asking for it would fail this test.
        fetch = FetchSpy(
            {
                sums_url(self.VERSION): sums_body(self.ASSETS),
                license_url(self.VERSION): b"license",
            }
        )
        cli_only = layout_at(self.out_root, inventory={"magpie-cli-bin": "magpie-cli"})
        bump.render(self.VERSION, layout=cli_only, fetch=fetch)

        self.assertNotIn(ICON_URL, fetch.urls)
        self.assertNotIn("sha_icon", self.pkgbuild("magpie-cli-bin"))

    def test_templates_come_from_the_layout_not_from_the_repository(self):
        # The bug this replaced: templates were read from the module's own ROOT
        # while the output directory was a parameter, so a caller pointing the
        # renderer at a tree still got the repository's templates filled in.
        decoy = self.out_root / "templates"
        decoy.mkdir()
        for pkg in bump.PACKAGES:
            text = (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()
            (decoy / f"{pkg}.PKGBUILD.in").write_text(text + "\n# rendered from the decoy tree\n")

        fetch = FetchSpy(self.table())
        bump.render(self.VERSION, layout=layout_at(self.out_root, templates=decoy), fetch=fetch)

        for pkg in bump.PACKAGES:
            self.assertIn("rendered from the decoy tree", self.pkgbuild(pkg))

    def test_a_template_whose_pkgname_drifted_stops_before_the_network(self):
        # The 包清单 publishes the inventory key as the pkgname, and the release
        # step asks for exactly that name. A template naming another package
        # rendered clean until this guard: the build would produce files under
        # the template's name and the release would find none, after gh-pages
        # had been pushed.
        decoy = self.out_root / "templates"
        decoy.mkdir()
        for pkg in bump.PACKAGES:
            text = (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()
            (decoy / f"{pkg}.PKGBUILD.in").write_text(text)
        drifted = decoy / "magpie-cli-bin.PKGBUILD.in"
        drifted.write_text(
            drifted.read_text().replace("pkgname=magpie-cli-bin", "pkgname=magpie-cli-bn")
        )

        fetch = FetchSpy(self.table())
        with self.assertRaises(bump.BumpError) as caught:
            bump.render(self.VERSION, layout=layout_at(self.out_root, templates=decoy), fetch=fetch)

        self.assertIn("magpie-cli-bn", str(caught.exception))
        self.assertIn("magpie-cli-bin", str(caught.exception))
        self.assertEqual(fetch.urls, [])
        self.assertFalse((self.out_root / "packages").exists())

    def test_a_layout_missing_a_template_names_the_package(self):
        empty = self.out_root / "templates"
        empty.mkdir()
        fetch = FetchSpy(self.table())
        with self.assertRaises(bump.BumpError) as caught:
            bump.render(self.VERSION, layout=layout_at(self.out_root, templates=empty), fetch=fetch)
        self.assertIn("no template for magpie-bin", str(caught.exception))
        self.assertFalse((self.out_root / "packages").exists())

    def decoy(self) -> Path:
        """A copy of this repository's templates, for the tests that edit one."""
        templates = self.out_root / "templates"
        templates.mkdir(exist_ok=True)
        for pkg in bump.PACKAGES:
            (templates / f"{pkg}.PKGBUILD.in").write_text(
                (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()
            )
        return templates

    def test_the_url_fetched_is_the_one_the_template_names(self):
        # The whole point of the pairing: move the URL in the template and the
        # render fetches the moved one. While ICON_URL decided, the digest was
        # of the old URL's bytes and the PKGBUILD named the new one -- a
        # checksum error inside the container, after the write.
        moved = "https://usemagpie.ai/img/icon-512.png"
        templates = self.decoy()
        icon = templates / "magpie-bin.PKGBUILD.in"
        icon.write_text(icon.read_text().replace(ICON_URL, moved))
        table = self.table()
        table.pop(ICON_URL)
        table[moved] = b"the moved icon"

        fetch = FetchSpy(table)
        bump.render(self.VERSION, layout=layout_at(self.out_root, templates=templates), fetch=fetch)

        self.assertIn(moved, fetch.urls)
        self.assertNotIn(ICON_URL, fetch.urls)
        self.assertIn(sha(b"the moved icon"), self.pkgbuild("magpie-bin"))

    def test_a_sums_entry_the_render_cannot_fill_is_refused(self):
        # A literal digest in the plain array is a value nobody re-derives. The
        # render fills these slots by position, so it needs to know which marker
        # belongs to which source.
        templates = self.decoy()
        icon = templates / "magpie-bin.PKGBUILD.in"
        icon.write_text(icon.read_text().replace("{{sha_icon}}", "0" * 64))
        fetch = FetchSpy(self.table())

        with self.assertRaises(bump.BumpError) as caught:
            bump.render(self.VERSION, layout=layout_at(self.out_root, templates=templates), fetch=fetch)

        self.assertIn("placeholder", str(caught.exception))
        self.assertEqual(fetch.urls, [])
        self.assertFalse((self.out_root / "packages").exists())

    def test_a_missing_license_stops_before_writing_anything(self):
        fetch = FetchSpy(self.table(license_bytes=None))
        with self.assertRaises(bump.BumpError) as caught:
            bump.render(self.VERSION, layout=self.layout, fetch=fetch)
        # The URL is the template's, so the failure names it rather than a
        # constant, and it says which package could not be rendered.
        self.assertIn("magpie-bin", str(caught.exception))
        self.assertIn(license_url(self.VERSION), str(caught.exception))
        self.assertFalse((self.out_root / "packages").exists())

    def test_a_missing_arch_asset_names_the_package(self):
        assets = {k: v for k, v in self.ASSETS.items() if k != "magpie-linux-amd64"}
        fetch = FetchSpy(self.table(assets=assets))
        with self.assertRaises(bump.BumpError) as caught:
            bump.render(self.VERSION, layout=self.layout, fetch=fetch)
        self.assertIn("magpie-bin: the release has no magpie-linux-amd64", str(caught.exception))
        self.assertFalse((self.out_root / "packages").exists())

    def test_refuses_a_version_that_is_not_dotted_digits(self):
        fetch = FetchSpy(self.table())
        with self.assertRaises(bump.BumpError) as caught:
            bump.render("0.1.2-rc1", layout=self.layout, fetch=fetch)
        self.assertIn("expected digits separated by dots", str(caught.exception))
        self.assertEqual(fetch.urls, [])

    def test_hands_the_version_to_the_caller(self):
        # publish.yml scrapes this one line instead of re-parsing the file
        # render just wrote; the wording is the contract.
        fetch = FetchSpy(self.table())
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            bump.render(self.VERSION, layout=self.layout, fetch=fetch)
        self.assertIn(f"ver={self.VERSION}", buffer.getvalue().splitlines())


class PackagedEvrTest(unittest.TestCase):
    def test_reads_the_pair_a_package_file_is_named_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.2", "magpie-cli-bin": "0.1.1"})
            self.assertEqual(
                bump.packaged_evr(layout_at(root)),
                {"magpie-bin": "0.1.2-1", "magpie-cli-bin": "0.1.1-1"},
            )

    def test_skips_packages_that_were_never_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.2"})
            self.assertEqual(bump.packaged_evr(layout_at(root)), {"magpie-bin": "0.1.2-1"})

    def test_a_pkgbuild_without_a_pkgrel_is_not_an_answer(self):
        # Half a version is not a version: a file with pkgver and no pkgrel is
        # one makepkg would refuse, and it must not read as a publishable tree.
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.2"})
            path = root / "packages" / "magpie-bin" / "PKGBUILD"
            path.write_text("pkgname=magpie-bin\npkgver=0.1.2\n")
            self.assertEqual(bump.packaged_evr(layout_at(root)), {})


class CheckTest(unittest.TestCase):
    """What `--check` says: the frozen line, and the log's meaning.

    `bump=` is what the workflow reads through pkg.sh, so its shape is a
    contract. The rest of the output is a log for whoever reads the run --
    this class used to assert all five lines verbatim and in order, which made
    a reworded prose line a test failure with no caller behind it.
    """

    def lines(self, fetch, out_root) -> list[str]:
        buffer = io.StringIO()
        with nothing_declared(), contextlib.redirect_stdout(buffer):
            bump.check(layout=layout_at(out_root), fetch=fetch)
        return buffer.getvalue().splitlines()

    def log_of(self, fetch, out_root) -> dict:
        """The log's meaning: what was compared, and what it concluded.

        The frozen lines are read through `line_field_or` -- the production
        reader, in bash -- so a duplicated `bump=` fails here exactly as it
        fails the step. This class used to split them itself, which accepted a
        duplicate and took the last one. The rest of the output is a log for
        whoever reads the run, and is read as prose on purpose.
        """
        text = "\n".join(self.lines(fetch, out_root)) + "\n"
        parsed = {
            "upstream": frozen.field("upstream", text),
            "packaged": {},
            "served": {},
            "bump": frozen.field("bump", text),
            "carried": frozen.field("carried", text),
            "behind": {},
            "warnings": [],
        }
        for line in text.splitlines():
            if line.startswith("packaged "):
                pkg, _, ver = line.removeprefix("packaged ").partition("=")
                parsed["packaged"][pkg] = ver
            elif line.startswith("served "):
                pkg, _, ver = line.removeprefix("served ").partition("=")
                parsed["served"][pkg] = ver
            elif line.startswith("behind "):
                pkg, _, reason = line.removeprefix("behind ").partition("=")
                parsed["behind"][pkg] = reason
            elif line.startswith("warning:"):
                parsed["warnings"].append(line)
        return parsed

    def test_reports_every_package_that_is_behind(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.9", "magpie-cli-bin": "0.1.9"})
            log = self.log_of(fetch, root)
        self.assertEqual(log["upstream"], "0.1.10")
        self.assertEqual(log["packaged"], {"magpie-bin": "0.1.9-1", "magpie-cli-bin": "0.1.9-1"})
        self.assertEqual(log["bump"], "1")
        self.assertEqual(
            log["behind"], {"magpie-bin": "upstream", "magpie-cli-bin": "upstream"}
        )

    def test_hands_over_the_version_the_tree_carries(self):
        # The fifth direction asks the Release question about this, not about
        # 上游's: a pinned tree carries the older 版本 by design, and the
        # question has to be about what the tree would publish.
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.9", "magpie-cli-bin": "0.1.9"})
            log = self.log_of(fetch, root)

        self.assertEqual(log["carried"], "0.1.9")

    def test_says_nothing_to_do_when_the_versions_match(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
            log = self.log_of(fetch, root)
        self.assertEqual(log["bump"], "0")
        self.assertEqual(log["behind"], {})
        self.assertEqual(
            log["packaged"], {"magpie-bin": "0.1.10-1", "magpie-cli-bin": "0.1.10-1"}
        )

    def test_treats_a_missing_pkgbuild_as_behind(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            log = self.log_of(fetch, Path(tmp))
        # No PKGBUILD on disk at all: nothing to report as packaged, and the
        # reader is warned rather than left to read an empty section.
        self.assertEqual(log["packaged"], {})
        self.assertEqual(len(log["warnings"]), 1)
        self.assertEqual(log["bump"], "1")
        self.assertEqual(
            log["behind"], {"magpie-bin": "upstream", "magpie-cli-bin": "upstream"}
        )

    def test_one_missing_pkgbuild_is_still_behind(self):
        # The other package is current, so only the absent one is behind; a
        # reader that walked the rendered packages would see nothing to do.
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.10"})
            log = self.log_of(fetch, root)
        self.assertEqual(log["packaged"], {"magpie-bin": "0.1.10-1"})
        self.assertEqual(log["bump"], "1")
        self.assertEqual(log["behind"], {"magpie-cli-bin": "upstream"})

    def test_an_upstream_reupload_under_the_same_tag_is_behind(self):
        # 上游 replaced the asset without moving the tag: the version matches,
        # the tree matches what the 软件源 serves, and only the release's own
        # checksums can say the bytes changed. Without this the new bytes would
        # never build -- and `makepkg` would refuse them the day something did.
        table = version_sources(github="0.1.10", site="0.1.10")
        table[sums_url("0.1.10")] = sums_body(
            {
                "magpie-linux-amd64": "3" * 64,  # replaced upstream
                "magpie-cli-linux-amd64": ASSET_SUM,  # untouched
            }
        )
        fetch = FetchSpy(table)
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(
                Path(tmp), {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"}
            )
            log = self.log_of(fetch, root)
        self.assertEqual(log["bump"], "1")
        # One package's bytes changed, and only that package is behind -- for
        # the reason only the release's own sums can see.
        self.assertEqual(log["behind"], {"magpie-bin": "checksums"})

    def test_a_committed_pkgbuild_that_is_not_its_render_is_reported(self):
        # The edit that starts a run through publish.yml's paths filter, finds
        # bump=0 and prints "nothing to publish": without this, it ships later,
        # bundled with whatever the next 版本 happens to be.
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(
                Path(tmp), {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"}
            )
            path = root / "packages" / "magpie-cli-bin" / "PKGBUILD"
            path.write_text(path.read_text().replace("pkgrel=1", "pkgrel=2"))
            with nothing_declared(), self.assertRaises(bump.BumpError) as caught:
                bump.check(layout=layout_at(root), fetch=fetch)
        self.assertIn("magpie-cli-bin", str(caught.exception))
        self.assertIn("re-render", str(caught.exception))
        # This repository's own problem, so it is reported before the network.
        self.assertEqual(fetch.urls, [])

    def test_a_template_that_no_longer_fits_is_reported_before_the_network(self):
        """The other half of "is this tree sound", in the mode the pipeline runs.

        Each drift here edits the template *and* its committed PKGBUILD together,
        so `values_of` still matches and the drift guard is satisfied: what
        catches it is `read_template`, which the render used to be the only
        caller of. Reaching it from `--check` is the fix -- the tree stops
        reading sound, instead of the failure waiting for the next 版本.
        """
        drifts = {
            "arch=('x86_64')": ("arch=()", "leaves out x86_64"),
            "pkgname=magpie-bin": ("pkgname=magpie-other", "files it as magpie-bin"),
        }
        for old, (new, message) in drifts.items():
            with self.subTest(drift=old):
                fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
                with tempfile.TemporaryDirectory() as tmp:
                    root = staged_root(
                        Path(tmp), {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"}
                    )
                    templates = Path(tmp) / "templates"
                    shutil.copytree(REPO / "templates", templates)
                    for path in (
                        templates / "magpie-bin.PKGBUILD.in",
                        root / "packages" / "magpie-bin" / "PKGBUILD",
                    ):
                        text = path.read_text()
                        self.assertIn(old, text)
                        path.write_text(text.replace(old, new))

                    with nothing_declared(), self.assertRaises(bump.BumpError) as caught:
                        bump.check(layout=layout_at(root, templates=templates), fetch=fetch)

                self.assertIn("magpie-bin", str(caught.exception))
                self.assertIn(message, str(caught.exception))
                self.assertEqual(fetch.urls, [])

    def test_every_template_is_read_even_before_it_is_rendered(self):
        # A package never rendered is 落后, not drift -- but its template is one
        # this repository will render, so a template that cannot render is the
        # entry check's problem too.
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            templates = Path(tmp) / "templates"
            shutil.copytree(REPO / "templates", templates)
            path = templates / "magpie-cli-bin.PKGBUILD.in"
            path.write_text(path.read_text().replace("arch=('x86_64')", "arch=('aarch64')"))
            with nothing_declared(), self.assertRaises(bump.BumpError) as caught:
                bump.check(layout=layout_at(Path(tmp), templates=templates), fetch=fetch)
        self.assertIn("magpie-cli-bin", str(caught.exception))
        self.assertEqual(fetch.urls, [])


class PinnedTreeTest(unittest.TestCase):
    """A rollback that records itself, so the next scheduled run honours it.

    `WANTED` reaches one run and is forgotten with the container. The run that
    performs a rollback commits the PKGBUILDs it rendered, and that state reads
    as 落后 to every later run: the tree carries an older 版本 than 上游, so the
    next 04:17 renders the newer one and publishes it again -- a rollback that
    lasts a day, with no run saying it had undone it. The pin is the missing
    record: one line in the tree the run commits, read by the entry check, and
    printed in the log of every run it is holding back.
    """

    def check_tree(
        self, tree: Path, *, served: dict[str, str] | None, upstream: str = "0.1.10"
    ) -> tuple[dict, list[str]]:
        """One `--check` over the tree as it stands, plus a 软件源 if named."""
        fetch = FetchSpy(version_sources(github=upstream, site=upstream))
        if served is not None:
            fetch.table[served_at().database] = database_body(served)
        buffer = io.StringIO()
        declared = address_env() if served is not None else {}
        with mock.patch.dict(os.environ, declared, clear=True):
            with contextlib.redirect_stdout(buffer):
                bump.check(layout=layout_at(tree), fetch=fetch)
        lines = buffer.getvalue().splitlines()
        text = "\n".join(lines) + "\n"
        log = {"bump": frozen.field("bump", text), "pinned": frozen.field("pinned", text), "behind": {}}
        for line in lines:
            if line.startswith("behind "):
                pkg, _, reason = line.removeprefix("behind ").partition("=")
                log["behind"][pkg] = reason
        return log, lines

    def check_pinned(
        self, tree: Path, pinned: str, *, served: dict[str, str] | None, upstream: str = "0.1.10"
    ) -> tuple[dict, list[str]]:
        """A staged tree that carries `pinned`, then one `--check` over it."""
        (tree / bump.PIN_FILE).write_text(pinned + "\n")
        return self.check_tree(tree, served=served, upstream=upstream)

    def test_a_pinned_tree_is_not_behind_when_upstream_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = staged_root(Path(tmp), {"magpie-bin": "0.1.9", "magpie-cli-bin": "0.1.9"})
            log, lines = self.check_pinned(
                tree,
                "0.1.9",
                served={"magpie-bin": "0.1.9-1", "magpie-cli-bin": "0.1.9-1"},
            )
        self.assertEqual(log["behind"], {})
        self.assertEqual(log["bump"], "0")
        self.assertTrue(log["pinned"].startswith("0.1.9"), log["pinned"])
        # The log says why nothing is being published, so a pin nobody
        # remembers is a line in every run rather than a silent repository.
        self.assertTrue(any("stays where it was told" in line for line in lines), lines)

    def test_a_pinned_tree_that_is_not_served_still_publishes(self):
        # The pin says which 版本 the tree stays at; it does not say the build
        # reached the 软件源. The rollback's own run is what puts it there, and
        # that run starts from exactly this state.
        with tempfile.TemporaryDirectory() as tmp:
            tree = staged_root(Path(tmp), {"magpie-bin": "0.1.9", "magpie-cli-bin": "0.1.9"})
            log, _ = self.check_pinned(
                tree,
                "0.1.9",
                served={"magpie-bin": "0.1.10-1", "magpie-cli-bin": "0.1.10-1"},
            )
        self.assertEqual(
            log["behind"], {"magpie-bin": "unpublished", "magpie-cli-bin": "unpublished"}
        )
        self.assertEqual(log["bump"], "1")

    def test_a_pin_the_tree_does_not_carry_holds_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = staged_root(Path(tmp), {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
            log, lines = self.check_pinned(tree, "0.1.9", served=None)
        self.assertEqual(log["bump"], "0")
        self.assertTrue(any("holds nothing" in line for line in lines), lines)

    def test_the_check_reads_the_pin_out_of_the_tree(self):
        # The production wire, and the only one: the file is where a pin comes
        # from, so a check that did not open it would leave every test above
        # green -- the helper writes the file it then honours. Here the tree
        # carries the file and the helper wrote nothing.
        with tempfile.TemporaryDirectory() as tmp:
            tree = staged_root(Path(tmp), {"magpie-bin": "0.1.9", "magpie-cli-bin": "0.1.9"})
            (tree / bump.PIN_FILE).write_text("0.1.9\n")
            log, lines = self.check_tree(tree, served=None)
        self.assertTrue(log["pinned"].startswith("0.1.9"), log["pinned"])
        self.assertEqual(log["bump"], "0")
        self.assertTrue(any("stays where it was told" in line for line in lines), lines)

    def test_a_tree_without_a_pin_prints_no_line(self):
        # The other two states are "one line" and "refused by name". A log that
        # said `pinned=None` every run would be a line nobody asked for, and it
        # would make the pin look like a state this tree is in rather than a
        # record someone left.
        with tempfile.TemporaryDirectory() as tmp:
            tree = staged_root(Path(tmp), {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
            log, lines = self.check_tree(tree, served=None)
        self.assertIsNone(log["pinned"])
        self.assertFalse([line for line in lines if line.startswith("pinned=")], lines)

    def test_the_pin_is_one_version_or_a_named_refusal(self):
        # A pin nothing can read must not read as "nothing is pinned": that is
        # the defect the file exists to fix, and a typo in it would cost a day
        # of publishing.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(bump.pinned_of(root))
            (root / bump.PIN_FILE).write_text("0.1.9\n")
            self.assertEqual(bump.pinned_of(root), "0.1.9")
            for bad in ("0.1.9\n0.1.10\n", "main\n", "\n"):
                with self.subTest(bad=bad):
                    (root / bump.PIN_FILE).write_text(bad)
                    with self.assertRaises(bump.BumpError):
                        bump.pinned_of(root)


class SyntaxDoorTest(unittest.TestCase):
    """The entry check read the PKGBUILD as text; nothing asked bash about it.

    `read_template` checks the five ways a template restates the arch table and
    `values_of` checks that the committed file is that template's render, and
    both read text -- so a `package()` body with a stray quote passed every gate
    the job runs. The only parser in the pipeline was
    `makepkg --printsrcinfo` in scripts/probe-repo-build.sh's case 6, and
    scripts/check.sh skips the probe where makepkg is missing, which is the
    runner. What the fixture below reproduces is a real edit: the same broken
    body in the template *and* in its render, so the drift guard is satisfied
    and only a parser can object.
    """

    # A body bash cannot parse. Appended to the end of the file: the template
    # stays each other's exact match, which is the shape a real edit has.
    BROKEN = "\nif true; then\n"

    def broken(self, root: Path) -> Path:
        """The tree with every template and every committed PKGBUILD broken."""
        templates = root / "templates"
        shutil.copytree(REPO / "templates", templates)
        for path in [
            *templates.glob("*.PKGBUILD.in"),
            *(root / "packages").glob("*/PKGBUILD"),
        ]:
            with path.open("a") as handle:
                handle.write(self.BROKEN)
        return templates

    def test_every_committed_pkgbuild_in_this_repository_parses(self):
        # The real files, not a fixture: the templates this repository ships
        # must render to something bash reads.
        for pkg in bump.PACKAGES:
            with self.subTest(pkg=pkg):
                text = (REPO / "packages" / pkg / "PKGBUILD").read_text()
                self.assertIsNone(bump.parse_failure(text))

    def test_a_body_bash_cannot_parse_is_refused_before_the_network(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
            templates = self.broken(root)
            with nothing_declared(), self.assertRaises(bump.BumpError) as caught:
                bump.check(layout=layout_at(root, templates=templates), fetch=fetch)
        self.assertIn("bash cannot parse", str(caught.exception))
        self.assertIn("magpie-bin", str(caught.exception))
        # This repository's own problem, so it is answered before any fetch.
        self.assertEqual(fetch.urls, [])

    def test_the_render_refuses_to_write_one(self):
        # The same question asked before the write: `--render` used to fill the
        # markers and put the file down whatever its body said.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            templates = root / "templates"
            shutil.copytree(REPO / "templates", templates)
            for path in templates.glob("*.PKGBUILD.in"):
                with path.open("a") as handle:
                    handle.write(self.BROKEN)
            fetch = FetchSpy(
                {
                    sums_url("0.1.2"): sums_body(
                        {
                            "magpie-linux-amd64": ASSET_SUM,
                            "magpie-cli-linux-amd64": ASSET_SUM,
                        }
                    ),
                    license_url("0.1.2"): b"license",
                    ICON_URL: b"icon",
                }
            )
            with self.assertRaises(bump.BumpError) as caught:
                bump.render("0.1.2", layout=layout_at(root, templates=templates), fetch=fetch)
        self.assertIn("not a bash script", str(caught.exception))
        self.assertFalse((root / "packages").exists())


class ServedTest(unittest.TestCase):
    """What the 软件源 carries, compared against what this tree would publish.

    `bump=` used to mean one thing: the rendered pkgver is not 上游's. The
    版本 a 包文件 is named after and a database entry states is the pair
    `<pkgver>-<pkgrel>`, and only the second half can say "rebuild this" -- so
    the entry check reads the database pacman reads, and a tree that differs
    from it is 落后 even when 上游 has not moved. That is the difference
    between a rebuild shipping and a rebuild being a silent no-op.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def rebuilt_templates(self, pkgrel: str = "2") -> Path:
        """The repository's templates with a different pkgrel -- a rebuild."""
        templates = self.tmp / "templates"
        shutil.copytree(REPO / "templates", templates)
        for path in templates.glob("*.PKGBUILD.in"):
            path.write_text(path.read_text().replace("pkgrel=1", f"pkgrel={pkgrel}"))
        return templates

    def log_of(self, fetch, root: Path, *, templates: Path | None = None) -> dict:
        buffer = io.StringIO()
        with mock.patch.dict(os.environ, address_env(), clear=True):
            with contextlib.redirect_stdout(buffer):
                bump.check(layout=layout_at(root, templates=templates), fetch=fetch)
        lines = buffer.getvalue().splitlines()
        served = {}
        behind = {}
        for line in lines:
            if line.startswith("served "):
                pkg, _, evr = line.removeprefix("served ").partition("=")
                served[pkg] = evr
            elif line.startswith("behind "):
                pkg, _, reason = line.removeprefix("behind ").partition("=")
                behind[pkg] = reason
        return {
            "bump": frozen.field("bump", "\n".join(lines) + "\n"),
            "served": served,
            "behind": behind,
            "lines": lines,
        }

    def fetch_with(self, served_body, versions=("0.1.10",)) -> FetchSpy:
        table = version_sources(github=versions[0], site=versions[0])
        table[served_at().database] = served_body
        return FetchSpy(table)

    def test_a_rebuild_the_tree_has_and_the_repository_does_not_is_behind(self):
        # pkgrel is the only way to say "same 版本, rebuilt": every gate is
        # green, 上游 has not moved, and before this the run said
        # "nothing to publish" while a client kept the old bytes.
        templates = self.rebuilt_templates("2")
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"},
                           templates=templates)
        fetch = self.fetch_with(
            database_body({"magpie-bin": "0.1.10-1", "magpie-cli-bin": "0.1.10-1"})
        )
        log = self.log_of(fetch, root, templates=templates)
        self.assertEqual(log["bump"], "1")
        # pkgver matches 上游 and only the build does not: that is the reason
        # the run prints, instead of leaving the reader to diff two sections.
        self.assertEqual(
            log["behind"],
            {"magpie-bin": "unpublished", "magpie-cli-bin": "unpublished"},
        )

    def test_a_package_the_repository_serves_and_the_inventory_dropped_is_behind(self):
        # The fourth direction. A package that left the 包清单 is not in the
        # inventory loop, so nothing counted it: its files and its database
        # entry kept being served, and build-repo.sh's retire only runs behind
        # `bump=1`. Dropping a package therefore waited for an unrelated 上游
        # move -- forever, if 上游 went quiet.
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = self.fetch_with(
            database_body(
                {
                    "magpie-bin": "0.1.10-1",
                    "magpie-cli-bin": "0.1.10-1",
                    "ghost-bin": "9.9.9-1",
                }
            )
        )
        log = self.log_of(fetch, root)
        self.assertEqual(log["bump"], "1")
        self.assertEqual(log["behind"], {"ghost-bin": "retired"})
        # The 软件源 still has to be readable for this direction to be known.
        self.assertEqual(log["served"]["ghost-bin"], "9.9.9-1")

    def test_a_tree_that_renders_an_older_build_than_the_one_served_is_refused(self):
        # pkgrel only goes up. `-1` against a 软件源 serving `-2` of the same
        # 版本 is not 落后 -- it contradicts itself, and reading it as 未发布
        # published the wrong build (a Release takes the newest on disk) and then
        # reported itself as 未发布 again: a green loop that shipped nothing new.
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = self.fetch_with(
            database_body({"magpie-bin": "0.1.10-2", "magpie-cli-bin": "0.1.10-2"})
        )

        with self.assertRaises(bump.BumpError) as caught:
            self.log_of(fetch, root)

        self.assertIn("pkgrel", str(caught.exception))
        self.assertIn("0.1.10-2", str(caught.exception))

    def test_a_run_reads_the_release_table_once(self):
        # The 版本 decision and the third direction (checksums) are the same
        # reading now. They used to be two fetches of one URL, so the run could
        # pick a 版本 from one answer and compare the tree against another.
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = self.fetch_with(
            database_body({"magpie-bin": "0.1.10-1", "magpie-cli-bin": "0.1.10-1"})
        )
        self.log_of(fetch, root)
        self.assertEqual(fetch.urls.count(sums_url("0.1.10")), 1)

    def test_a_served_pkgrel_this_repository_cannot_read_is_refused(self):
        # Arch's `<int>.<int>` shape. `pkgrel_of` used to answer None for it and
        # the contradiction branch above then skipped itself, so the same pair
        # read as 未发布: the run would publish forever while the database named
        # the newer build. A value nobody can read is a contradiction to report.
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = self.fetch_with(
            database_body({"magpie-bin": "0.1.10-2.1", "magpie-cli-bin": "0.1.10-2.1"})
        )

        with self.assertRaises(bump.BumpError) as caught:
            self.log_of(fetch, root)

        self.assertIn("0.1.10-2.1", str(caught.exception))
        self.assertIn("whole-number", str(caught.exception))

    def test_a_served_pkgrel_of_another_version_is_not_this_runs_question(self):
        # 上游 moved on and the 软件源 still serves the old build. That is 落后
        # for the upstream reason, and the tree is publishing the new 版本
        # anyway: refusing the pair would block the move 上游 just made. The
        # pkgrel comparison is only ever about two builds of one 版本.
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = self.fetch_with(
            database_body({"magpie-bin": "0.1.10-2.1", "magpie-cli-bin": "0.1.10-2.1"}),
            versions=("0.1.11",),
        )
        log = self.log_of(fetch, root)
        self.assertEqual(log["bump"], "1")
        self.assertEqual(
            log["behind"], {"magpie-bin": "upstream", "magpie-cli-bin": "upstream"}
        )

    def test_a_tree_the_repository_already_serves_is_not_behind(self):
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = self.fetch_with(
            database_body({"magpie-bin": "0.1.10-1", "magpie-cli-bin": "0.1.10-1"})
        )
        log = self.log_of(fetch, root)
        self.assertEqual(log["bump"], "0")
        self.assertEqual(
            log["served"], {"magpie-bin": "0.1.10-1", "magpie-cli-bin": "0.1.10-1"}
        )

    def test_nothing_published_yet_is_behind(self):
        # No gh-pages on the first run: a 404 is an empty repository, not a
        # reason to call the tree current.
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = self.fetch_with(None)
        log = self.log_of(fetch, root)
        self.assertEqual(log["bump"], "1")
        self.assertEqual(log["served"], {})

    def test_a_repository_that_cannot_be_read_falls_back_to_upstream(self):
        # Pages having a bad minute must not read as "nothing is published":
        # that answer would rebuild and republish, and it would look like a
        # rebuild the tree asked for.
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = self.fetch_with(urllib.error.URLError("connection refused"))
        log = self.log_of(fetch, root)
        self.assertEqual(log["bump"], "0")
        self.assertTrue(
            any("comparing against 上游 alone" in line for line in log["lines"]),
            log["lines"],
        )

    def test_a_body_that_is_not_a_database_falls_back_to_upstream(self):
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        log = self.log_of(self.fetch_with(b"not a tarball"), root)
        self.assertEqual(log["bump"], "0")
        self.assertTrue(any("is not a database" in line for line in log["lines"]), log["lines"])

    def test_a_run_that_declares_no_address_never_asks_for_the_database(self):
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        buffer = io.StringIO()
        with nothing_declared():
            with contextlib.redirect_stdout(buffer):
                bump.check(layout=layout_at(root), fetch=fetch)
        self.assertIn(f"{bump.RELEASES}/download/v0.1.10/SHA256SUMS", fetch.urls)
        self.assertEqual([url for url in fetch.urls if url.endswith(".db.tar.gz")], [])
        # And it says so: the 上游-only comparison used to be the silent half of
        # the fallback GLOSSARY documents ("日志会说明").
        lines = buffer.getvalue().splitlines()
        self.assertTrue(any("no 软件源 address declared" in line for line in lines), lines)
        self.assertIn("compared=upstream", lines)

    def test_half_a_declaration_is_refused_by_the_check(self):
        # A name typed into the job env with its neighbour forgotten answered
        # the same None as "nothing declared", which silently turned off the
        # two directions that read the 软件源 -- and the pkgrel refusal with
        # them. The refusal belongs to the question, and it names the gap.
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        env = address_env()
        env.pop("ARCH")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(bump.BumpError) as caught:
                bump.check(layout=layout_at(root), fetch=fetch)
        # The wording is ServedFromEnvTest's to hold; what this adds is that the
        # refusal is the check's -- asked before anything is fetched.
        self.assertIn("ARCH", str(caught.exception))
        self.assertEqual(fetch.urls, [])

    def test_a_run_that_read_the_repository_says_it_compared_both_halves(self):
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = self.fetch_with(
            database_body({"magpie-bin": "0.1.10-1", "magpie-cli-bin": "0.1.10-1"})
        )
        log = self.log_of(fetch, root)
        self.assertIn("compared=upstream+served", log["lines"])

    def test_a_repository_that_could_not_be_read_says_which_half_was_compared(self):
        # The other None: declared, asked, unreadable. served_evr logs the
        # reason; the run states the conclusion, so neither is left to infer.
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        log = self.log_of(self.fetch_with(b"not a tarball"), root)
        self.assertIn("compared=upstream", log["lines"])
        self.assertTrue(any("is not a database" in line for line in log["lines"]), log["lines"])


class ServedFromEnvTest(unittest.TestCase):
    """The three names a run declares, and what their absence means."""

    def test_all_three_make_the_address_and_its_database(self):
        env = {"PAGES_URL": "https://h.invalid/r", "REPO_NAME": "magpie", "ARCH": "x86_64"}
        with mock.patch.dict(os.environ, env, clear=True):
            served = bump.served_from_env()
        self.assertEqual(served, bump.Served(url="https://h.invalid/r", section="magpie", arch="x86_64"))
        self.assertEqual(served.database, "https://h.invalid/r/x86_64/magpie.db.tar.gz")

    def test_no_name_at_all_declares_no_address(self):
        # A hand run: the 上游-only comparison, and the log says so.
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(bump.served_from_env())

    def test_two_of_three_names_is_a_half_declaration_the_run_refuses(self):
        # It used to answer None, the same value as "nothing declared": a
        # mistyped name in the job env turned off the two directions that read
        # the 软件源 -- and the pkgrel refusal -- and every run stayed green.
        for missing in ("PAGES_URL", "REPO_NAME", "ARCH"):
            with self.subTest(missing=missing):
                env = {"PAGES_URL": "https://h.invalid/r", "REPO_NAME": "magpie", "ARCH": "x86_64"}
                env.pop(missing)
                with mock.patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(bump.BumpError) as caught:
                        bump.served_from_env()
                self.assertIn(missing, str(caught.exception))
                self.assertIn("declare all three or none", str(caught.exception))


class MainTest(unittest.TestCase):
    """The process boundary: argv in, 冻结行 and an exit code out.

    CI reads exactly those two things, and nothing drove the entry point that
    prints them: every other class calls check()/render() one level below, so a
    swallowed exit code kept the suite green. `fetch` and the layout are handed
    to main() now, which is what lets this class exist.
    """

    VERSION = "0.1.2"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.layout = layout_at(self.root)

    def table(self, version: str = VERSION) -> dict:
        return release_table(version)

    def cli(self, *argv, fetch) -> tuple[object, str]:
        """Run the entry point; return its exit code and what it printed."""
        return run_main(*argv, fetch=fetch, layout=self.layout)

    def test_check_hands_over_both_frozen_lines(self):
        fetch = FetchSpy({**version_sources(github=self.VERSION, site=self.VERSION), **self.table()})
        code, out = self.cli("--check", fetch=fetch)
        self.assertEqual(code, 0)
        self.assertIn(f"upstream={self.VERSION}", out.splitlines())
        self.assertIn("bump=1", out.splitlines())

    def test_render_writes_and_hands_over_the_version(self):
        code, out = self.cli("--render", "--version", self.VERSION, fetch=FetchSpy(self.table()))
        self.assertEqual(code, 0)
        self.assertIn(f"ver={self.VERSION}", out.splitlines())
        for pkg in bump.PACKAGES:
            self.assertIn(
                f"pkgver={self.VERSION}",
                (self.root / "packages" / pkg / "PKGBUILD").read_text(),
            )

    def test_a_render_without_a_version_reads_the_table_once(self):
        # Deciding the 版本 reads the release's table; the render used to read
        # the same URL a second time. The reading the decision was made on now
        # travels into the render, so one run has one reading either way.
        fetch = FetchSpy(
            {**version_sources(github=self.VERSION, site=self.VERSION), **self.table()}
        )
        code, out = self.cli("--render", fetch=fetch)
        self.assertEqual(code, 0)
        self.assertIn(f"ver={self.VERSION}", out.splitlines())
        self.assertEqual(fetch.urls.count(sums_url(self.VERSION)), 1)

    def test_no_run_reads_the_same_url_twice(self):
        # One run, one reading per URL. The 版本 decision, the checksum
        # comparison and the render all ask the same producer, and a second
        # fetch of one URL is how a run decides on one answer and compares
        # against another -- the exact defect this pair of modes had, in `check`
        # first and then again in `render`. The spy records every read, so the
        # rule is one assertion per mode instead of a review.
        for label, argv in (
            ("check", ("--check",)),
            ("render-a-pinned-version", ("--render", "--version", self.VERSION)),
            ("render-the-walk", ("--render",)),
        ):
            with self.subTest(mode=label):
                fetch = FetchSpy(
                    {**version_sources(github=self.VERSION, site=self.VERSION), **self.table()}
                )
                code, _ = self.cli(*argv, fetch=fetch)
                self.assertEqual(code, 0)
                twice = sorted({url for url in fetch.urls if fetch.urls.count(url) > 1})
                self.assertEqual(twice, [])

    def test_a_bump_error_is_one_line_and_not_a_zero_exit(self):
        # The exit code is what makes the workflow step fail; a zero here turns
        # a broken render into "nothing to publish".
        fetch = FetchSpy(self.table())
        code, _ = self.cli("--render", "--version", "0.1.2-rc1", fetch=fetch)
        self.assertIsInstance(code, str)
        self.assertIn("digits separated by dots", str(code))
        self.assertEqual(fetch.urls, [])

    def test_a_http_error_is_one_line_and_not_a_zero_exit(self):
        url = sums_url(self.VERSION)
        fetch = FetchSpy({url: urllib.error.HTTPError(url, 403, "blocked", Message(), None)})
        code, _ = self.cli("--render", "--version", self.VERSION, fetch=fetch)
        self.assertIsInstance(code, str)
        self.assertIn("HTTP 403", str(code))

    def test_a_network_error_is_one_line_and_not_a_zero_exit(self):
        table: dict[str, bytes | None | BaseException] = {
            sums_url(self.VERSION): sums_body({"magpie-linux-amd64": "1" * 64}),
            license_url(self.VERSION): urllib.error.URLError("no route to host"),
        }
        code, _ = self.cli("--render", "--version", self.VERSION, fetch=FetchSpy(table))
        self.assertIsInstance(code, str)
        self.assertIn("network error", str(code))

    def test_a_version_beside_check_is_refused(self):
        # It used to be accepted and silently ignored, which reads as "check
        # this version" and does nothing of the sort.
        code, _ = self.cli("--check", "--version", self.VERSION, fetch=FetchSpy({}))
        self.assertEqual(code, 2)


class ExitFactsTest(unittest.TestCase):
    """Which exit asks which question: only the comparison needs both facts.

    The tree read `PINNED` and the 软件源 address while it was built, so every
    exit inherited both refusals -- the 包清单's only exit printed two directory
    names and could still die because a pin elsewhere in the tree was
    malformed. These drive `main()` against a staged tree (its own templates,
    its own packages), because the construction under test is the production
    one: a handed-in layout would decide the answer for it.
    """

    VERSION = "0.1.2"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def tree(self, *, pin: str | None = None) -> Path:
        """A staged checkout: its own templates, its packages, and maybe a pin."""
        staged_checkout(self.root, {pkg: self.VERSION for pkg in bump.PACKAGES})
        if pin is not None:
            (self.root / bump.PIN_FILE).write_text(pin + "\n")
        return self.root

    def cli(self, *argv, fetch=None, env: dict | None = None) -> tuple[object, str]:
        """Run the entry point against the staged tree, with a clean environment."""
        return run_main(
            *argv,
            fetch=fetch or FetchSpy({}),
            root=self.root,
            env=env if env is not None else {},
        )

    def test_the_package_list_does_not_read_the_pin(self):
        # `--packages` is the 包清单's only exit and every run passes through
        # it: a line it never reads must not take it down.
        self.tree(pin="not-a-version")
        code, out = self.cli("--packages")
        self.assertEqual(code, 0)
        # The line's spelling belongs to the class that owns the 包清单 format
        # (test_inventory's), so this asks only what this ticket is about.
        self.assertEqual(len(out.splitlines()), len(bump.PACKAGES))
        for pkg in bump.PACKAGES:
            self.assertIn(pkg, out)

    def test_the_package_list_does_not_read_the_address(self):
        # Half an address is a configuration error, and it belongs to the
        # question that reads the address -- not to this one.
        self.tree()
        code, out = self.cli("--packages", env={"PAGES_URL": "https://example.invalid/r"})
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), len(bump.PACKAGES))

    def test_the_check_still_refuses_a_malformed_pin(self):
        self.tree(pin="not-a-version")
        code, _ = self.cli("--check")
        self.assertIsInstance(code, str)
        self.assertIn(bump.PIN_FILE, str(code))
        self.assertIn("not a 版本", str(code))

    def test_a_tree_that_is_both_broken_reports_the_address(self):
        # Two configuration errors, one message: the address is read first, so
        # that is what a reader fixes first. Said once, held here.
        self.tree(pin="not-a-version")
        code, _ = self.cli("--check", env={"PAGES_URL": "https://example.invalid/r"})
        self.assertIsInstance(code, str)
        self.assertIn("half declared", str(code))
        self.assertNotIn(bump.PIN_FILE, str(code))

    def test_the_check_still_refuses_half_an_address(self):
        # By name: the message says which of the three was not declared.
        self.tree()
        code, _ = self.cli("--check", env={"PAGES_URL": "https://example.invalid/r"})
        self.assertIsInstance(code, str)
        self.assertIn("REPO_NAME", str(code))
        self.assertIn("ARCH", str(code))
        self.assertIn("declare all three or none", str(code))

    def test_the_render_does_not_read_the_pin(self):
        # Rendering a 版本 does not depend on where the tree was told to stay;
        # the pin only suppresses one direction of 落后.
        self.tree(pin="not-a-version")
        code, out = self.cli(
            "--render", "--version", self.VERSION, fetch=FetchSpy(release_table(self.VERSION))
        )
        self.assertEqual(code, 0)
        self.assertEqual(frozen.field("ver", out), self.VERSION)
        for pkg in bump.PACKAGES:
            self.assertIn(
                f"pkgver={self.VERSION}",
                (self.root / "packages" / pkg / "PKGBUILD").read_text(),
            )

    def test_the_render_walk_does_not_read_the_pin(self):
        # The pipeline's own render: no 版本 on argv, so the walk decides it.
        self.tree(pin="not-a-version")
        code, out = self.cli(
            "--render",
            fetch=FetchSpy(
                {
                    **version_sources(github=self.VERSION, site=self.VERSION),
                    **release_table(self.VERSION),
                }
            ),
        )
        self.assertEqual(code, 0)
        self.assertEqual(frozen.field("ver", out), self.VERSION)

    def test_the_render_does_not_read_the_address(self):
        self.tree()
        code, out = self.cli(
            "--render",
            "--version",
            self.VERSION,
            fetch=FetchSpy(release_table(self.VERSION)),
            env={"PAGES_URL": "https://example.invalid/r"},
        )
        self.assertEqual(code, 0)
        self.assertEqual(frozen.field("ver", out), self.VERSION)


class EntryDecisionTest(unittest.TestCase):
    """What this run publishes, and whether it records a pin.

    The decision used to live one module up, in the shell step body that called
    the check: the check compared, the caller decided. That made the
    comparison's answer ("is anything 落后") a different fact from the run's
    ("does this run publish"), and the two could disagree in one run's log --
    a `WANTED` run printed the check's `bump=0` on stderr and the relay's
    `bump=1` on stdout. Both come out of the check now.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def tree(self, version: str = "0.1.10") -> Path:
        return staged_checkout(self.root, {pkg: version for pkg in bump.PACKAGES})

    def fetch(self, upstream: str) -> FetchSpy:
        return FetchSpy(version_sources(github=upstream, site=upstream))

    def handover(self, upstream: str, *, tree_version: str = "0.1.10", wanted: str | None = None) -> dict:
        """One `--check`, its lines read the way the workflow reads them."""
        env = {} if wanted is None else {"WANTED": wanted}
        code, out = run_main("--check", fetch=self.fetch(upstream), root=self.tree(tree_version), env=env)
        self.assertEqual(code, 0, out)
        return frozen.fields(out)

    def test_a_wanted_version_is_the_target_and_records_a_pin(self):
        # A human's 版本 publishes even though the check just said the tree is
        # current; the pin is what keeps the next run from undoing it.
        handed = self.handover("0.1.10", wanted="0.1.9")
        self.assertEqual(handed["upstream"], "0.1.10")
        self.assertEqual(handed["target"], "0.1.9")
        self.assertEqual(handed["bump"], "1")
        self.assertEqual(handed["pin"], "1")

    def test_asking_for_the_version_the_tree_carries_is_not_a_pin(self):
        # The retry the fifth direction needs: re-running the 版本 the tree
        # already carries. Writing that down as a pin would suppress the
        # upstream direction on every later run, and the pin outlives the run.
        handed = self.handover("0.1.10", wanted="0.1.10")
        self.assertEqual(handed["target"], "0.1.10")
        self.assertEqual(handed["pin"], "0")
        self.assertEqual(handed["bump"], "1")

    def test_a_scheduled_run_targets_upstream_and_records_no_pin(self):
        handed = self.handover("0.1.11")
        self.assertEqual(handed["target"], "0.1.11")
        self.assertEqual(handed["pin"], "0")
        self.assertEqual(handed["bump"], "1")

    def test_a_current_tree_does_not_publish(self):
        handed = self.handover("0.1.10")
        self.assertEqual(handed["bump"], "0")
        self.assertEqual(handed["target"], "0.1.10")


class HandoverKeysTest(unittest.TestCase):
    """入口检查交出的键，表里说它交出的就是那几个。

    The step reads them through the gates now, so a key the table credits the
    entry check with and this module stopped printing fails a publish run at the
    read -- after the upstream comparison and a network round trip -- with "no
    such frozen key". The table is asked through the test-side reader; this
    module's side is a real `--check` run, because a hand-written list of the
    four keys would be a third spelling of them (and a fifth key printed here,
    declared nowhere, is a log line until somebody reads it).
    """

    def tabled(self) -> list[str]:
        rows = [row["key"] for row in frozen.table() if row["producer"] == "entry-check"]
        self.assertTrue(rows, "the table gives the entry check no keys; reword this test")
        return rows

    def test_a_real_run_emits_every_key_the_table_gives_the_entry_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_checkout(Path(tmp), {pkg: "0.1.10" for pkg in bump.PACKAGES})
            fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
            code, out = run_main("--check", fetch=fetch, root=root)

        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        missing = [
            key for key in self.tabled() if not any(line.startswith(f"{key}=") for line in lines)
        ]
        self.assertEqual(missing, [])


class BehindTest(unittest.TestCase):
    """The five directions, judged where they are decided.

    `behind` had one caller and no test: its inputs were chewed by the caller
    and its answer was read back out of a log. It takes the facts now --
    including the one the network had to answer -- and returns the directions,
    so a direction can be changed without running a whole `--check`.
    """

    LAYOUT = bump.layout_for(REPO)  # only its 包清单 is read

    def directions(
        self,
        packaged: dict[str, str],
        *,
        upstream: str = "0.1.10",
        served: dict[str, str] | None = None,
        stale: set[str] | None = None,
        pinned: str | None = None,
        released: bool | None = None,
    ) -> dict[str, str]:
        return bump.behind(
            self.LAYOUT,
            upstream=upstream,
            packaged=packaged,
            served=served,
            stale=set() if stale is None else stale,
            pinned=pinned,
            released=released,
        )

    def test_a_pkgver_that_is_not_upstreams_is_behind(self):
        self.assertEqual(
            self.directions({pkg: "0.1.9-1" for pkg in bump.PACKAGES}),
            {pkg: bump.UPSTREAM_MOVED for pkg in bump.PACKAGES},
        )

    def test_the_pin_suppresses_that_one_direction(self):
        self.assertEqual(
            self.directions({pkg: "0.1.9-1" for pkg in bump.PACKAGES}, pinned="0.1.9"),
            {},
        )

    def test_a_missing_release_is_a_direction_like_the_others(self):
        self.assertEqual(
            self.directions({pkg: "0.1.10-1" for pkg in bump.PACKAGES}, released=False),
            {pkg: bump.NO_RELEASE for pkg in bump.PACKAGES},
        )

    def test_an_existing_release_adds_nothing(self):
        self.assertEqual(
            self.directions({pkg: "0.1.10-1" for pkg in bump.PACKAGES}, released=True),
            {},
        )

    def test_a_pin_holds_the_fifth_direction_back_too(self):
        # The tree stays at 0.1.9; a missing Release for it is not a reason to
        # publish 0.1.11 over the pin.
        self.assertEqual(
            self.directions({pkg: "0.1.9-1" for pkg in bump.PACKAGES}, pinned="0.1.9", released=False),
            {},
        )

    def test_a_build_the_served_tree_does_not_have_is_unpublished(self):
        packaged = {pkg: "0.1.10-1" for pkg in bump.PACKAGES}
        self.assertEqual(
            self.directions(packaged, served={pkg: "0.1.9-1" for pkg in bump.PACKAGES}),
            {pkg: bump.NOT_PUBLISHED for pkg in bump.PACKAGES},
        )

    def test_a_table_upstream_re_uploaded_is_a_checksum_difference(self):
        packaged = {pkg: "0.1.10-1" for pkg in bump.PACKAGES}
        self.assertEqual(
            self.directions(
                packaged,
                served={pkg: "0.1.10-1" for pkg in bump.PACKAGES},
                stale={"magpie-bin"},
            ),
            {"magpie-bin": bump.UPSTREAM_REUPLOADED},
        )

    def test_a_package_dropped_from_the_inventory_is_retired(self):
        packaged = {pkg: "0.1.10-1" for pkg in bump.PACKAGES}
        retired = "magpie-docs-bin"
        self.assertEqual(
            self.directions(
                packaged,
                served={**{pkg: "0.1.10-1" for pkg in bump.PACKAGES}, retired: "0.1.10-1"},
            ),
            {retired: bump.RETIRED},
        )

    def test_a_direction_that_already_says_why_is_not_joined_by_another(self):
        self.assertEqual(
            self.directions({pkg: "0.1.9-1" for pkg in bump.PACKAGES}, released=False),
            {pkg: bump.UPSTREAM_MOVED for pkg in bump.PACKAGES},
        )


class FifthDirectionTest(unittest.TestCase):
    """The one direction the tree alone cannot answer: has its build a Release?

    A rebuild's 版本 is committed by the person who re-rendered it, so once the
    branch push lands the four directions are quiet -- and a Release that
    failed after that push is never asked for again, while the 软件源 serves a
    build nobody can describe. That question used to be the relay's, with a
    sentence in the docs standing in for a name; it is the check's now, and it
    is a direction like the other four: `behind <包>=no-release`.
    """

    VERSION = "0.1.10"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def tree(self, version: str = VERSION, *, pin: str | None = None) -> Path:
        tree = staged_checkout(self.root, {pkg: version for pkg in bump.PACKAGES})
        if pin is not None:
            (tree / bump.PIN_FILE).write_text(pin + "\n")
        return tree

    def check(
        self,
        tree: Path,
        *,
        upstream: str = VERSION,
        releases: dict | None = None,
        repo: str | None = "owner/repo",
    ) -> tuple[object, list[str], FetchSpy]:
        table = version_sources(github=upstream, site=upstream)
        table.update(releases or {})
        fetch = FetchSpy(table)
        env = {} if repo is None else {"RELEASE_REPO": repo}
        code, out = run_main("--check", fetch=fetch, root=tree, env=env)
        return code, out.splitlines(), fetch

    def behind_lines(self, lines: list[str]) -> dict[str, str]:
        return {
            line.split(" ", 1)[1].split("=", 1)[0]: line.split("=", 1)[1]
            for line in lines
            if line.startswith("behind ")
        }

    def test_a_missing_release_is_a_reason_to_publish(self):
        code, lines, fetch = self.check(
            self.tree(), releases={release_url(self.VERSION): None}
        )

        self.assertEqual(code, 0, lines)
        self.assertEqual(
            self.behind_lines(lines),
            {"magpie-bin": "no-release", "magpie-cli-bin": "no-release"},
        )
        self.assertIn("bump=1", lines)
        said = [line for line in lines if f"v{self.VERSION} has no Release" in line]
        self.assertEqual(said, [f"v{self.VERSION} has no Release: publishing it again"])
        self.assertIn(release_url(self.VERSION), fetch.urls)

    def test_an_existing_release_is_nothing_to_do(self):
        # Line for line what the run said before this question moved in: the
        # only thing a 200 may add is the fetch itself.
        code, lines, _ = self.check(
            self.tree(), releases={release_url(self.VERSION): b"{}"}
        )

        self.assertEqual(code, 0, lines)
        self.assertEqual(self.behind_lines(lines), {})
        self.assertIn("bump=0", lines)
        self.assertEqual([line for line in lines if "Release" in line], [])

    def test_a_lookup_that_failed_is_not_a_missing_release(self):
        # A 500 or a rate limit is not "no Release": it says so and publishes
        # only what the check found. Reading it as absent would republish a
        # build on a bad minute at GitHub.
        url = release_url(self.VERSION)
        code, lines, _ = self.check(
            self.tree(), releases={url: urllib.error.HTTPError(url, 500, "boom", Message(), None)}
        )

        self.assertEqual(code, 0, lines)
        self.assertEqual(self.behind_lines(lines), {})
        self.assertIn("bump=0", lines)
        self.assertTrue(
            any("cannot ask whether v0.1.10 has a Release" in line for line in lines), lines
        )

    def test_a_network_failure_is_not_a_missing_release(self):
        # The other exception a fetch can raise: no route, no DNS, no answer.
        url = release_url(self.VERSION)
        code, lines, _ = self.check(
            self.tree(), releases={url: urllib.error.URLError("no route to host")}
        )

        self.assertEqual(code, 0, lines)
        self.assertEqual(self.behind_lines(lines), {})
        self.assertIn("bump=0", lines)
        self.assertTrue(
            any("cannot ask whether v0.1.10 has a Release" in line for line in lines), lines
        )

    def test_a_run_that_is_publishing_anyway_does_not_ask(self):
        # The fifth direction is for the quiet runs: a run that is behind for
        # another reason creates the Release it publishes.
        code, lines, fetch = self.check(
            self.tree("0.1.9"), releases={release_url("0.1.9"): None}
        )

        self.assertEqual(code, 0, lines)
        self.assertEqual(
            self.behind_lines(lines), {pkg: "upstream" for pkg in bump.PACKAGES}
        )
        self.assertIn("bump=1", lines)
        self.assertNotIn(release_url("0.1.9"), fetch.urls)

    def test_without_a_repository_the_question_is_skipped_out_loud(self):
        code, lines, fetch = self.check(self.tree(), repo=None)

        self.assertEqual(code, 0, lines)
        self.assertEqual(self.behind_lines(lines), {})
        self.assertIn("bump=0", lines)
        said = [line for line in lines if "no repository named" in line]
        self.assertEqual(len(said), 1, said)
        # How to fix it, not just that it could not be asked.
        self.assertIn("(pass <owner/repo>)", said[0])
        self.assertNotIn(release_url(self.VERSION), fetch.urls)

    def test_a_pinned_tree_without_a_release_is_not_published(self):
        # The tree stays at 0.1.9 on purpose; this run would render 上游's
        # 0.1.11. Publishing that to fix a missing Release would undo the pin
        # on the next upstream move -- the day-long rollback the pin ends.
        tree = self.tree("0.1.9", pin="0.1.9")
        code, lines, _ = self.check(
            tree, upstream="0.1.11", releases={release_url("0.1.9"): None}
        )

        self.assertEqual(code, 0, lines)
        self.assertEqual(self.behind_lines(lines), {})
        self.assertIn("bump=0", lines)
        said = [line for line in lines if "has no Release" in line]
        self.assertEqual(len(said), 1, said)
        self.assertIn("dispatch 0.1.9", said[0])

    def test_the_direction_has_a_name_like_the_others(self):
        # The count GLOSSARY states used to be scraped out of a shell echo
        # because this one had no constant to be counted from.
        self.assertEqual(
            set(bump.DIRECTIONS),
            {"upstream", "unpublished", "checksums", "retired", bump.NO_RELEASE},
        )


class PackageListExitTest(unittest.TestCase):
    """The 包清单 exit keeps its own invariant: a file nobody can build from.

    The refusal used to live in the step that ran this exit, which made it the
    one decision the workflow still held. It is the exit's own: what it writes
    is a list of packages, and an empty one is not a list.
    """

    def test_an_empty_inventory_is_refused(self):
        code, out = run_main(
            "--packages", fetch=FetchSpy({}), layout=bump.layout_for(REPO, inventory={})
        )

        self.assertIsInstance(code, str)
        self.assertIn("names no packages", str(code))
        self.assertEqual(out.splitlines(), [])


class TemplatePkgnameTest(unittest.TestCase):
    """A template's literal pkgname= is the inventory key it is filed under."""

    def test_our_own_templates_carry_their_inventory_key(self):
        for pkg in bump.PACKAGES:
            with self.subTest(pkg=pkg):
                text = (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()
                bump.check_pkgname(pkg, text)

    def test_a_template_naming_another_package_is_refused(self):
        text = (REPO / "templates" / "magpie-cli-bin.PKGBUILD.in").read_text()
        drifted = text.replace("pkgname=magpie-cli-bin", "pkgname=magpie-cli-bn")
        with self.assertRaises(bump.BumpError) as caught:
            bump.check_pkgname("magpie-cli-bin", drifted)
        self.assertIn("magpie-cli-bn", str(caught.exception))
        self.assertIn("magpie-cli-bin", str(caught.exception))

    def test_a_template_with_no_pkgname_is_named(self):
        with self.assertRaises(bump.BumpError) as caught:
            bump.check_pkgname("magpie-bin", "pkgver=0.1.2\n")
        self.assertIn("no pkgname=", str(caught.exception))

    def test_a_quoted_value_reads_the_way_the_renderer_writes_it(self):
        # pkg_field takes the first token and strips one matching pair of
        # quotes; this reads the same shape, so a quoted template is not a
        # drift the shell and python disagree about.
        bump.check_pkgname("magpie-bin", "pkgname='magpie-bin'\n")


class TemplatePkgrelTest(unittest.TestCase):
    """A template's pkgrel= is a whole number, and that is where it is decided.

    Arch also allows `<int>.<int>`, and this repository does not use it: the
    shell's 包文件 reader refuses that name, so a template carrying it used to
    render clean, pass the entry check, and die in the container on a file name
    -- while the check's own contradiction refusal skipped its branch on the
    value it could not parse.
    """

    def template(self, pkg: str = "magpie-bin") -> str:
        return (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()

    def test_our_own_templates_carry_one(self):
        for pkg in bump.PACKAGES:
            with self.subTest(pkg=pkg):
                bump.check_pkgrel(pkg, self.template(pkg))

    def test_the_dotted_form_arch_allows_is_refused_by_name(self):
        drifted = self.template().replace("pkgrel=1", "pkgrel=1.1")
        self.assertIn("pkgrel=1.1", drifted, "the pkgrel line moved; reword this test")
        with self.assertRaises(bump.BumpError) as caught:
            bump.check_pkgrel("magpie-bin", drifted)
        self.assertIn("pkgrel=1.1", str(caught.exception))
        self.assertIn("whole number", str(caught.exception))

    def test_a_template_with_no_pkgrel_is_named(self):
        with self.assertRaises(bump.BumpError) as caught:
            bump.check_pkgrel("magpie-bin", "pkgname=magpie-bin\n")
        self.assertIn("no pkgrel=", str(caught.exception))

    def test_a_quoted_value_reads_the_way_the_renderer_writes_it(self):
        bump.check_pkgrel("magpie-bin", "pkgrel='2'\n")

    def test_a_digit_int_does_not_agree_with_is_refused(self):
        # `str.isdigit` says yes to `²` and to the full-width `１`; `int()` and
        # the shell's `pkgfile_pkgname` say no. Either one getting past this
        # door puts a pkgrel in the tree the container cannot name -- or makes
        # the reader raise ValueError where a BumpError was promised.
        for value in ("²", "１"):
            with self.subTest(value=value):
                with self.assertRaises(bump.BumpError) as caught:
                    bump.check_pkgrel("magpie-bin", f"pkgrel={value}\n")
                self.assertIn("whole number", str(caught.exception))

    def test_the_reader_refuses_what_it_cannot_read_as_a_number(self):
        self.assertEqual(bump.pkgrel_of("0.1.10-2"), 2)
        with self.assertRaises(bump.BumpError) as caught:
            bump.pkgrel_of("0.1.10-²")
        self.assertIn("0.1.10-²", str(caught.exception))

    def test_both_doors_read_it(self):
        # The entry check and the render both go through read_template, so one
        # call there is what makes the refusal reach the mode the pipeline runs.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            templates = root / "templates"
            templates.mkdir()
            for pkg in bump.PACKAGES:
                drifted = self.template(pkg).replace("pkgrel=1", "pkgrel=2.1")
                self.assertIn(
                    "pkgrel=2.1", drifted, "the pkgrel line moved; reword this test"
                )
                (templates / f"{pkg}.PKGBUILD.in").write_text(drifted)
            layout = layout_at(root, templates=templates)
            for name, call in (
                ("check", lambda: bump.check(layout=layout, fetch=FetchSpy({}))),
                ("render", lambda: bump.render("0.1.2", layout=layout, fetch=FetchSpy({}))),
            ):
                with self.subTest(door=name):
                    with nothing_declared(), self.assertRaises(bump.BumpError) as caught:
                        call()
                    self.assertIn("pkgrel=2.1", str(caught.exception))


class TemplateArchesTest(unittest.TestCase):
    """A template's arches are held to the table before anything is fetched.

    The table handed in is this class's own two-arch one, not the repository's:
    what is under test is the mechanism (a table with N arches versus a template
    with M), and coupling it to today's table would make every arch decision
    here look like fifteen broken tests. `test_our_own_templates_pass` holds the
    real files to the real table.
    """

    ARCHES = {"x86_64": "linux-amd64", "aarch64": "linux-arm64"}

    TEMPLATE = (
        "pkgname=magpie-bin\n"
        "pkgrel=1\n"
        "arch=('x86_64' 'aarch64')\n"
        "source_x86_64=('magpie-${pkgver}-amd64::https://example.invalid/magpie-linux-amd64')\n"
        "source_aarch64=('magpie-${pkgver}-arm64::https://example.invalid/magpie-linux-arm64')\n"
        "sha256sums_x86_64=('{{sha_linux-amd64}}')\n"
        "sha256sums_aarch64=('{{sha_linux-arm64}}')\n"
        "package() {\n"
        "    case \"$CARCH\" in\n"
        "        x86_64)  _suffix=amd64 ;;\n"
        "        aarch64) _suffix=arm64 ;;\n"
        "    esac\n"
        "}\n"
    )

    def check(self, template: str, arches: dict[str, str] | None = None) -> None:
        bump.read_template(
            pkg="magpie-bin",
            prefix="magpie",
            text=template,
            arches=self.ARCHES if arches is None else arches,
        )

    def test_a_template_that_agrees_with_the_table_passes(self):
        self.check(self.TEMPLATE)

    def test_an_arch_the_table_gained_names_the_template_that_lags(self):
        arches = {**self.ARCHES, "riscv64": "linux-riscv64"}
        with self.assertRaises(bump.BumpError) as caught:
            self.check(self.TEMPLATE, arches)
        self.assertIn("magpie-bin", str(caught.exception))
        self.assertIn("riscv64", str(caught.exception))

    def test_a_template_arch_the_table_never_heard_of_is_refused(self):
        template = self.TEMPLATE.replace("'aarch64')", "'aarch64' 'riscv64')")
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("riscv64", str(caught.exception))

    def test_a_missing_checksum_array_is_named(self):
        template = self.TEMPLATE.replace("sha256sums_aarch64=('{{sha_linux-arm64}}')\n", "")
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("sha256sums_aarch64", str(caught.exception))

    def test_a_missing_source_array_is_named(self):
        # This is the one the guard did not look at: the table gained an arch,
        # the template's source_<arch> array was forgotten, and the render
        # passed -- the build only failed later, inside the container.
        template = self.TEMPLATE.replace(
            "source_aarch64=('magpie-${pkgver}-arm64::https://example.invalid/magpie-linux-arm64')\n", ""
        )
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("source_aarch64", str(caught.exception))

    def test_a_source_url_for_another_arch_is_named(self):
        # The URL is the restatement nobody read: an aarch64 entry pointing at
        # the amd64 asset rendered clean, and the mistake only surfaced as a
        # checksum mismatch inside the build container.
        template = self.TEMPLATE.replace("magpie-linux-arm64", "magpie-linux-amd64")
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("source_aarch64", str(caught.exception))
        self.assertIn("linux-arm64", str(caught.exception))

    def test_a_source_file_name_for_another_arch_is_named(self):
        template = self.TEMPLATE.replace("-arm64::", "-amd64::")
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("source_aarch64", str(caught.exception))
        self.assertIn("amd64", str(caught.exception))

    def test_a_source_array_that_is_only_a_comment_is_named(self):
        # The guard used to be a substring test, so a template with the array
        # commented out still rendered.
        template = self.TEMPLATE.replace("source_aarch64=(", "# retired source_aarch64=(")
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("source_aarch64", str(caught.exception))

    def test_a_checksum_array_that_is_only_a_comment_is_named(self):
        # The fifth restatement was still read with `in template`, so this one
        # passed: source_aarch64 was live with no checksums for it, and the
        # failure landed on whoever built aarch64 -- which CI never does.
        template = self.TEMPLATE.replace("sha256sums_aarch64=(", "# retired sha256sums_aarch64=(")
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("sha256sums_aarch64", str(caught.exception))

    def test_the_real_template_is_read_past_its_first_line(self):
        # magpie-bin spreads source_x86_64 over several lines; the guard has to
        # read the array, not just find the key. The asset it is held to is the
        # one the 包清单 publishes, so a URL naming another arch's asset is
        # what this mutation puts there.
        text = (REPO / "templates" / "magpie-bin.PKGBUILD.in").read_text()
        mutated = text.replace("magpie-linux-amd64", "magpie-linux-arm64")
        self.assertNotEqual(mutated, text, "the fixture stopped changing anything")
        with self.assertRaises(bump.BumpError) as caught:
            bump.read_template(
                pkg="magpie-bin", prefix="magpie", text=mutated, arches=bump.ARCHES
            )
        self.assertIn("source_x86_64", str(caught.exception))

    def test_an_asset_name_the_inventory_never_publishes_is_named(self):
        # The gap this closed: the guard compared the URL's *tail*, and the
        # renderer took the asset name from the 包清单's prefix, so the two
        # halves never met. `magpieXY-linux-amd64` ends in `linux-amd64` like
        # the right asset does, so a tail test passed it and the render wrote
        # it down -- a 404 in the container, or nothing at all on an arch CI
        # never builds.
        template = self.TEMPLATE.replace("example.invalid/magpie-linux-amd64",
                                         "example.invalid/magpieXY-linux-amd64")
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("source_x86_64", str(caught.exception))
        self.assertIn("magpie-linux-amd64", str(caught.exception))

    def test_a_source_array_with_two_assets_for_one_arch_is_refused(self):
        # The 包清单 models one upstream prefix per package, so a second asset
        # per arch is a template this renderer cannot answer for -- not a value
        # to pick between.
        template = self.TEMPLATE.replace(
            "source_x86_64=('magpie-${pkgver}-amd64::https://example.invalid/magpie-linux-amd64')",
            "source_x86_64=('magpie-${pkgver}-amd64::https://example.invalid/magpie-linux-amd64'\n"
            "                'magpie-${pkgver}-amd64-extra::https://example.invalid/magpie-linux-amd64)')",
        )
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("one asset per arch", str(caught.exception))

    def test_a_missing_case_arm_is_named(self):
        template = self.TEMPLATE.replace("        aarch64) _suffix=arm64 ;;\n", "")
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("no case arm for aarch64", str(caught.exception))

    def test_a_case_arm_that_disagrees_with_the_table_is_named(self):
        template = self.TEMPLATE.replace("_suffix=arm64", "_suffix=amd64")
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("maps aarch64 to amd64", str(caught.exception))

    def test_a_case_arm_for_an_arch_the_table_never_heard_of_is_refused(self):
        template = self.TEMPLATE.replace(
            "        aarch64) _suffix=arm64 ;;\n",
            "        aarch64) _suffix=arm64 ;;\n        riscv64) _suffix=riscv64 ;;\n",
        )
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("riscv64", str(caught.exception))

    def test_our_own_templates_pass(self):
        # The synthetic template above is the shape; these are the files.
        for pkg, prefix in bump.PACKAGES.items():
            with self.subTest(pkg=pkg):
                text = (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()
                bump.read_template(
                    pkg=pkg, prefix=prefix, text=text, arches=bump.ARCHES
                )

    def test_a_missing_placeholder_is_named(self):
        template = self.TEMPLATE.replace("'{{sha_linux-arm64}}'", "'" + "0" * 64 + "'")
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("sha_linux-arm64", str(caught.exception))

    def test_render_checks_the_arches_before_the_network(self):
        # The table gaining an arch means this repository's template lags, not
        # that upstream's release is missing an asset -- so say so without
        # fetching anything. The table is handed in through the layout now; the
        # test no longer monkeypatches the module to say "an arch table with
        # riscv64 in it".
        arches = {**bump.ARCHES, "riscv64": "linux-riscv64"}
        with tempfile.TemporaryDirectory() as tmp:
            fetch = FetchSpy({})
            with self.assertRaises(bump.BumpError) as caught:
                bump.render(
                    "0.1.2", layout=layout_at(Path(tmp), arches=arches), fetch=fetch
                )
        self.assertIn("riscv64", str(caught.exception))
        self.assertEqual(fetch.urls, [])


class TemplateSumsTest(unittest.TestCase):
    """The package's own source= and sha256sums= arrays are read, and paired.

    The arch arrays were paired by the plan long before this; these two are the
    LICENSE and the icon, and no reader touched them. A third source with no
    checksum rendered clean and makepkg refused the file in the container.
    """

    def template(self, pkg: str = "magpie-cli-bin") -> str:
        return (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()

    def test_our_own_templates_pair_their_plain_arrays(self):
        for pkg in bump.PACKAGES:
            with self.subTest(pkg=pkg):
                bump.check_sums(pkg, self.template(pkg))

    def test_a_source_with_no_checksum_behind_it_is_named(self):
        text = self.template().replace(
            'source=("magpie-${pkgver}-LICENSE::https://raw.githubusercontent.com/yetone/magpie/v${pkgver}/LICENSE")',
            'source=("magpie-${pkgver}-LICENSE::https://raw.githubusercontent.com/yetone/magpie/v${pkgver}/LICENSE"\n'
            '        "extra-${pkgver}.bin::https://example.invalid/extra.bin")',
        )
        with self.assertRaises(bump.BumpError) as caught:
            bump.check_sums("magpie-cli-bin", text)
        self.assertIn("source= carries 2 entries", str(caught.exception))
        self.assertIn("sha256sums= carries 1", str(caught.exception))

    def test_a_checksum_for_a_file_nothing_downloads_is_named(self):
        text = self.template().replace(
            "sha256sums=('{{sha_license}}')",
            "sha256sums=('{{sha_license}}'\n            '{{sha_icon}}')",
        )
        with self.assertRaises(bump.BumpError) as caught:
            bump.check_sums("magpie-cli-bin", text)
        self.assertIn("sha256sums= carries 2", str(caught.exception))

    def test_a_one_line_array_is_counted_by_entry_not_by_line(self):
        # magpie-bin writes magpie-cli-bin's one-line array as two lines. A
        # reader that split on newlines would call the second shape "one
        # checksum" and pass a template that makepkg refuses.
        one_line = "sha256sums=('{{sha_license}}' '{{sha_icon}}')\n"
        stacked = "sha256sums=('{{sha_license}}'\n            '{{sha_icon}}')\n"
        self.assertEqual(bump.array_entries(one_line, "sha256sums"),
                         ["{{sha_license}}", "{{sha_icon}}"])
        self.assertEqual(bump.array_entries(stacked, "sha256sums"),
                         ["{{sha_license}}", "{{sha_icon}}"])

    def test_the_render_refuses_it_before_writing_anything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decoy = root / "templates"
            decoy.mkdir()
            for pkg in bump.PACKAGES:
                (decoy / f"{pkg}.PKGBUILD.in").write_text(self.template(pkg))
            text = self.template().replace(
                'source=("magpie-${pkgver}-LICENSE::https://raw.githubusercontent.com/yetone/magpie/v${pkgver}/LICENSE")',
                'source=("magpie-${pkgver}-LICENSE::https://raw.githubusercontent.com/yetone/magpie/v${pkgver}/LICENSE"\n'
                '        "extra-${pkgver}.bin::https://example.invalid/extra.bin")',
            )
            (decoy / "magpie-cli-bin.PKGBUILD.in").write_text(text)

            fetch = FetchSpy({})
            with self.assertRaises(bump.BumpError):
                bump.render(
                    "0.1.2", layout=layout_at(root, templates=decoy), fetch=fetch
                )
            # Before the network, and before the tree gains a half-rendered file.
            self.assertEqual(fetch.urls, [])
            self.assertFalse((root / "packages").exists())


if __name__ == "__main__":
    unittest.main()
