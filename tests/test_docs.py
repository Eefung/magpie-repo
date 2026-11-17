"""The facts the docs state, held to the programs that own them.

README is read by someone who will act on what it says, and it is not
executable: a sentence can be asserted, not generated. Two of its sentences
quote a value a program decides -- when the pipeline runs, and what the desktop
package declares against -- and neither had a reader, so moving the cron or
dropping `conflicts=` left the prose promising something that is no longer
true. (The third such fact, the 保留窗口's number, is held by
`test_inventory.RetentionWindowTest`, next to the window it belongs to.)

AGENTS.md's test map is the same shape and is held here too: a guard that lists
files fails until the map is complete, which is what makes the sentence above
the table a checked claim rather than a hope.

GLOSSARY and check.sh's blind-spot list are the same shape one step removed:
they point at modules, test classes and paths with the code's own names, and
nothing checked that those names still exist -- GLOSSARY had named
`check_arches` for a check that is inline in `read_template`.

    python3 -m unittest discover -s tests
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "tests"))

import bump  # noqa: E402  (the module lives beside the scripts, not on the path)
import gate  # noqa: E402  (the reader of scripts/check.sh)
import workflow  # noqa: E402  (the one reader of publish.yml)


def readme() -> str:
    return (REPO / "README.md").read_text()


def template(pkg: str) -> str:
    return (REPO / "templates" / f"{pkg}.PKGBUILD.in").read_text()


# --- what a document points at ------------------------------------------------
#
# Two documents point at the code with its own names: GLOSSARY says which module
# answers a question, and check.sh's "not covered here" says which test stands
# behind a gap. Both are entry points a reader acts on, and neither had a
# reader of its own -- GLOSSARY had been naming `check_arches`, which does not
# exist (the check is inline in `read_template`).
#
# ADRs are deliberately not scanned: a decision record quotes the code that was
# deleted, and "the fix was to delete `scripts/publish.sh`" is its job.

# A module's own name, as the docs write it: `read_template`, `line_field`.
IDENT = re.compile(r"`([a-z][a-z0-9]*_[a-z0-9_]+)`")

# A path in this repository, as the docs write it: `scripts/build-check.sh`.
PATH = re.compile(r"`((?:scripts|tests)/[\w./-]+)`")

# A test class, as the gate's note names it: `RootAdapterTest`.
CLASS = re.compile(r"\b([A-Z][A-Za-z]*(?:Test|Reader|Error))\b")

def code_facts(repo: Path = REPO) -> str:
    """The production tree, for a name the docs point at to exist in.

    `scripts/` only, and comment lines dropped: a doc names what *decides*
    something -- a module in the pipeline -- so a name kept alive in a comment
    about a deleted function is exactly the case that must not pass. Including
    `tests/` would let this file's own fixtures satisfy the claim they exist to
    falsify.
    """
    sources = [*repo.glob("scripts/**/*.sh"), *repo.glob("scripts/**/*.py")]
    return "\n".join(
        line
        for path in sources
        for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )


def gone_names(document: str, facts: str) -> list[str]:
    """Names a document points at that the code no longer carries."""
    return [
        name
        for name in sorted(set(IDENT.findall(document)))
        if not re.search(rf"\b{re.escape(name)}\b", facts)
    ]


def gone_paths(document: str, repo: Path = REPO) -> list[str]:
    """Paths a document points at that are not there.

    Existence, not executability: `scripts/lib/pkg.sh` is sourced, not run, so
    "every script the docs name can run" is a claim about the workflow's own
    steps -- which `test_inventory.WorkflowShellTest` already makes for the
    scripts a step invokes.
    """
    return [path for path in sorted(set(PATH.findall(document))) if not (repo / path).is_file()]


def gone_classes(note: str, repo: Path = REPO) -> list[str]:
    """Test classes the gate's note names that no test module defines."""
    defined = "\n".join(
        path.read_text() for path in (repo / "tests").glob("*.py")
    )
    return [
        name
        for name in sorted(set(CLASS.findall(note)))
        if f"class {name}" not in defined
    ]


