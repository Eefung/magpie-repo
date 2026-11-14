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
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Mapping

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import bump  # noqa: E402  (the module lives beside the scripts, not on the path)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
    return f"{bump.SOURCE_REPO}/v{version}/LICENSE"


def version_sources(github: str | None, site: str | None) -> dict[str, bytes | None]:
    """A fetch table for the two version sources; None means "that one 404s"."""
    table: dict[str, bytes | None] = {}
    if github is not None:
        table[bump.GITHUB_LATEST] = json.dumps({"tag_name": f"v{github}"}).encode()
    if site is not None:
        table[bump.SITE_API] = json.dumps({"version": site}).encode()
    return table


def sums_body(entries: dict[str, str]) -> bytes:
    return "".join(f"{digest}  {name}\n" for name, digest in entries.items()).encode()


def staged_root(root: Path, versions: dict[str, str]) -> Path:
    """A fake checkout whose packages/<pkg>/PKGBUILD carry these pkgvers."""
    for pkg, version in versions.items():
        path = root / "packages" / pkg / "PKGBUILD"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"pkgname={pkg}\npkgver={version}\npkgrel=1\n")
    return root


def layout_at(
    root: Path,
    *,
    templates: Path | None = None,
    inventory: dict[str, str] | None = None,
    arches: dict[str, str] | None = None,
) -> bump.Layout:
    """A layout pointing the renderer at a temporary tree.

    Only the output directory moves: the templates and the inventory are the
    repository's own unless a test overrides them. This goes through the
    production constructor now, so the defaults under test are the ones that
    ship.
    """
    return bump.layout_for(
        REPO,
        templates=templates,
        packages_dir=root / "packages",
        inventory=inventory,
        arches=arches,
        maintainer="Test Maintainer <test@example.invalid>",
    )


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


class HighestTest(unittest.TestCase):
    def test_compares_parts_as_numbers_not_text(self):
        self.assertEqual(bump.highest(["0.1.999", "0.1.1000"]), "0.1.1000")

    def test_drops_blank_and_prerelease_entries(self):
        self.assertEqual(bump.highest(["", "0.1.4-beta", "0.1.3"]), "0.1.3")

    def test_names_the_junk_when_nothing_is_usable(self):
        with self.assertRaises(bump.BumpError) as caught:
            bump.highest(["0.1.4-beta", "latest"])
        self.assertIn("0.1.4-beta", str(caught.exception))


