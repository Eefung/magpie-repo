"""Read scripts/check.sh as what it is: a gate table, its bodies, and one note.

One reader, for the reason tests/workflow.py is one. The table, the `gate_*`
bodies it names, the header comment above them and the blind-spot note a run
prints are four facts about one file, and they were read in two places with two
sets of regexes: tests/test_check_range.py cut the table out to hold it to the
bodies, and tests/test_docs.py cut the note out with its own ``cat <<'EOF'``
split to hold the names it mentions. Whichever of the two a change did not
touch went on passing.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CHECK = Path(__file__).resolve().parent.parent / "scripts" / "check.sh"

# Scoped to the table: `PINNED_TOOLS` above it is a table of rows too, and it
# names three of the same tools.
TABLE = re.compile(r"^GATE_ROWS=\(\n(.*?)^\)", re.M | re.S)
ROW = re.compile(r'^\s*"([a-z][a-z0-9_]*)\|([^"]*)"', re.M)
FUNCTION = re.compile(r"^gate_([a-z][a-z0-9_]*)\(\)", re.M)

# The note a run prints, and where the header comment ends.
NOTE = "cat <<'EOF'"
FIRST_EXECUTABLE = "set -uo pipefail"


@dataclass(frozen=True)
class Row:
    """One gate, as the table declares it: the slug names its function."""

    slug: str
    title: str


def text() -> str:
    return CHECK.read_text()


def table(source: str | None = None) -> str:
    """The gate table itself, so a row cannot be found in another table."""
    match = TABLE.search(text() if source is None else source)
    if match is None:
        raise AssertionError(f"no GATE_ROWS table in {CHECK}")
    return match.group(1)


def rows(source: str | None = None) -> list[Row]:
    """Every gate the table declares, in the order the loop runs them."""
    return [Row(slug, title) for slug, title in ROW.findall(table(source))]


def slugs(source: str | None = None) -> list[str]:
    """The slugs alone -- the order the summary counts rows in."""
    return [row.slug for row in rows(source)]


def functions(source: str | None = None) -> list[str]:
    """The slugs that have a body."""
    return FUNCTION.findall(text() if source is None else source)


def note(source: str | None = None) -> str:
    """The blind-spot list a run prints, as the heredoc it is written in."""
    after = (text() if source is None else source).split(NOTE, 1)[1]
    return after.split("\nEOF", 1)[0]


def header(source: str | None = None) -> str:
    """The comment block above the first executable line: prose, not a gate.

    Everything here is a claim about the gate set or about what a green run does
    not prove, and the note a run prints is the list that has a reader. A
    sentence in this block describing coverage is a second copy of that list,
    which is what `tests/test_docs.py` refuses.
    """
    return (text() if source is None else source).split(FIRST_EXECUTABLE, 1)[0]
