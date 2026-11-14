"""A committed PKGBUILD must still be its template with values filled in.

`--check` answers exactly one question -- is the packaged 版本 behind 上游 --
and that is by design (GLOSSARY: 落后). Nothing asserted the other edge of the
render: that `packages/<pkg>/PKGBUILD` is still `templates/<pkg>.PKGBUILD.in`
with values substituted.

Without it a template edit triggers a publish run through `publish.yml`'s path
filter, finds `bump=0`, prints "nothing to publish" -- and the edit ships later,
invisibly bundled with the next 版本. The guarantee lived in the reader's
memory instead of in an assertion.

The values are read back out of the committed file rather than pinned here on
purpose: this test locks the template's literal text, not whether the 版本 is
current. Pinning `pkgver` would mean editing this file on every bump.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import bump  # noqa: E402  (the module lives beside the scripts, not on the path)

SHA256_RE = re.compile(r"[0-9a-f]{64}")


class TemplateMismatch(AssertionError):
    """The committed file is not this template with values substituted."""


def values_of(template: str, committed: str) -> dict[str, str]:
    """The values `committed` supplies for this template's placeholders.

    Turns the template into a capture-group regex, so the parts that must match
    exactly are its literal text and the parts that may vary are the
    placeholders. Raises TemplateMismatch when the literals no longer agree or
    one placeholder was filled two different ways.
    """
    pattern = ""
    names: list[str] = []
    cursor = 0
    for match in bump.PLACEHOLDER_RE.finditer(template):
        pattern += re.escape(template[cursor:match.start()]) + "(.*?)"
        names.append(match.group(1))
        cursor = match.end()
    pattern += re.escape(template[cursor:])

    found = re.fullmatch(pattern, committed, re.S)
    if found is None:
        raise TemplateMismatch(
            "the committed PKGBUILD no longer matches its template; "
            "re-render it (scripts/bump.py --render) or revert the template edit"
        )

    values: dict[str, str] = {}
    for name, value in zip(names, found.groups()):
        if values.setdefault(name, value) != value:
            raise TemplateMismatch(f"{{{{{name}}}}} was filled two different ways")
    return values


class CommittedPkgbuildTest(unittest.TestCase):
    def pkgbuild(self, pkg: str) -> str:
        return (REPO / "packages" / pkg / "PKGBUILD").read_text()

    def template(self, pkg: str) -> str:
        return (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()

    def test_each_committed_pkgbuild_is_its_template_with_values_filled_in(self):
        for pkg in bump.PACKAGES:
            with self.subTest(pkg=pkg):
                values = values_of(self.template(pkg), self.pkgbuild(pkg))

                self.assertEqual(
                    set(values), set(bump.placeholders(self.template(pkg)))
                )
                self.assertRegex(values["pkgver"], bump.VERSION_RE)
                self.assertNotIn("{{", "".join(values.values()))
                self.assertIn("maintainer", values)
                for name, value in values.items():
                    if name.startswith("sha_"):
                        self.assertRegex(value, SHA256_RE, f"{pkg}: {name}")

    def test_both_packages_carry_the_same_version(self):
        # They are rendered in one pass from one 版本; a tree where they differ
        # would publish two packages built from different upstream releases.
        versions = {
            pkg: values_of(self.template(pkg), self.pkgbuild(pkg))["pkgver"]
            for pkg in bump.PACKAGES
        }
        self.assertEqual(len(set(versions.values())), 1, versions)

    def test_a_changed_template_stops_matching(self):
        # The guard has to bite, or it is decoration. Editing a template in
        # memory stands in for the edit that would land without a re-render.
        pkg = "magpie-cli-bin"
        edited = self.template(pkg).replace("pkgrel=1", "pkgrel=2")
        self.assertNotEqual(edited, self.template(pkg))
        with self.assertRaises(TemplateMismatch):
            values_of(edited, self.pkgbuild(pkg))

    def test_one_placeholder_filled_two_ways_is_caught(self):
        # Today's templates repeat no placeholder (the source lines use shell
        # ${pkgver}, not {{pkgver}}), so this rule has nothing to bite on in
        # the files above. It is what stops a template that does repeat one
        # from being edited apart in a single place.
        with self.assertRaises(TemplateMismatch) as caught:
            values_of("a={{x}}\nb={{x}}\n", "a=1\nb=2\n")
        self.assertIn("x", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