class ScheduleTest(unittest.TestCase):
    """README says when the run happens; the workflow's cron is the answer."""

    def stated(self, source: str | None = None) -> str:
        """The README's sentence, derived from the cron the workflow declares."""
        minute, hour, *_ = workflow.schedule(source=source).split()
        return f"每天 {int(hour):02d}:{int(minute):02d} UTC"

    def test_the_readme_states_the_time_the_cron_declares(self):
        self.assertIn(self.stated(), readme())

    def test_a_moved_cron_without_the_readme_is_caught(self):
        moved = workflow.text().replace('cron: "17 4 * * *"', 'cron: "17 6 * * *"')
        self.assertIn('cron: "17 6 * * *"', moved, "the cron line moved; reword this test")
        self.assertNotIn(self.stated(moved), readme())

    def test_two_cron_lines_are_a_second_answer_not_a_choice(self):
        doubled = workflow.text().replace(
            '    - cron: "17 4 * * *"', '    - cron: "17 4 * * *"\n    - cron: "0 0 * * *"'
        )
        self.assertEqual(doubled.count("cron:"), 2, "the schedule block moved; reword this test")
        with self.assertRaises(AssertionError):
            workflow.schedule(source=doubled)


class ProvidesClaimTest(unittest.TestCase):
    """README's FAQ says the desktop package both provides and conflicts `magpie`.

    A user reads that to know why the package cannot live beside the unrelated
    `magpie` one. It is a claim about the template, and the template is where
    `provides=` and `conflicts=` live, so the sentence is held to them instead
    of to a memory of what they said.
    """

    DESKTOP = "magpie-bin"  # the 桌面版 the sentence is about

    def claimed(self, document: str) -> str:
        """The line of the document that makes the claim, or a failure."""
        for line in document.splitlines():
            if "provides" in line and "conflicts" in line:
                return line
        raise AssertionError("README no longer says what provides/conflicts are")

    def problems(self, document: str, template_text: str) -> list[str]:
        """Names the template declares that the document's sentence omits."""
        claim = self.claimed(document)
        names = (bump.array_entries(template_text, "provides") or []) + (
            bump.array_entries(template_text, "conflicts") or []
        )
        return [
            f"the claim does not name {name!r}" for name in names if f"`{name}`" not in claim
        ]

    def test_the_readme_names_what_the_template_declares(self):
        self.assertEqual(self.problems(readme(), template(self.DESKTOP)), [])

    def test_a_name_the_template_drops_is_caught(self):
        # The guard's own fixture: a template whose conflict is another name
        # while the sentence still says `magpie`. The previous version of this
        # test asserted on the string it had just edited and passed with the
        # guard removed.
        drifted = template(self.DESKTOP).replace("conflicts=('magpie')", "conflicts=('magpie-x')")
        self.assertIn("conflicts=('magpie-x')", drifted, "the conflict line moved; reword this test")
        self.assertEqual(
            [problem for problem in self.problems(readme(), drifted) if "magpie-x" in problem],
            ["the claim does not name 'magpie-x'"],
        )


class TestMapTest(unittest.TestCase):
    """AGENTS.md's test map lists every module in tests/, or it is not a map.

    The map is what a reader -- human or agent -- uses instead of grepping, and
    the sentence above it promises completeness. `tests/frozen.py` was missing
    while every test imported it, and the line count in the same sentence had
    drifted by six hundred lines; both are the shape this repository keeps
    finding, a document stating a fact a directory decides. The count is gone,
    and the listing is held to the directory here.

    The names are read from the **table's rows**, not from the file: the
    sentence above the table names a file to say who checks the map, and a
    document-wide search would count that sentence as a row.
    """

    SECTION = "## 测试地图"

    def listed(self, map_text: str) -> set[str]:
        """The file names the map's own table rows list."""
        section = map_text.split(self.SECTION, 1)[1].split("\n## ", 1)[0]
        return {
            line.split("|")[1].strip().strip("`")
            for line in section.splitlines()
            if line.startswith("| `")
        }

    def unlisted(self, map_text: str) -> list[str]:
        """Modules under tests/ the map's table does not list."""
        listed = self.listed(map_text)
        return [
            path.name for path in sorted((REPO / "tests").glob("*.py")) if path.name not in listed
        ]

    def agents(self) -> str:
        return (REPO / "AGENTS.md").read_text()

    def rows(self) -> set[str]:
        return {path.name for path in (REPO / "tests").glob("*.py")}

    def test_every_module_is_in_the_map(self):
        self.assertEqual(self.unlisted(self.agents()), [])
        # And the table lists nothing that is not a module in tests/.
        self.assertEqual(self.listed(self.agents()) - self.rows(), set())

    def test_a_module_the_map_omits_is_caught(self):
        drifted = "\n".join(
            line
            for line in self.agents().splitlines()
            if not line.startswith("| `test_docs.py`")
        )
        self.assertNotIn("test_docs.py", self.listed(drifted), "the row moved; reword this test")
        self.assertEqual(self.unlisted(drifted), ["test_docs.py"])


