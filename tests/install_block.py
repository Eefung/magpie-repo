"""The install block, held to the four facts a 软件源 is served as.

Two documents ship it: README's 安装 section, and the notes every Release
carries. Neither can be generated from the other -- one is prose for someone who
arrived at the repository, the other is emitted by scripts/release.sh -- but both
restate the same section name, URL, arch directory and 包清单, and both are read
by a human who will type them into pacman.conf. That is why each one needs a
guard rather than a generator, and why both go through the reader here: a second
copy of this rule would be the drift it exists to catch.

`ReleaseTest` feeds it the notes release.sh writes, `ServedIdentityTest` feeds it
README's fenced block, and the identity on both sides is the workflow's env --
the one place ARCH, REPO_NAME and PAGES_URL are declared. `Served` is that
identity, so the block's nouns and the env's names are converted in one place
instead of at every call site.

The three config lines live in the block; the install lines may sit anywhere in
the document, which is why the two scopes arrive as separate arguments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# The line the whole install depends on: an unsigned 软件源 inherits `Required`
# from [options], and every write that omits this one ships instructions that
# cannot install (ADR-0002).
SIGLEVEL = "SigLevel = Optional"

# `pacman -S <names...>` on one line, `-Syu` excluded: the next character after
# the flag is what tells the two apart, and the comment after the names is not
# an argument.
_INSTALL_LINE = re.compile(r"pacman\s+-S(?!\w)([^#\n]*)")


@dataclass(frozen=True)
class Served:
    """What this 软件源 is served as: the three facts the workflow declares.

    They travel together everywhere they appear -- the block a user pastes, the
    env a step passes, the `Server =` URL in the notes -- and unpacking them by
    hand at each call is how a guard ends up with seven keyword arguments.
    """

    section: str
    url: str
    arch: str

    @property
    def server(self) -> str:
        """The value of the `Server =` line: the URL plus the served arch dir."""
        return f"{self.url}/{self.arch}"

    def lines(self) -> tuple[str, ...]:
        """The three lines a user copies into pacman.conf, as written."""
        return (f"[{self.section}]", SIGLEVEL, f"Server = {self.server}")

    def env(self) -> dict[str, str]:
        """The names the workflow declares the same three under."""
        return {"ARCH": self.arch, "REPO_NAME": self.section, "PAGES_URL": self.url}


def installed(installs: str) -> set[str]:
    """The packages this document's `pacman -S` lines name.

    A document may list one package per line (README, so each carries its
    description) or all of them on one (the notes, generated from the 包清单),
    and both are the same fact -- so the answer is read out of the arguments,
    not matched as a line.
    """
    named: set[str] = set()
    for match in _INSTALL_LINE.finditer(installs):
        named.update(word for word in match.group(1).split() if not word.startswith("-"))
    return named


def problems(
    served: Served,
    *,
    config: str,
    installs: str,
    label: str,
    packages: Iterable[str],
) -> list[str]:
    """Every install-block fact `config`/`installs` does not restate.

    `label` is the caller's name for the document ("README", "the Release
    notes"), because the message is read by whoever broke it.
    """
    missing = [
        f"{label} carries no {literal!r}" for literal in served.lines() if literal not in config
    ]
    named = installed(installs)
    missing += [f"{label} never installs {pkg}" for pkg in packages if pkg not in named]
    return missing
