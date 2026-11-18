"""Read a shell-ish text as code: where does a comment start?

Every guard in this suite that looks at a `run:` block, a script or a
`.gitignore` has to tell code from prose before it asks its question, and each
one answered it itself -- before this ticket, eight split at the first ``#``
and fifteen dropped only whole-line comments -- and the two widths disagreed
about a trailing comment. The
consequence was not cosmetic -- ``true  # ./scripts/seed-tree.sh …`` read as the
call itself, so a commented-out seed kept its guard green (#15).

One reader, and the guards that still spell this themselves move over in
batches; until the last one does, this file is where the rule is written. It
answers three things and nothing else:

    live(source)             the text with comments removed, same line count
    live_lines(source)       its code lines, stripped, without the empty ones
    naming(source, value)    the code lines that name that value

The rules are bash's: a ``#`` begins a comment only at the start of a word --
line start, whitespace, or one of ``; & | ( ) < >`` -- and not inside ``'…'``,
inside ``"…"``, or after a backslash. A ``#`` inside a word is content, which
is what keeps a URL fragment or a ``a#b`` whole.

What it does not do: a quote that does not close on its own line (a heredoc
body, a multi-line string) is read line by line, so a ``#`` on such a line is a
comment. Every caller here reads shell the pipeline writes one line at a time;
a reader that guessed at a here-doc's body would be a parser, and this is not
one.

Whitespace: the space between code and the comment it ends with is trimmed --
it was there to separate them -- and every other character of a line survives,
including trailing whitespace on a line with no comment. A line whose only
content was a comment becomes empty and keeps its place.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

# Characters after which a `#` starts a new word, and so a comment. bash reads
# `a#b` as one word, `;#c` and `>#f` as a comment (the `#` starts the next word).
_WORD_BREAKS = " \t;&|()<>"


def _code_of(line: str) -> tuple[str, bool]:
    """That line's code, and whether a comment was cut from it."""
    code: list[str] = []
    quote = ""
    escaped = False
    cut = False
    for index, char in enumerate(line):
        if escaped:
            code.append(char)
            escaped = False
        elif char == "\\" and quote != "'":
            # Outside single quotes a backslash escapes the next character; a
            # `\#` is a literal `#`, not the start of a comment.
            code.append(char)
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
            code.append(char)
        elif char in "'\"":
            quote = char
            code.append(char)
        elif char == "#" and (index == 0 or line[index - 1] in _WORD_BREAKS):
            cut = True
            break
        else:
            code.append(char)
    return "".join(code), cut


def live(source: str) -> str:
    """The text with its comments removed, one output line per input line.

    Line endings are carried through rather than re-joined, so a text that ends
    with a blank line still ends with one: the callers compare line counts, and
    a reader that silently dropped the last empty line would move that count by
    one on one step and not another. A last line that was only a comment and
    carried no newline of its own gets one, for the same promise -- a `run:`
    block whose final line is prose would otherwise lose that line.
    """
    out: list[str] = []
    for line in source.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        code, cut = _code_of(body)
        out.append((code.rstrip() if cut else code) + line[len(body):])
    if out and not out[-1] and source.splitlines() and not source.endswith(("\n", "\r")):
        out[-1] = "\n"
    return "".join(out)


def live_lines(source: str) -> list[str]:
    """The code lines of that text, stripped, with the empty ones left out."""
    return [line for line in (one.strip() for one in live(source).splitlines()) if line]


def naming(source: str, value: str) -> list[str]:
    """The code lines that name a value: one entry per line, not per mention."""
    return [line for line in live_lines(source) if value in line]