class ScriptsMapTest(unittest.TestCase):
    """AGENTS.md lists the scripts, and the list is held to the directory.

    The test map above answers "where is this guarded"; nothing answered "which
    script decides this", so a reader rebuilt that map by opening every script
    in `scripts/` -- which is the walk the map exists to save. Same shape as the
    test map, same guard: the table's own rows against `scripts/**/*.sh`, both
    directions, so a new script costs one row and a deleted one costs none.
    """

    SECTION = "## 脚本地图"

    def agents(self) -> str:
        return (REPO / "AGENTS.md").read_text()

    def listed(self, map_text: str) -> set[str]:
        """The paths the map's own table rows list."""
        section = map_text.split(self.SECTION, 1)[1].split("\n## ", 1)[0]
        return {
            line.split("|")[1].strip().strip("`")
            for line in section.splitlines()
            if line.startswith("| `")
        }

    def scripts(self) -> set[str]:
        """Everything under `scripts/` a reader could open: shell and python."""
        found = set()
        for suffix in ("*.sh", "*.py"):
            found |= {
                str(path.relative_to(REPO))
                for path in sorted((REPO / "scripts").rglob(suffix))
            }
        return found

    def test_every_script_is_in_the_map(self):
        listed = self.listed(self.agents())
        self.assertEqual(sorted(self.scripts() - listed), [])
        self.assertEqual(sorted(listed - self.scripts()), [])

    def test_a_script_the_map_omits_is_caught(self):
        dropped = "\n".join(
            line
            for line in self.agents().splitlines()
            if not line.startswith("| `scripts/check-range.sh`")
        )
        self.assertNotIn("scripts/check-range.sh", self.listed(dropped), "the row moved; reword this test")
        self.assertEqual(sorted(self.scripts() - self.listed(dropped)), ["scripts/check-range.sh"])


