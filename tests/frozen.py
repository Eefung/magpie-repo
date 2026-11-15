"""Read a producer's log the way the pipeline reads it.

`scripts/lib/pkg.sh`'s `line_field` / `line_field_or` are the only readers of
the frozen `name=value` lines in production. A test that splits them itself is
a second reader with its own rules -- `test_bump`'s `log_of` used to accept a
duplicated `bump=` (taking the last one) where `line_field` fails the step, so
the test job stayed green on a log the workflow would have died on.

This runs the production reader, with the caller's default for a line the
producer never wrote, which is what `upstream.sh` does with an absent `bump=`.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib" / "pkg.sh"

# A default no producer could write, so "absent" stays distinguishable from "".
ABSENT = "__absent__"


def field(name: str, log: str) -> str | None:
    """The frozen line's value, or None when the producer wrote no such line.

    A duplicated line, or a line with an empty value, is the reader's complaint
    and raises with its own message: that is a protocol break, not a value.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "log.txt"
        path.write_text(log)
        result = subprocess.run(
            [
                "bash",
                "-c",
                f"set -euo pipefail; source {shlex.quote(str(LIB))}; "
                f"line_field_or {shlex.quote(name)} {shlex.quote(str(path))} "
                f"{shlex.quote(ABSENT)}",
            ],
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise AssertionError(f"line_field_or {name}: {result.stderr.strip()}")
    value = result.stdout.strip()
    return None if value == ABSENT else value
