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
from unittest import mock

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
    """The stdout protocol: frozen for the workflow, warnings left free."""

    def lines(self, fetch, out_root) -> list[str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            bump.check(layout=layout_at(out_root), fetch=fetch)
        return buffer.getvalue().splitlines()

    def test_reports_every_package_that_is_behind(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.9", "magpie-cli-bin": "0.1.9"})
            self.assertEqual(
                self.lines(fetch, root),
                [
                    "upstream=0.1.10",
                    "packaged magpie-bin=0.1.9",
                    "packaged magpie-cli-bin=0.1.9",
                    "bump=1",
                    "behind=magpie-bin magpie-cli-bin",
                ],
            )

    def test_says_nothing_to_do_when_the_versions_match(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.10", "magpie-cli-bin": "0.1.10"})
            self.assertEqual(
                self.lines(fetch, root),
                [
                    "upstream=0.1.10",
                    "packaged magpie-bin=0.1.10",
                    "packaged magpie-cli-bin=0.1.10",
                    "bump=0",
                ],
            )

    def test_treats_a_missing_pkgbuild_as_behind(self):
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                self.lines(fetch, Path(tmp)),
                [
                    "upstream=0.1.10",
                    "warning: no rendered PKGBUILD yet",
                    "bump=1",
                    "behind=magpie-bin magpie-cli-bin",
                ],
            )

    def test_one_missing_pkgbuild_is_still_behind(self):
        # The other package is current, so only the absent one is behind; a
        # reader that walked the rendered packages would see nothing to do.
        fetch = FetchSpy(version_sources(github="0.1.10", site="0.1.10"))
        with tempfile.TemporaryDirectory() as tmp:
            root = staged_root(Path(tmp), {"magpie-bin": "0.1.10"})
            self.assertEqual(
                self.lines(fetch, root),
                [
                    "upstream=0.1.10",
                    "packaged magpie-bin=0.1.10",
                    "bump=1",
                    "behind=magpie-cli-bin",
                ],
            )


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

    def check(self, template: str) -> None:
        bump.check_arches("magpie-bin", template, bump.placeholders(template))

    def test_a_template_that_agrees_with_the_table_passes(self):
        self.check(self.TEMPLATE)

    def test_an_arch_the_table_gained_names_the_template_that_lags(self):
        with mock.patch.dict(bump.ARCHES, {"riscv64": "linux-riscv64"}):
            with self.assertRaises(bump.BumpError) as caught:
                self.check(self.TEMPLATE)
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
                bump.check_arches(pkg, text, bump.placeholders(text))

    def test_a_missing_placeholder_is_named(self):
        template = self.TEMPLATE.replace("'{{sha_linux-arm64}}'", "'" + "0" * 64 + "'")
        with self.assertRaises(bump.BumpError) as caught:
            self.check(template)
        self.assertIn("sha_linux-arm64", str(caught.exception))

    def test_render_checks_the_arches_before_the_network(self):
        # The table gaining an arch means this repository's template lags, not
        # that upstream's release is missing an asset -- so say so without
        # fetching anything.
        with mock.patch.dict(bump.ARCHES, {"riscv64": "linux-riscv64"}):
            with tempfile.TemporaryDirectory() as tmp:
                fetch = FetchSpy({})
                with self.assertRaises(bump.BumpError) as caught:
                    bump.render("0.1.2", layout=layout_at(Path(tmp)), fetch=fetch)
        self.assertIn("riscv64", str(caught.exception))
        self.assertEqual(fetch.urls, [])


if __name__ == "__main__":
    unittest.main()