class LatestVersionTest(unittest.TestCase):
    def test_site_ahead_of_github_wins(self):
        fetch = FetchSpy(version_sources(github="0.1.9", site="0.1.10"))
        self.assertEqual(bump.latest_version(fetch=fetch), "0.1.10")

    def test_github_ahead_of_site_wins(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.9"))
        self.assertEqual(bump.latest_version(fetch=fetch), "0.1.10")

    def test_github_agrees_with_site(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        self.assertEqual(bump.latest_version(fetch=fetch), "0.1.10")

    def test_github_rate_limit_falls_back_to_the_site(self):
        fetch = FetchSpy(
            {
                bump.GITHUB_LATEST: urllib.error.HTTPError(bump.GITHUB_LATEST, 403, "rate limited", Message(), None),
                bump.SITE_API: json.dumps({"version": "0.1.9"}).encode(),
            }
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bump.latest_version(fetch=fetch), "0.1.9")

    def test_connection_error_falls_back_to_the_site(self):
        fetch = FetchSpy(
            {
                bump.GITHUB_LATEST: urllib.error.URLError("no route to host"),
                bump.SITE_API: json.dumps({"version": "0.1.9"}).encode(),
            }
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bump.latest_version(fetch=fetch), "0.1.9")

    def test_no_answer_from_anywhere_is_one_line(self):
        fetch = FetchSpy({bump.GITHUB_LATEST: None, bump.SITE_API: None})
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(bump.BumpError) as caught:
                bump.latest_version(fetch=fetch)
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
            bump.ICON_URL: icon_bytes,
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
        self.assertIn(self.ASSETS["magpie-cli-linux-arm64"], self.pkgbuild("magpie-cli-bin"))

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

        self.assertNotIn(bump.ICON_URL, fetch.urls)
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

    def test_a_missing_license_stops_before_writing_anything(self):
        fetch = FetchSpy(self.table(license_bytes=None))
        with self.assertRaises(bump.BumpError) as caught:
            bump.render(self.VERSION, layout=self.layout, fetch=fetch)
        self.assertIn("no LICENSE", str(caught.exception))
        self.assertFalse((self.out_root / "packages").exists())

    def test_a_missing_arch_asset_names_the_package(self):
        assets = {k: v for k, v in self.ASSETS.items() if k != "magpie-linux-arm64"}
        fetch = FetchSpy(self.table(assets=assets))
        with self.assertRaises(bump.BumpError) as caught:
            bump.render(self.VERSION, layout=self.layout, fetch=fetch)
        self.assertIn("magpie-bin: the release has no magpie-linux-arm64", str(caught.exception))
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


class PackagedVersionsTest(unittest.TestCase):
    def test_reads_pkgver_from_each_package_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.2", "magpie-cli-bin": "0.1.1"})
            self.assertEqual(
                bump.packaged_versions(layout_at(root)),
                {"magpie-bin": "0.1.2", "magpie-cli-bin": "0.1.1"},
            )

    def test_skips_packages_that_were_never_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.2"})
            self.assertEqual(bump.packaged_versions(layout_at(root)), {"magpie-bin": "0.1.2"})


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
        """The log's meaning: what was compared, and what it concluded."""
        parsed = {
            "upstream": None,
            "packaged": {},
            "bump": None,
            "behind": set(),
            "warnings": [],
        }
        for line in self.lines(fetch, out_root):
            if line.startswith("upstream="):
                parsed["upstream"] = line.removeprefix("upstream=")
            elif line.startswith("packaged "):
                pkg, _, ver = line.removeprefix("packaged ").partition("=")
                parsed["packaged"][pkg] = ver
            elif line.startswith("bump="):
                parsed["bump"] = line.removeprefix("bump=")
            elif line.startswith("behind="):
                parsed["behind"] = set(line.removeprefix("behind=").split())
            elif line.startswith("warning:"):
                parsed["warnings"].append(line)
        return parsed

    def test_reports_every_package_that_is_behind(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.9", "magpie-cli-bin": "0.1.9"})
            log = self.log_of(fetch, root)
        self.assertEqual(log["upstream"], "0.1.10")
        self.assertEqual(log["packaged"], {"magpie-bin": "0.1.9", "magpie-cli-bin": "0.1.9"})
        self.assertEqual(log["bump"], "1")
        self.assertEqual(log["behind"], {"magpie-bin", "magpie-cli-bin"})

    def test_says_nothing_to_do_when_the_versions_match(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
            log = self.log_of(fetch, root)
        self.assertEqual(log["bump"], "0")
        self.assertEqual(log["behind"], set())
        self.assertEqual(
            log["packaged"], {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"}
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
        self.assertEqual(log["behind"], {"magpie-bin", "magpie-cli-bin"})

    def test_one_missing_pkgbuild_is_still_behind(self):
        # The other package is current, so only the absent one is behind; a
        # reader that walked the rendered packages would see nothing to do.
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.10"})
            log = self.log_of(fetch, root)
        self.assertEqual(log["packaged"], {"magpie-bin": "0.1.10"})
        self.assertEqual(log["bump"], "1")
        self.assertEqual(log["behind"], {"magpie-cli-bin"})


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
            bump.ICON_URL: b"icon",
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
    """A template's arches are held to the table before anything is fetched."""

    TEMPLATE = (
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
        bump.check_arches("magpie-bin", template, bump.ARCHES if arches is None else arches)

    def test_a_template_that_agrees_with_the_table_passes(self):
        self.check(self.TEMPLATE)

    def test_an_arch_the_table_gained_names_the_template_that_lags(self):
        arches = {**bump.ARCHES, "riscv64": "linux-riscv64"}
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

    def test_the_real_template_is_read_past_its_first_line(self):
        # magpie-bin spreads source_x86_64 over several lines; the guard has to
        # read the array, not just find the key.
        text = (REPO / "templates" / "magpie-bin.PKGBUILD.in").read_text()
        mutated = text.replace("magpie-linux-arm64", "magpie-linux-amd64")
        with self.assertRaises(bump.BumpError) as caught:
            bump.check_arches("magpie-bin", mutated, bump.ARCHES)
        self.assertIn("source_aarch64", str(caught.exception))

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
        for pkg in bump.PACKAGES:
            with self.subTest(pkg=pkg):
                text = (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()
                bump.check_arches(pkg, text, bump.ARCHES)

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


if __name__ == "__main__":
    unittest.main()
