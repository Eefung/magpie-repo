"""The claims a document makes about this tree, and the ones that cannot land.

A claim is a backticked span, and its kind comes from the span's shape and
prefix -- never from whether it happens to resolve. That is the whole point of
this module: the guard it replaces asked "does this match one of my three
patterns", so a span it did not recognise passed in silence, and a page that
was renamed stayed green for as long as nobody looked.

    claims(document)            the claims it makes, in reading order
    unresolved(document, repo)  the ones `repo` does not hold
    unresolved_paths(...)       the same question for the path kind alone

A document that makes no claim of the kind it is asked about is refused, not
answered: both questions raise `SilentDocumentError`. An empty list from a
document with nothing to read is the third way a guard fails -- it read nothing
and said so in green -- and a document cannot be silent by accident when the
question it is asked is written down.

Three kinds, each a resolver here:

- `path` -- a span starting with one of `PREFIXES`. The claim is the first word
  (`scripts/bump.py --check` points at `scripts/bump.py`; the rest is the
  command line a reader is shown). A span whose *path* carries `*`, `<` or `>`
  describes a shape -- `templates/**`, `packages/<包名>/` -- and nothing exists to
  find; a marker in an argument (`scripts/bump.py <版本>`) leaves the path
  answerable. What is left must exist, as a file or a directory: `docs/adr/` is
  a real place to point at.
- `module` -- `a_b`, or `name()`. The first has to appear in the production
  code's live lines; the second has to be defined there, or in `templates/`
  (`package()` is defined by the PKGBUILD templates as well as by the probe
  script, and a glossary naming it is naming something real).
- `test class` -- `...Test`, `...Reader`, `...Error`, defined by a `class` line
  in `tests/`.

Everything else is not a claim about this tree: a span whose first word is not
a repository path (`x86_64/`, `usr/bin/`, `Eefung/magpie-repo`), a command
(`pacman -S magpie`), an env name (`GITHUB_OUTPUT`), a config key (`provides`).

The shape cannot tell an arch from a function: `x86_64` is `a_b` shaped and the
string is in the code, so it is a module claim that lands. That is the answer
this kind gives -- "the name is still in the code" -- not a claim about being a
function.

The production code is `scripts/` only, and a comment is not code: a name kept
alive in a comment about a deleted function is the case this guard exists for.
Test-side helpers are deliberately out of that scope -- including `tests/`
would let a fixture satisfy the claim it was written to falsify. That is why
`unresolved_paths` is its own question: README and AGENTS name test-side
helpers, so they are read for their paths, and the name kinds are asked of the
two documents that talk about the pipeline.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import live

# The prefixes a document writes when it points inside this repository. The
# list is the repository's layout, not a list of exceptions: a directory added
# at the top level is a prefix the moment a document points into it.
PREFIXES = ("scripts/", "tests/", "templates/", "packages/", "docs/", ".github/")

# A claim is backticked and on one line; a span may not carry the closing
# backtick, so prose across a paragraph stays prose.
SPAN = re.compile(r"`([^`\n]+)`")

# A module name, as the docs write it: `read_template`, `line_field`.
NAME = re.compile(r"^[a-z][a-z0-9]*_[a-z0-9_]+$")

# A function, as a document names one: `plan()`, `package()`.
CALL = re.compile(r"^([a-z][a-z0-9_.-]*)\(\)$")

# A test class, as a claim writes one and as bare prose names one. The two
# spellings are one shape: the anchored one decides a claim, the loose one
# answers "does this text name a class at all" (a gate's header must not).
_TEST_CLASS = re.compile(r"^[A-Z][A-Za-z]*(?:Test|Reader|Error)$")
CLASS_WORD = re.compile(r"\b[A-Z][A-Za-z]*(?:Test|Reader|Error)\b")


@dataclass(frozen=True)
class Claim:
    """One thing a document points at: where it is written, and what kind it is."""

    span: str
    kind: str
    line: int


def claims(document: str) -> list[Claim]:
    """The claims one document makes, in reading order."""
    found: list[Claim] = []
    for number, text in enumerate(document.splitlines(), start=1):
        for span in SPAN.findall(text):
            kind_span = _classify(span)
            if kind_span is not None:
                found.append(Claim(kind_span[0], kind_span[1], number))
    return found


class SilentDocumentError(AssertionError):
    """A document that made no claim of the kind it was asked about.

    An `AssertionError` so an unguarded caller reports it as a failure, and
    named `...Error` so the test-class kind in this module sees it like any
    other class of this suite.
    """


def unresolved(document: str, repo: Path) -> list[Claim]:
    """The claims in `document` that `repo` does not hold.

    Raises `SilentDocumentError` when the document makes no claim at all.
    """
    return [claim for claim in _required(document, None) if not _lands(claim, repo)]


def unresolved_paths(document: str, repo: Path) -> list[Claim]:
    """The path claims `repo` does not hold: the question a page is asked.

    README and AGENTS name test-side helpers, which the module kind does not
    read, so those pages are asked for their paths and the name kinds are asked
    of the two documents that talk about the pipeline. Raises
    `SilentDocumentError` when the document makes no path claim, which is not
    the same silence: a page that names only test-side helpers is not a page
    the path guard read.
    """
    return [claim for claim in _required(document, "path") if not _lands(claim, repo)]


def _required(document: str, kind: str | None) -> list[Claim]:
    """The claims a question needs, or a refusal if the document offers none.

    `kind` of `None` means every kind: that is the whole-document question.
    """
    found = [claim for claim in claims(document) if kind is None or claim.kind == kind]
    if not found:
        asked = "claim" if kind is None else f"{kind} claim"
        raise SilentDocumentError(f"this document makes no {asked}: the guard would read nothing")
    return found


def _classify(span: str) -> tuple[str, str] | None:
    """What kind of claim this span is, and the part of it that is the claim."""
    words = span.split()
    path = words[0] if words else ""
    if path.startswith(PREFIXES):
        # A shape marker in the path itself is what makes the span a shape; one
        # in an argument (`scripts/bump.py <版本>`) leaves the path answerable.
        if any(marker in path for marker in "*<>"):
            return None
        return path, "path"
    if _TEST_CLASS.match(span):
        return span, "test class"
    if NAME.match(span) or CALL.match(span):
        return span, "module"
    return None


def _lands(claim: Claim, repo: Path) -> bool:
    if claim.kind == "path":
        return (repo / claim.span).exists()
    if claim.kind == "test class":
        return re.search(rf"^class {re.escape(claim.span)}\b", _test_code(repo), re.M) is not None
    call = CALL.match(claim.span)
    if call:
        return _defines(repo, call.group(1))
    return re.search(rf"\b{re.escape(claim.span)}\b", _script_code(repo)) is not None


def _sources(repo: Path) -> list[Path]:
    """The production tree: the shell and python that decide something."""
    return [*repo.glob("scripts/**/*.sh"), *repo.glob("scripts/**/*.py")]


def _script_code(repo: Path) -> str:
    """The production tree's live lines: a module name has to appear in these.

    `scripts/` only, comments dropped: a doc names what *decides* something -- a
    module in the pipeline -- so a name kept alive in a comment about a deleted
    function is the case that must not pass. `tests/` is out: a fixture there
    would satisfy the claim the guard exists to falsify.
    """
    return "\n".join(line for path in _sources(repo) for line in live.live_lines(path.read_text()))


def _test_code(repo: Path) -> str:
    """The test tree's live lines: the only place a test class is defined."""
    return "\n".join(
        line
        for path in sorted((repo / "tests").glob("*.py"))
        for line in live.live_lines(path.read_text())
    )


def _defines(repo: Path, name: str) -> bool:
    """Whether the production tree defines `name` as a function.

    `scripts/` and `templates/`: a PKGBUILD template defines `package()`, and a
    glossary naming it is naming something real. Read through `live`, so a
    definition someone commented out stops counting.
    """
    pattern = re.compile(rf"^\s*(?:def {re.escape(name)}\(|{re.escape(name)}\(\)\s*\{{)", re.M)
    sources = [*_sources(repo), *(path for path in repo.glob("templates/**/*") if path.is_file())]
    return any(
        pattern.search("\n".join(live.live_lines(path.read_text()))) for path in sources
    )
