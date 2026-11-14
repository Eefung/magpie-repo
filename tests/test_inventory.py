"""The package inventory is stated once, and these hold it that way.

Three things used to claim to know which packages this repository publishes:
`bump.PACKAGES`, the argv `publish.yml` handed to `build-repo.sh`, and the
directories under `packages/`. Nothing compared them. A package added to the
dict was rendered and committed but never built -- and the next `--check` saw
its PKGBUILD current and printed `bump=0` for good, so the 软件源 never gained
its 包文件.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import bump  # noqa: E402  (the module lives beside the scripts, not on the path)


class InventoryAgreementTest(unittest.TestCase):
    def test_every_package_on_disk_is_in_the_inventory(self):
        # The failing input is a third directory with a PKGBUILD in it: the
        # render would write it, the commit would carry it, nothing would build
        # it. This is the assertion that notices.
        on_disk = {
            path.name
            for path in (REPO / "packages").iterdir()
            if (path / "PKGBUILD").is_file()
        }
        self.assertEqual(on_disk, set(bump.PACKAGES))

    def test_the_workflow_does_not_name_packages(self):
        # The build step reads the directories from the inventory, so a name
        # typed back into the YAML is the second list coming back.
        workflow = (REPO / ".github" / "workflows" / "publish.yml").read_text()
        self.assertNotIn("packages/magpie", workflow)


class PackageDirsTest(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self.addCleanup(os.chdir, self._cwd)
        os.chdir(REPO)

    def test_the_dirs_are_the_inventory_in_order(self):
        self.assertEqual(
            bump.package_dirs(bump.layout_for(REPO)),
            [f"packages/{pkg}" for pkg in bump.PACKAGES],
        )

    def test_the_frozen_line_is_what_the_workflow_reads(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(bump.main(["--packages"]), 0)
        self.assertEqual(
            buffer.getvalue().splitlines(),
            ["packages=" + " ".join(f"packages/{pkg}" for pkg in bump.PACKAGES)],
        )


if __name__ == "__main__":
    unittest.main()
