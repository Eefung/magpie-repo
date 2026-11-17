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
    return bump.required_assets(bump.plan(bump.layout_for(REPO)))


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


def layout_at(
    root: Path,
    *,
    templates: Path | None = None,
    inventory: dict[str, str] | None = None,
    arches: dict[str, str] | None = None,
    served: bump.Served | None = None,
) -> bump.Layout:
    """A layout pointing the renderer at a temporary tree.

    Only the output directory moves: the templates and the inventory are the
    repository's own unless a test overrides them -- and unless the tree staged
    its own `templates/`, which is what `layout_for` would use for that root.
    That last clause is the one that matters: `staged_root` renders a PKGBUILD
    from the templates it was handed, and this function builds the layout the
    code under test reads, so a caller that names a tree to one and not the
    other points two fixtures at two trees. The failure does not look like a
    fixture bug -- `values_of` reports the committed file as "not its
    template's render", which is the drift message.

    This goes through the production constructor, so the defaults under test
    are the ones that ship. `served` is None unless a test is about the
    comparison against what the 软件源 carries, which is also what a hand run
    without the three names gets.
    """
    if templates is None:
        staged = root / "templates"
        templates = staged if staged.is_dir() else None
    return bump.layout_for(
        REPO,
        templates=templates,
        packages_dir=root / "packages",
        inventory=inventory,
        arches=arches,
        maintainer="Test Maintainer <test@example.invalid>",
        served=served,
    )


def served_at(url: str = "https://example.invalid/repo") -> bump.Served:
    """The address a test's fake 软件源 is served at."""
    return bump.Served(url=url, section="magpie", arch="x86_64")


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
        with contextlib.redirect_stdout(buffer):
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
            with self.assertRaises(bump.BumpError) as caught:
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

                    with self.assertRaises(bump.BumpError) as caught:
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
            with self.assertRaises(bump.BumpError) as caught:
                bump.check(layout=layout_at(Path(tmp), templates=templates), fetch=fetch)
        self.assertIn("magpie-cli-bin", str(caught.exception))
        self.assertEqual(fetch.urls, [])


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
            with self.assertRaises(bump.BumpError) as caught:
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
        with contextlib.redirect_stdout(buffer):
            bump.check(
                layout=layout_at(root, templates=templates, served=served_at()),
                fetch=fetch,
            )
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

    def test_a_layout_without_an_address_never_asks_for_the_database(self):
        root = staged_root(self.tmp, {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with contextlib.redirect_stdout(io.StringIO()):
            bump.check(layout=layout_at(root), fetch=fetch)
        self.assertIn(f"{bump.RELEASES}/download/v0.1.10/SHA256SUMS", fetch.urls)
        self.assertEqual([url for url in fetch.urls if url.endswith(".db.tar.gz")], [])


class ServedFromEnvTest(unittest.TestCase):
    """The three names a run declares, and what their absence means."""

    def test_all_three_make_the_address_and_its_database(self):
        env = {"PAGES_URL": "https://h.invalid/r", "REPO_NAME": "magpie", "ARCH": "x86_64"}
        with mock.patch.dict(os.environ, env, clear=True):
            served = bump.served_from_env()
        self.assertEqual(served, bump.Served(url="https://h.invalid/r", section="magpie", arch="x86_64"))
        self.assertEqual(served.database, "https://h.invalid/r/x86_64/magpie.db.tar.gz")

    def test_a_missing_name_declares_no_address(self):
        for missing in ("PAGES_URL", "REPO_NAME", "ARCH"):
            with self.subTest(missing=missing):
                env = {"PAGES_URL": "https://h.invalid/r", "REPO_NAME": "magpie", "ARCH": "x86_64"}
                env.pop(missing)
                with mock.patch.dict(os.environ, env, clear=True):
                    self.assertIsNone(bump.served_from_env())


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
        return {
            sums_url(version): sums_body(
                {
                    "magpie-linux-amd64": "1" * 64,
                    "magpie-linux-arm64": "2" * 64,
                    "magpie-cli-linux-amd64": "3" * 64,
                    "magpie-cli-linux-arm64": "4" * 64,
                }
            ),
            license_url(version): b"license",
            ICON_URL: b"icon",
        }

    def cli(self, *argv, fetch) -> tuple[object, str]:
        """Run the entry point; return its exit code and what it printed."""
        buffer = io.StringIO()
        code: object = 0
        with contextlib.redirect_stdout(buffer):
            try:
                code = bump.main(list(argv), fetch=fetch, layout=self.layout)
            except SystemExit as exc:
                code = exc.code
        return code, buffer.getvalue()

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
