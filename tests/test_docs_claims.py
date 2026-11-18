"""The claims a document makes about this tree, and the ones that cannot land.

Driven only through the interface: a document in, the claims that do not
resolve out. Nothing here asks which pattern ran, because the question a
maintainer has is "does this sentence still point at something".

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import docs_claims  # noqa: E402  (the reader of what a document points at)


def spans(claims: list) -> list[str]:
    return [claim.span for claim in claims]


# One resolvable example per prefix. Held equal to the module's own list, so a
# prefix added there without a place to point at reds here rather than waiting
# for a document to trip on it.
EXAMPLES = {
    "scripts/": "scripts/build-check.sh",
    "tests/": "tests/test_bump.py",
    "templates/": "templates/magpie-bin.PKGBUILD.in",
    "packages/": "packages/magpie-bin",
    "docs/": "docs/adr/0001-publish-pipeline-stays-in-the-workflow.md",
    ".github/": ".github/workflows/publish.yml",
}


class PathClaimTest(unittest.TestCase):
    """A path claim is a span that starts with one of the repository's prefixes."""

    def test_the_examples_cover_every_prefix_the_module_reads(self):
        self.assertEqual(set(EXAMPLES), set(docs_claims.PREFIXES))

    def test_every_prefix_resolves(self):
        for prefix, span in EXAMPLES.items():
            with self.subTest(prefix=prefix):
                self.assertTrue(span.startswith(prefix), span)
                self.assertEqual(spans(docs_claims.unresolved(f"`{span}`", REPO)), [])

    def test_a_directory_is_a_place_the_documents_can_name(self):
        # All four are directories: a document saying "the ADRs live in
        # `docs/adr/`" points at something real.
        for span in ("scripts/", "templates/", "docs/adr/", "packages/"):
            with self.subTest(span=span):
                self.assertEqual(spans(docs_claims.unresolved(f"`{span}`", REPO)), [])

    def test_the_line_is_where_the_claim_is(self):
        document = "First line.\n\nSee `scripts/gone.py` for it.\n"
        self.assertEqual(
            [(claim.span, claim.kind, claim.line) for claim in docs_claims.unresolved(document, REPO)],
            [("scripts/gone.py", "path", 3)],
        )

    def test_a_template_that_is_gone_is_caught(self):
        self.assertEqual(
            spans(docs_claims.unresolved("`templates/gone.PKGBUILD.in`", REPO)),
            ["templates/gone.PKGBUILD.in"],
        )

    def test_an_adr_that_is_gone_is_caught(self):
        self.assertEqual(
            spans(docs_claims.unresolved("`docs/adr/0009-x.md`", REPO)), ["docs/adr/0009-x.md"]
        )

    def test_a_span_with_a_placeholder_describes_a_shape_not_a_path(self):
        # `templates/**` and `packages/<包名>/` are how a document describes a
        # family; nothing is missing when no file is literally called that.
        for span in ("templates/**", "packages/<包名>/", "scripts/**", "scripts/*.sh"):
            with self.subTest(span=span):
                self.assertEqual(spans(docs_claims.unresolved(f"`{span}`", REPO)), [])

    def test_a_path_that_is_not_this_repository_is_not_a_claim(self):
        # Paths inside a package or on the user's disk. Reading them as claims
        # would make every document about packaging red.
        for span in ("x86_64/", "usr/bin/", "src/", "Eefung/magpie-repo", "~/magpie-repo"):
            with self.subTest(span=span):
                self.assertEqual(docs_claims.claims(f"`{span}`"), [])

    def test_a_span_is_cut_at_its_first_word(self):
        # A document shows a command; the claim is the file it runs, not the
        # arguments after it.
        self.assertEqual(
            [claim.span for claim in docs_claims.claims("`scripts/bump.py --check`")],
            ["scripts/bump.py"],
        )

    def test_a_marker_in_an_argument_does_not_make_the_path_a_shape(self):
        # `scripts/bump.py <版本>` names a file and shows what to pass it; only
        # a marker inside the path itself (`scripts/*.sh`) is a shape. The
        # claim is asserted as a claim, not as an empty answer -- "no claim" and
        # "a claim that resolves" both come back empty from `unresolved`.
        self.assertEqual(
            [claim.span for claim in docs_claims.claims("`scripts/bump.py <版本>`")],
            ["scripts/bump.py"],
        )

    def test_a_tree_without_the_file_reports_it(self):
        # The reproduction this guard exists for: AGENTS.md points at a page
        # that was renamed, and every other test in this file still passes.
        document = (REPO / "AGENTS.md").read_text()
        self.assertIn("docs/agents/domain.md", document, "AGENTS.md moved; reword this test")
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "docs" / "agents").mkdir(parents=True)
            (tree / "docs" / "agents" / "domain-renamed.md").write_text("moved\n")
            reporting = spans(docs_claims.unresolved(document, tree))
        self.assertIn("docs/agents/domain.md", reporting)

    def test_a_staged_tree_reports_a_family_at_a_time(self):
        # Two prefixes, one tree: both are missing at first, then one is
        # supplied and only the other is left. Nothing here reads the real
        # repository, so a template or a page can be off the tree on purpose.
        document = (
            "`templates/magpie-bin.PKGBUILD.in` and "
            "`docs/adr/0003-only-ship-arches-we-build-and-check.md`\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            self.assertEqual(
                spans(docs_claims.unresolved(document, tree)),
                [
                    "templates/magpie-bin.PKGBUILD.in",
                    "docs/adr/0003-only-ship-arches-we-build-and-check.md",
                ],
            )
            (tree / "templates").mkdir()
            (tree / "templates" / "magpie-bin.PKGBUILD.in").write_text("x\n")
            self.assertEqual(
                spans(docs_claims.unresolved(document, tree)),
                ["docs/adr/0003-only-ship-arches-we-build-and-check.md"],
            )

    def test_the_repository_pages_claim_paths_that_all_resolve(self):
        # Not just "nothing is broken": a page whose claims came out empty
        # would pass that too, and an empty reading is a guard that stopped
        # reading (#24 makes the invariant general).
        for page in ("README.md", "AGENTS.md", "GLOSSARY.md"):
            with self.subTest(page=page):
                document = (REPO / page).read_text()
                self.assertTrue(docs_claims.claims(document), f"{page} made no claim")
                self.assertEqual(spans(docs_claims.unresolved(document, REPO)), [])


if __name__ == "__main__":
    unittest.main()