class NamedThingsTest(unittest.TestCase):
    """The names the docs point at exist, because a reader acts on them.

    GLOSSARY answers "which module decides this", and `check.sh`'s not covered
    here answers "what does green not prove". Both point with the code's own
    names -- a module, a test class, a path -- and nothing read them: GLOSSARY
    had said the render compared the arch table with `check_arches` while that
    check was inline in `read_template`, so the one sentence that tells a reader
    where to look named nothing at all.

    The scope is those two documents, not the ADRs. A decision record is
    allowed to quote the code it deleted -- ADR-0001 names `scripts/publish.sh`
    to say it was removed -- and scanning them would make a history its own
    failure.

    The identifiers are the ones this repository *owns*: lowercase snake_case
    module names, which is what a shell function and a python function are. Env
    names and tables (`ARCHES`, `PAGES_URL`) are left out rather than
    allowlisted one by one, because some of the uppercase names a glossary needs
    are somebody else's (`SHA256SUMS`, `PKGBUILD`) and an exception list would
    be a second document to drift.
    """

    def glossary(self) -> str:
        return (REPO / "GLOSSARY.md").read_text()

    def note(self) -> str:
        return gate.note()

    def test_every_module_glossary_names_is_in_the_tree(self):
        self.assertEqual(gone_names(self.glossary(), code_facts()), [])

    def test_every_name_the_gates_note_points_at_is_in_the_tree(self):
        # The note is the document that says what a green run does not prove,
        # and it names the module behind each gap (`served_evr`, `bump.py`).
        self.assertEqual(gone_names(self.note(), code_facts()), [])

    def test_every_path_the_documents_name_is_there(self):
        # Existence, not executability: `scripts/lib/pkg.sh` is sourced, not
        # run, and executability of what a *step* runs is the workflow's own
        # guard (`test_inventory.WorkflowShellTest`).
        for label, document in (("GLOSSARY.md", self.glossary()), ("check.sh", self.note())):
            with self.subTest(document=label):
                self.assertEqual(gone_paths(document), [])

    def test_every_test_class_the_note_names_is_defined(self):
        self.assertEqual(gone_classes(self.note()), [])

    def test_a_name_the_code_lost_is_caught(self):
        drifted = self.glossary().replace("`read_template`", "`check_arches`", 1)
        self.assertNotEqual(drifted, self.glossary(), "the entry moved; reword this test")
        self.assertEqual(gone_names(drifted, code_facts()), ["check_arches"])

    def test_a_path_that_moved_is_caught(self):
        drifted = self.glossary().replace("tests/test_templates.py", "tests/test_template.py")
        self.assertNotEqual(drifted, self.glossary(), "the entry moved; reword this test")
        self.assertEqual(gone_paths(drifted), ["tests/test_template.py"])

    def test_a_comment_does_not_keep_a_name_alive(self):
        # The tree's text includes the comments, so a function deleted from the
        # code but still named in a comment about it would pass a whole-text
        # search. Comment lines are dropped before the search, the same rule the
        # frozen-line reader applies to its call sites.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            (root / "scripts" / "a.sh").write_text(
                "real_fn() { :; }\n# ghost_fn used to live here\n"
            )
            facts = code_facts(root)
        self.assertIn("real_fn", facts)
        self.assertNotIn("ghost_fn", facts)
        self.assertEqual(gone_names("`ghost_fn`", facts), ["ghost_fn"])

    def test_a_test_class_that_was_renamed_is_caught(self):
        drifted = self.note().replace("RootAdapterTest", "RootAdapterReaderTest")
        self.assertIn("RootAdapterReaderTest", drifted, "the note moved; reword this test")
        self.assertEqual(gone_classes(drifted), ["RootAdapterReaderTest"])


