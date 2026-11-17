"""Read the publish workflow as what it is: a declaration and a list of steps.

The file is YAML, but this suite is stdlib-only, so every test that needed it
read it with its own regex: a whole-line scan for the arch, a per-key
``^\\s+KEY: (\\S+)$`` for the identity, and the same ARCH pattern again one class
later. Each reader had its own idea of what a declaration looks like, so a
trailing comment on ``ARCH: x86_64``, a comment mentioning the arch, or moving
the env block into a step turned the test job red without changing anything the
workflow does -- while a guard whose subject is the workflow's *behaviour* (the
step order, the gates) could not be written at all.

One reader here, used by every test that needs the file. It parses the three
shapes this workflow uses -- a job-level ``env:`` block, ``- name:`` steps, and
the one ``cron:`` line a reader plans around -- and nothing else: it is not a
YAML parser, and a step shape it does not know arrives with no ``if`` or no
name, which the tests then report.

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
# A write to the step-output carrier, whatever produces it; and the one shape
# this reader parses. The gap between them is a fact nobody sees, so the tests
# assert the two counts agree.
WRITE_RE = re.compile(r'>>\s*"\$GITHUB_OUTPUT"')
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
                if_ = _value(key.group(2))
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


SCHEDULE_RE = re.compile(r"^\s+- cron:\s*(.+?)\s*$", re.M)


def schedule(source: str | None = None) -> str:
    """The ``cron:`` this file declares, as the five fields crontab reads.

    The README quotes the time the run happens, so the time has a producer and
    the sentence can be held to it (tests/test_docs.py). Exactly one: a second
    cron line would be a second answer to "when does this run", and the
    schedule is the one fact in this file a reader plans around.
    """
    found = [value.strip("'\"") for value in SCHEDULE_RE.findall(text() if source is None else source)]
    if len(found) != 1:
        raise AssertionError(f"{WORKFLOW} declares {len(found)} cron lines: {found}")
    return found[0]


PUSH_PATHS_RE = re.compile(r"^    paths:\s*(.+?)\s*$", re.M)


def paths(source: str | None = None) -> list[str]:
    """The patterns ``on.push.paths`` watches: which pushes start a run.

    Nothing read this list. Every other declaration in the file had a reader
    and a guard -- the env, the step order, the cron, the output carrier -- so
    dropping ``templates/**`` left all 355 tests green while AGENTS.md still
    told a maintainer that a template edit triggers a publish, and the advice
    beside that sentence ("这类提交与别的工作分开推") is advice about a list
    that no longer said what it was about. The consumer here is GitHub itself,
    which nothing offline can observe, so the guards are about the list's
    membership: this function is where they read it.

    Exactly one list, and the inline shape this file writes it in: a second
    ``paths:`` under the same trigger is a second answer to "what starts a
    run", and a block list is a shape this reader would silently read as empty.
    """
    text_ = text() if source is None else source
    triggers = text_.split("\non:", 1)[-1].split("\njobs:", 1)[0]
    found = PUSH_PATHS_RE.findall(triggers)
    if len(found) != 1:
        raise AssertionError(f"{WORKFLOW} declares {len(found)} push path lists: {found}")
    raw = found[0]
    if not (raw.startswith("[") and raw.endswith("]")):
        raise AssertionError(f"`on.push.paths` is not the inline list this reader knows: {raw}")
    return [item.strip().strip("'\"") for item in raw[1:-1].split(",") if item.strip()]


def env_writes(source: str | None = None) -> list[str]:
    """Live lines that write to ``$GITHUB_ENV`` -- the carrier this file avoids.

    Step-to-step facts ride ``$GITHUB_OUTPUT``, where ``output_keys`` and
    ``output_reads`` hold "every key has a reader" and "one write shape".
    ``$GITHUB_ENV`` had neither rule, so ``ver`` used to ride it and a dead
    handover there would have been invisible. The tests assert this list is
    empty instead of grepping the file themselves: the shape belongs to the
    reader, and the day a step really needs the second carrier is the day this
    function has to grow the same two rules.
    """
    text_ = text() if source is None else source
    return [
        line.strip()
        for line in text_.splitlines()
        if "$GITHUB_ENV" in line and not line.strip().startswith("#")
    ]


def unparsed_output_writes(source: str | None = None) -> list[str]:
    """Writes to the output carrier that ``output_keys`` cannot see.

    The keys are parsed out of one shape (``printf 'a=%s\\nb=%s\\n' ... >>
    "$GITHUB_OUTPUT"``). A second shape -- ``echo "x=y" >> "$GITHUB_OUTPUT"``,
    a heredoc -- writes a key no invariant in tests/ can name, so the tests
    assert this list is empty and a new carrier fails loudly instead of
    silently escaping the "every output has a reader" rule.
    """
    text_ = text() if source is None else source
    spans = [match.span() for match in OUTPUT_WRITE_RE.finditer(text_)]
    unparsed: list[str] = []
    offset = 0
    for line in text_.splitlines(keepends=True):
        if line.strip().startswith("#"):
            offset += len(line)
            continue
        if WRITE_RE.search(line):
            position = offset + line.index(">>")
            if not any(start <= position < end for start, end in spans):
                unparsed.append(line.strip())
        offset += len(line)
    return unparsed
