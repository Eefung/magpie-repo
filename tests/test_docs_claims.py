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
        # reading (#24 makes the invariant general). Asked as the path
        # question, because these pages also name test-side helpers.
        for page in ("README.md", "AGENTS.md", "GLOSSARY.md"):
            with self.subTest(page=page):
                document = (REPO / page).read_text()
                self.assertTrue(docs_claims.claims(document), f"{page} made no claim")
                self.assertEqual(spans(docs_claims.unresolved_paths(document, REPO)), [])

    def test_a_path_claim_is_asked_apart_from_the_other_kinds(self):
        # README and AGENTS name test-side helpers (`pkgfile_pkgname` lives in
        # `tests/`), and the name resolver reads `scripts/` only -- so a
        # document that is fed for its paths is asked for its paths.
        document = "`test_helper_fn` and `scripts/build-check.sh`"
        self.assertEqual(spans(docs_claims.unresolved_paths(document, REPO)), [])
        self.assertEqual(spans(docs_claims.unresolved(document, REPO)), ["test_helper_fn"])


class ModuleClaimTest(unittest.TestCase):
    """A module claim is a snake_case name the production code still carries."""

    def test_a_name_the_code_has_is_not_reported(self):
        self.assertEqual(spans(docs_claims.unresolved("`read_template`", REPO)), [])
        self.assertEqual(spans(docs_claims.unresolved("`frozen_var`", REPO)), [])

    def test_a_name_the_code_lost_is_reported(self):
        self.assertEqual(spans(docs_claims.unresolved("`gone_function`", REPO)), ["gone_function"])

    def test_a_bare_name_is_not_a_claim(self):
        # One spelling for a claim: backticks. A class name in prose is prose,
        # which is why #22 put them around the note's one -- a bare name is
        # invisible here by decision, not by accident.
        self.assertEqual(docs_claims.claims("RootAdapterTest drives the root path"), [])

    def test_the_kind_is_named_on_the_claim(self):
        self.assertEqual(
            [(claim.span, claim.kind) for claim in docs_claims.claims("`read_template`")],
            [("read_template", "module")],
        )

    def test_a_comment_does_not_keep_a_name_alive(self):
        # Facts are code lines: a name kept in a comment about a deleted
        # function is exactly the case this guard exists for.
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "scripts").mkdir()
            (tree / "scripts" / "a.sh").write_text(
                "real_fn() { :; }\n# ghost_fn used to live here\n"
            )
            self.assertEqual(spans(docs_claims.unresolved("`real_fn`", tree)), [])
            self.assertEqual(spans(docs_claims.unresolved("`ghost_fn`", tree)), ["ghost_fn"])

    def test_a_trailing_comment_does_not_keep_a_name_alive(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "scripts").mkdir()
            (tree / "scripts" / "x.sh").write_text("echo 1  # ghost_name\n")
            self.assertEqual(spans(docs_claims.unresolved("`ghost_name`", tree)), ["ghost_name"])


class CallClaimTest(unittest.TestCase):
    """A `name()` claim names a function, and the tree's own definition answers it."""

    def test_a_call_the_scripts_define_is_not_reported(self):
        self.assertEqual(spans(docs_claims.unresolved("`plan()`", REPO)), [])

    def test_a_call_the_tree_defines_is_not_reported(self):
        # `package()` is defined by the templates *and* by the probe script, so
        # the real tree cannot say which one answered: the templates half is
        # proved on a staged tree, in the case below.
        self.assertEqual(spans(docs_claims.unresolved("`package()`", REPO)), [])

    def test_a_staged_tree_with_the_template_resolves_the_call(self):
        document = "`package()`"
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            self.assertEqual(spans(docs_claims.unresolved(document, tree)), ["package()"])
            (tree / "templates").mkdir()
            (tree / "templates" / "x.PKGBUILD.in").write_text("package() {\n  :\n}\n")
            self.assertEqual(spans(docs_claims.unresolved(document, tree)), [])

    def test_a_call_nothing_defines_is_reported(self):
        self.assertEqual(spans(docs_claims.unresolved("`planx()`", REPO)), ["planx()"])

    def test_a_tree_without_the_definition_reports_the_call(self):
        # The other half of the rule: not "the name happens to be missing", but
        # "the definition is gone".
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "scripts").mkdir()
            (tree / "scripts" / "bump.py").write_text("def render() -> None:\n    pass\n")
            self.assertEqual(spans(docs_claims.unresolved("`render()`", tree)), [])
            self.assertEqual(spans(docs_claims.unresolved("`plan()`", tree)), ["plan()"])


class TestClassClaimTest(unittest.TestCase):
    """A test class claim names a class a test module defines."""

    def test_a_class_the_suite_defines_is_not_reported(self):
        self.assertEqual(spans(docs_claims.unresolved("`RootAdapterTest`", REPO)), [])

    def test_a_class_the_suite_does_not_define_is_reported(self):
        self.assertEqual(spans(docs_claims.unresolved("`GoneTest`", REPO)), ["GoneTest"])





if __name__ == "__main__":
    unittest.main()