class GateDocumentsTest(unittest.TestCase):
    """What the gate runs, and what a green run does not prove: one copy each.

    `GATE_ROWS` decides the gate set, and `tests/test_check_range.py` holds it
    to the `gate_*` bodies -- but four prose copies of the same facts lived
    beside it: `check.sh`'s header stated the count ("Five gates") and the
    blind-spot list, README's 全部闸门 line named the tools, and AGENTS.md named
    them again. None had a reader, so a sixth gate (row, body, and the roster
    assertion updated) left all 355 tests green with every sentence describing
    five.

    Two rules, because there are two facts. The roster: every row's word is in
    the sentence that claims to list the gates. The blind-spot list: exactly one
    copy, the one the run prints -- a sentence in the header describing coverage
    is the second copy AGENTS.md says does not exist.
    """

    # One row's word is not its slug: README and AGENTS name the offline tests
    # the way a Chinese reader does. One entry, and a gate whose word is neither
    # its slug nor here is what this guard reports.
    TRANSLATED = {"tests": "单测"}

    def documents(self) -> dict[str, str]:
        """The one line in each document that claims to list the gates."""
        sources = {
            "README.md": readme(),
            "AGENTS.md": (REPO / "AGENTS.md").read_text(),
        }
        found: dict[str, str] = {}
        for name, source in sources.items():
            # The map's own row about check.sh says the same words, and it is
            # a row about the script, not a claim to list the gates.
            lines = [
                line
                for line in source.splitlines()
                if "全部闸门" in line and not line.startswith("|")
            ]
            if len(lines) != 1:
                raise AssertionError(f"{name} states the gate list {len(lines)} times; reword this test")
            found[name] = lines[0]
        return found

    def missing(self, source: str | None = None) -> dict[str, list[str]]:
        """For each document, the gates its sentence does not name."""
        return {
            name: [
                slug
                for slug in gate.rows(source)
                if self.TRANSLATED.get(slug, slug) not in line
            ]
            for name, line in self.documents().items()
        }

    def test_each_document_names_every_gate(self):
        self.assertEqual({name: found for name, found in self.missing().items() if found}, {})

    def test_a_gate_the_documents_do_not_name_is_reported(self):
        row = '    "probe|probe (real makepkg + repo-add)"\n'
        added = gate.text().replace(row, row + '    "vocabulary|the 落后 vocabulary"\n')
        self.assertNotEqual(added, gate.text(), "the table moved; reword this test")
        self.assertEqual(self.missing(added), {"README.md": ["vocabulary"], "AGENTS.md": ["vocabulary"]})

    def test_the_header_states_no_coverage_of_its_own(self):
        # The header is prose about the gate set and about what green does not
        # prove; the note the run prints is the list that has a reader.
        header = gate.header()
        self.assertEqual(re.findall(r"\btests/[\w./-]+", header), [])
        self.assertEqual(CLASS.findall(header), [])

    def test_a_header_that_restates_the_note_is_reported(self):
        first = "# Run every gate CI's test job runs, from any directory."
        leaked = gate.text().replace(
            first,
            first
            + "\n# Not covered here: tests/test_build_check.py stubs the root path.",
        )
        self.assertNotEqual(leaked, gate.text(), "the header moved; reword this test")
        self.assertEqual(
            re.findall(r"\btests/[\w./-]+", gate.header(leaked)), ["tests/test_build_check.py"]
        )


class DirectionsVocabularyTest(unittest.TestCase):
    """落后的方向词：码里印什么，GLOSSARY 与 AGENTS 就说那几个。

    「这趟为什么发」的答案是日志里 `behind <包>=<方向>` 的那些词。它们曾经是四个
    常量加一个没有名字的第五向（句子曾写在一条 relay 脚本的 echo 里，计数由一条测试
    从那一行抠出来再加一）；现在五个方向都是 `bump.py` 的常量、一个元组，计数
    与词表都从那里出，两段文档跟着它走。

    `tests/test_frozen.py` 给冻结行清单做了同样的两向对账，这里用的是它的形状：
    集合相等，两个方向各有一条变异证明守卫真的会响。
    """

    NUMERALS = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八", 9: "九"}

    def entry(self) -> str:
        """GLOSSARY 的落后条，只那一段：整份文档里这些词还出现在别处。"""
        glossary = (REPO / "GLOSSARY.md").read_text()
        after = glossary.split("\n**落后**：", 1)[1].split("_Avoid_", 1)[0]
        return after

    def printed(self) -> list[str]:
        """这次检查能印出来的那些方向词。"""
        return list(bump.DIRECTIONS)

    def listed(self, entry: str) -> set[str]:
        """那条 `behind <包>=<方向>` 后面括号里列的词。"""
        match = re.search(r"`behind[^`]*`（([^）]*)）", entry)
        if match is None:
            raise AssertionError("落后段不再列方向词；改这句测试")
        # A word may carry a dash: `no-release` is one direction, not two.
        return set(re.findall(r"`([a-z][a-z0-9-]*)`", match.group(1)))

    def missing(self, entry: str, words: list[str] | None = None) -> list[str]:
        return [word for word in (words or self.printed()) if f"`{word}`" not in entry]

    def test_the_entry_names_every_word_the_check_prints(self):
        self.assertEqual(self.missing(self.entry()), [])

    def test_the_entry_names_no_word_the_check_cannot_print(self):
        self.assertEqual(self.listed(self.entry()), set(self.printed()))

    def count(self, entry: str) -> str:
        """The number the entry's opening sentence gives."""
        match = re.search(r"([一二三四五六七八九])个方向都算", entry)
        if match is None:
            raise AssertionError("落后段不再说几个方向；改这句测试")
        return match.group(1)

    def test_the_entry_counts_the_directions_the_check_has(self):
        # 五向都印 `behind <包>=<方向>`：计数就是常量的个数，没有「再加一」。
        self.assertEqual(self.count(self.entry()), self.NUMERALS[len(self.printed())])

    def test_a_count_the_check_disagrees_with_is_reported(self):
        drifted = self.entry().replace("五个方向都算", "六个方向都算")
        self.assertNotEqual(drifted, self.entry(), "落后段换了写法；改这句测试")
        self.assertEqual(self.count(drifted), "六")

    def test_agents_counts_the_directions_the_check_has(self):
        # AGENTS 的脚本地图写着「落后（四向）」：那是同一份计数的第三份抄写。
        counted = f"落后（{self.NUMERALS[len(self.printed())]}向）"
        self.assertIn(counted, (REPO / "AGENTS.md").read_text())

    def test_a_word_the_check_gained_is_reported(self):
        self.assertEqual(self.missing(self.entry(), self.printed() + ["downgraded"]), ["downgraded"])

    def test_a_word_the_entry_lost_is_reported(self):
        dropped = self.entry().replace(" / `retired`", "")
        self.assertNotEqual(dropped, self.entry(), "落后段换了写法；改这句测试")
        self.assertEqual(self.missing(dropped), ["retired"])

    def test_a_word_the_check_cannot_print_is_reported(self):
        drifted = self.entry().replace("`no-release`）", "`no-release` / `downgraded`）")
        self.assertNotEqual(drifted, self.entry(), "落后段换了写法；改这句测试")
        self.assertEqual(self.listed(drifted) - set(self.printed()), {"downgraded"})


