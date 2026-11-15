"""Read the publish workflow as what it is: a declaration and a list of steps.

The file is YAML, but this suite is stdlib-only, so every test that needed it
read it with its own regex: a whole-line scan for the arch, a per-key
``^\\s+KEY: (\\S+)$`` for the identity, and the same ARCH pattern again one class
later. Each reader had its own idea of what a declaration looks like, so a
trailing comment on ``ARCH: x86_64``, a comment mentioning the arch, or moving
the env block into a step turned the test job red without changing anything the
workflow does -- while a guard whose subject is the workflow's *behaviour* (the
step order, the gates) could not be written at all.

One reader here, used by every test that needs the file. It parses the two
shapes this workflow uses -- a job-level ``env:`` block and ``- name:`` steps --
and nothing else: it is not a YAML parser, and a shape it does not know is a
step with no ``if`` or no name, which the tests then report.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "publish.yml"

# A job header at 2 spaces, a step at 6, a step's key at 8.
JOB_RE = re.compile(r"^  (\w+):\s*$")
STEP_RE = re.compile(r"^      - (?:name: (.+)|uses: (.+))\s*$")
KEY_RE = re.compile(r"^        (\w[\w-]*):\s*(.*)$")
ENV_KEY_RE = re.compile(r"^      (\w+):\s*(.*)$")
OUTPUT_READ_RE = re.compile(r"steps\.(\w+)\.outputs\.(\w+)")
OUTPUT_WRITE_RE = re.compile(r"printf '([^']*)'[^\n]*\n[^\n]*>>\s*\"\$GITHUB_OUTPUT\"")


@dataclass(frozen=True)
class Step:
    """One entry of the job's step list, with the keys this file uses."""

    name: str
    if_: str | None
    run: str


def text() -> str:
    return WORKFLOW.read_text()


def job(name: str = "publish", source: str | None = None) -> list[str]:
    """The lines of one job, header excluded.

    The file has two jobs; every reader here is about the publishing one, and
    the test job's steps would otherwise be read as part of the same list.
    """
    lines = text().splitlines() if source is None else source.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line == f"  {name}:":
            start = index + 1
            break
    if start is None:
        raise AssertionError(f"no {name} job in {WORKFLOW}")
    body = []
    for line in lines[start:]:
        if JOB_RE.match(line) or line == "jobs:":
            break
        body.append(line)
    return body


def env(source: str | None = None) -> dict[str, str]:
    """The publish job's own env block, without quotes and trailing comments.

    Only the job-level block: a step's ``env:`` is that step's business, and a
    value declared in both places would be two facts wearing one name.
    """
    found: dict[str, str] = {}
    inside = False
    for line in job(source=source):
        if line == "    env:":
            inside = True
            continue
        if not inside:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = ENV_KEY_RE.match(line)
        if match is None:
            inside = False
            continue
        found[match.group(1)] = _value(match.group(2))
    return found


def _value(raw: str) -> str:
    """A YAML scalar as this file writes it: maybe quoted, maybe commented."""
    value = raw.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def steps(source: str | None = None) -> list[Step]:
    """The publish job's steps, in the order they run."""
    lines = job(source=source)
    blocks: list[list[str]] = []
    for line in lines:
        if STEP_RE.match(line):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)

    found: list[Step] = []
    for block in blocks:
        match = STEP_RE.match(block[0])
        assert match is not None, block[0]  # it matched when the block was opened
        name = (match.group(1) or f"uses {match.group(2)}").strip()
        if_ = None
        run = ""
        for index, line in enumerate(block):
            key = KEY_RE.match(line)
            if key is None:
                continue
            if key.group(1) == "if":
                if_ = key.group(2).strip()
            elif key.group(1) == "run" and key.group(2).strip() in ("|", ">"):
                run = "\n".join(block[index + 1 :])
        found.append(Step(name=name, if_=if_, run=run))
    return found


def output_keys(source: str | None = None) -> set[str]:
    """Every key this file writes to ``$GITHUB_OUTPUT``."""
    keys: set[str] = set()
    for block in OUTPUT_WRITE_RE.findall(text() if source is None else source):
        # The format is one ``name=%s`` per line, written with a literal \n.
        for part in block.split("\\n"):
            match = re.match(r"(\w+)=%s$", part)
            if match is not None:
                keys.add(match.group(1))
    return keys


def output_reads(source: str | None = None) -> set[str]:
    """Every output key some step reads back through ``steps.<id>.outputs``."""
    return {
        key
        for _, key in OUTPUT_READ_RE.findall(text() if source is None else source)
    }
