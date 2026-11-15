"""One spelling of a 包文件's name for the test tree.

The rule belongs to scripts/lib/pkg.sh: makepkg writes
`<pkgname>-<pkgver>-<pkgrel>-<arch>.pkg.tar.zst`, and `pkgfile_pattern` /
`pkgfile_pkgname` / `pkgfile_version` are the only readers of it. The test tree
spelled the same name in four of its own modules -- `test_pkg_sh.build()`,
`test_release.pkgfile()`, `test_build_repo.seed()`, and the literals in the
workflow seed fixture -- plus two stubs that wrote `-1-x86_64` in by hand.

That is the shape a fixture drifts in. A pkgrel bump or a second arch (ADR-0003
makes both ordinary) leaves every retention and release test green while the
real chain produces names none of them anticipated, because the fake is the only
place the field was ever written down twice. `pkgfile_name` is the one writer
here, and `tests/test_pkg_sh.py::FixtureNamingTest` reads its output back
through the shell's own readers, so a rule that changes fails a test instead of
being outvoted by four copies.
"""

from __future__ import annotations

# The two fields a 包文件's name carries that are not in the PKGBUILD's
# interesting parts: makepkg's extension, and the arch this repository builds.
# Both are held to pkg.sh by FixtureNamingTest.
SUFFIX = ".pkg.tar.zst"
ARCH = "x86_64"
REL = "1"


def pkgfile_name(
    pkgname: str, version: str, rel: str = REL, arch: str = ARCH
) -> str:
    """The name makepkg gives a package file, as every test names it."""
    return f"{pkgname}-{version}-{rel}-{arch}{SUFFIX}"