class ChineseSpacingTest(unittest.TestCase):
    """Two Chinese words in a row take no space between them.

    English prose embeds a glossary term with spaces around it -- `the 包清单
    publishes` -- and that is right: those are English word boundaries, and
    dropping them would run two English words together. The same habit carried
    into Chinese prose leaves a space between two Chinese words, where the
    language has no word separator and the space does no work: it reads as a
    typo, and this repository has made it three times.

    A grep is what stops a fourth. The scope is what git tracks, so an untracked
    scratch file is not the gate's business; the self-test below is what keeps
    the rule from passing vacuously.
    """

    SUFFIXES = ("*.md", "*.py", "*.sh", "*.yml", "*.in")
    SPACED = re.compile(r"[\u4e00-\u9fff] +[\u4e00-\u9fff]")

    def tracked(self) -> list[Path]:
        """The files this rule covers: what git would carry, not a walk.

        `--others --exclude-standard` is what makes a brand-new file part of the
        answer: it is not in the index until someone stages it, and the gate runs
        before the commit.
        """
        result = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "--cached", "--others",
             "--exclude-standard", *self.SUFFIXES],
            capture_output=True,
            text=True,
            check=True,
        )
        return [REPO / line for line in result.stdout.splitlines() if line]

    def spaced(self, text: str) -> list[str]:
        """The lines of this text that set two Chinese words off from each other."""
        return [line.strip() for line in text.splitlines() if self.SPACED.search(line)]

    def test_no_chinese_word_is_set_off_from_the_next(self):
        offenders = [
            f"{path.relative_to(REPO)}: {line[:70]}"
            for path in self.tracked()
            for line in self.spaced(path.read_text())
        ]
        self.assertEqual(offenders, [])

    def test_the_rule_bites(self):
        # Built at run time: a literal of the offending shape in this file would
        # be found by the test above, and a self-test that reddens itself is not
        # one. Both accepted shapes are here too, so the rule cannot be widened
        # by accident -- an English sentence embedding a Chinese term keeps its
        # spaces, and so does a term with a Latin half.
        bad = "落后" + " " + "的第四向"
        self.assertEqual(self.spaced(bad), [bad])
        self.assertEqual(self.spaced("the 包清单 publishes"), [])
        self.assertEqual(self.spaced("arch 表管的是另一半"), [])


if __name__ == "__main__":
    unittest.main()
