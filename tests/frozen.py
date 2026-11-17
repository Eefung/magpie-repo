"""Read a producer's log the way the pipeline reads it.

`scripts/lib/pkg.sh`'s `frozen_var` / `frozen_list` / `frozen_write` are the
gates the pipeline reads and writes the frozen `name=value` lines with. A test
that splits them itself is a second reader with its own rules -- `test_bump`'s
`log_of` used to accept a duplicated `bump=` (taking the last one) where the
pipeline's reader fails the step, so the test job stayed green on a log the
workflow would have died on.

This runs the same gates for the keys the table names -- 域, shape, path and
absence are the pipeline's answers, in its words -- and falls back to
`line_field_or` for a name the table does not name (`pinned=`, `carried=` are
log lines, with no rule to apply).

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import functools
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib" / "pkg.sh"

# A frozen line's name: the key a program writes once and reads back.
LINE_NAME_RE = re.compile(r"^([a-z][a-z0-9_]*)=", re.M)

# The kinds table's columns, in the order pkg.sh writes them.
COLUMNS = ("key", "domain", "shape", "required", "producer")


@functools.lru_cache(maxsize=1)
def table() -> list[dict[str, str]]:
    """The kinds table, one dict per row.

    The test-side reader of the one place a key's 域, shape and producer are
    written. A guard that re-spells the columns here reads the same fact twice.
    """
    result = subprocess.run(
        ["bash", "-c", f"set -euo pipefail; source {shlex.quote(str(LIB))}; frozen_table"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"frozen_table: {result.stderr.strip()}")
    return [dict(zip(COLUMNS, line.split("|"))) for line in result.stdout.strip().splitlines()]

# A default no producer could write, so "absent" stays distinguishable from "".
ABSENT = "__absent__"


def field(name: str, log: str) -> str | None:
    """The frozen line's value, or None when the producer wrote no such line.

    A key the table names is read through the gates: the same 域, shape and path
    checks the pipeline applies, and the same sentence when one fails. The
    table also answers what None means here -- a required key's absence is the
    reader's complaint (it raises), an optional one's is the caller's (None).
    The 包清单's row is the one the gates refuse by name, and that refusal is
    the right answer here too: this reader hands back one key's value, and a
    `package <dir> <pkgname>` line is read by `pkglist_field`, not by a value.

    A name the table does not name is a log line (`pinned=`, `carried=`), read
    with `line_field_or`: there is no rule to apply, and None is the question.

    A duplicated line, or a line with an empty value, is the reader's complaint
    and raises with its own message: that is a protocol break, not a value.
    """
    row = next((row for row in table() if row["key"] == name), None)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "log.txt"
        path.write_text(log)
        quoted = shlex.quote(name)
        where = shlex.quote(str(path))
        if row is None:
            command = f"line_field_or {quoted} {where} {shlex.quote(ABSENT)}"
        elif row["shape"] == "list":
            # A list row's value names a file of paths; validating it is the
            # gate's, and the value this reader hands back is the name.
            command = f"files=(); frozen_list {quoted} {where} files || exit 1; line_field {quoted} {where}"
        elif row["required"] == "optional":
            command = (
                f'answer=""; frozen_var {quoted} {where} answer {shlex.quote(ABSENT)}; '
                'printf \'%s\\n\' "$answer"'
            )
        else:
            command = f'answer=""; frozen_var {quoted} {where} answer || exit 1; printf \'%s\\n\' "$answer"'
        result = subprocess.run(
            ["bash", "-c", f"set -euo pipefail; source {shlex.quote(str(LIB))}; {command}"],
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise AssertionError(f"{name}: {result.stderr.strip()}")
    value = result.stdout.strip()
    return None if value == ABSENT else value


def fields(log: str) -> dict[str, str]:
    """Every frozen line in the log, each value read through the reader.

    The handover a step prints, as a mapping: the callers ask for a key, so a
    line in another order is not a break, and a line written twice is. A test
    that splits the lines itself answers neither -- a helper that took the last
    of two `bump=` lines was exactly that, while the workflow's reader refuses
    to choose.
    """
    found: dict[str, str] = {}
    for name in dict.fromkeys(LINE_NAME_RE.findall(log)):
        value = field(name, log)
        if value is not None:
            found[name] = value
    return found
