"""One spelling of a 包文件's name for the test tree.

The rule belongs to scripts/lib/pkg.sh: makepkg writes
`<pkgname>-<pkgver>-<pkgrel>-<arch>.pkg.tar.zst`, and `pkgfile_pattern` /
`pkgfile_pkgname` / `pkgfile_version` are the only readers of it. The test tree
spelled the same name in four of its own modules -- `test_pkg_sh.build()`,
`test_release.pkgfile()`, `test_build_repo.seed()`, and the literals in the
workflow seed fixture -- plus two stubs that wrote `-1-x86_64` in by hand.

That is the shape a fixture drifts in. A pkgrel bump, or a second arch (which
ADR-0003 does not make ordinary -- an arch joins the table only with a build and
a 载荷核对 path behind it, and ADR-0001 reopens when a second one ships), leaves
every retention and release test green while the real chain produces names none
of them anticipated: the fake is the only place the field was written down
twice. `pkgfile_name` is the one writer here, and
`tests/test_pkg_sh.py::FixtureNamingTest` holds each of its three constants to
the thing that owns it -- the extension to pkg.sh's readers, the arch to the
arch 表, pkgrel to the templates -- so a rule that changes fails a test instead
of being outvoted by four copies.
"""

from __future__ import annotations

# The three fields makepkg decides that a test cannot ask it for. Each has an
# owner elsewhere, and FixtureNamingTest names that owner: SUFFIX is
# `pkgfile_suffix`'s, ARCH is the arch 表's (`bump.ARCHES`, itself held to the
# workflow's env), REL is the templates' `pkgrel=`.
SUFFIX = ".pkg.tar.zst"
ARCH = "x86_64"
REL = "1"


def pkgfile_name(
    pkgname: str, version: str, rel: str = REL, arch: str = ARCH
) -> str:
    """The name makepkg gives a package file, as every test names it."""
    return f"{pkgname}-{version}-{rel}-{arch}{SUFFIX}"
