"""The claims a document makes about this tree, and the ones that cannot land.

A claim is a backticked span, and its kind comes from the span's shape and
prefix -- never from whether it happens to resolve. That is the whole point of
this module: the guard it replaces asked "does this match one of my three
patterns", so a span it did not recognise passed in silence, and a page that
was renamed stayed green for as long as nobody looked.

`claims` and `unresolved` are the whole interface:

    claims(document)            the claims it makes, in reading order
    unresolved(document, repo)  the ones `repo` does not hold

One kind exists today, and each new one is a resolver here:

- `path` -- a span starting with one of `PREFIXES`. The claim is the first word
  (`scripts/bump.py --check` points at `scripts/bump.py`; the rest is the
  command line a reader is shown). A span whose *path* carries `*`, `<` or `>`
  describes a shape -- `templates/**`, `packages/<包名>/` -- and nothing exists to
  find; a marker in an argument (`scripts/bump.py <版本>`) leaves the path
  answerable. What
  is left must exist, as a file or a directory: `docs/adr/` is a real place to
  point at. A span whose first word is not a repository path (`x86_64/`,
  `usr/bin/`, `Eefung/magpie-repo`) is not a claim about this tree.

`unresolved` answers the only question a caller has -- which of these cannot
land -- and each answer carries its kind, span and line, so a failure says
which rule it used.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# The prefixes a document writes when it points inside this repository. The
# list is the repository's layout, not a list of exceptions: a directory added
# at the top level is a prefix the moment a document points into it.
PREFIXES = ("scripts/", "tests/", "templates/", "packages/", "docs/", ".github/")

# A claim is backticked and on one line; a span may not carry the closing
# backtick, so prose across a paragraph stays prose.
SPAN = re.compile(r"`([^`\n]+)`")


@dataclass(frozen=True)
class Claim:
    """One thing a document points at: where it is written, and what kind it is."""

    span: str
    kind: str
    line: int


def claims(document: str) -> list[Claim]:
    """The path claims one document makes, in reading order."""
    found: list[Claim] = []
    for number, text in enumerate(document.splitlines(), start=1):
        for span in SPAN.findall(text):
            words = span.split()
            path = words[0] if words else ""
            if not path.startswith(PREFIXES):
                continue
            # A shape marker in the path itself is what makes the span a shape;
            # one in an argument (`scripts/bump.py <版本>`) leaves the path
            # answerable.
            if any(marker in path for marker in "*<>"):
                continue
            found.append(Claim(path, "path", number))
    return found


def unresolved(document: str, repo: Path) -> list[Claim]:
    """The claims in `document` that `repo` does not hold."""
    return [claim for claim in claims(document) if not (repo / claim.span).exists()]
