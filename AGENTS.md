# AGENTS.md

本文件给在这个仓库里干活的 AI agent 看。项目本身的说明见 [README](README.md)。

## Agent skills

### Issue tracker

issue 与 spec 都放 GitHub Issues（`Eefung/magpie-repo`），用 `gh` CLI 操作。详见 `docs/agents/issue-tracker.md`。

### Triage labels

五个规范分流角色，标签串就是角色名本身：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

single-context：根目录一个 `GLOSSARY.md`，决定记在 `docs/adr/`。详见 `docs/agents/domain.md`。

## 检查

推送前跑 `scripts/check.sh`：它就是 CI `test` job 的全部闸门（离线单测、shellcheck、ruff、actionlint，以及本机装着 makepkg 时的 probe——真 `makepkg` + `repo-add` 跑一遍打包链），三个 linter 由脚本按固定版本下到 `~/.cache/magpie-repo`，所以本机没装也跑得动、且与 runner 同版本。分段提交时每个中间态也跑一次：它要十几秒，probe 占大头（真 `makepkg` 跑几个 case，数量看它自己打印的 `assertions=`），而每次 push 都会真的跑流水线。它跑不到的边界只有一个出处：它自己打印的 `not covered here` 段——那份清单会随改动变长或变短，别在这里另抄一份。

`publish.yml` 的 `paths` 含 `scripts/**`、`tests/**`、`templates/**`，所以这些改动推送后会触发一次 publish，可能直接发一版。这类提交与别的工作分开推，相关的几笔一起推：一次推送只跑一次流水线，一笔一推就会发一遍。一次提交只做一件事。

## 测试地图

`tests/` 的每个文件都在下面这张表上——`tests/test_docs.py` 对账，所以漏一个文件是红测试，不是一个记性。文件名说的是范围，不是它守住的事实。找一条断言时先看这张表，别从头翻：

| 文件 | 守住什么 |
| --- | --- |
| `test_bump.py` | 上游版本怎么选；渲染（模板 ↔ 值）；`--check` 的结论与冻结行 |
| `test_templates.py` | 已提交的 PKGBUILD 仍是它模板填上值的结果 |
| `test_pkg_sh.py` | `pkg.sh`：包名与排序、保留窗口、db 条目、冻结行 reader、载荷核对、残留集合 |
| `test_build_check.py` | `build-check.sh`：一次构建的生命周期，`BUILD_AS` 的两个 adapter，交回来的目录里有什么 |
| `test_build_repo.py` | `build-repo.sh`：保留窗口、db 条目、退役包 |
| `test_release.py` | `release.sh`：这一版的资产、校验和、发布说明 |
| `test_publish_pages.py` | `publish-pages.sh`：一棵树变成 gh-pages 上的一个 squash 提交（裸库当 remote） |
| `test_upstream.py` | `upstream.sh`：这次要不要发布、发哪个版本 |
| `test_frozen.py` | 测试侧的冻结行读者与 `line_field` 同规则 |
| `test_inventory.py` | 包清单 ↔ 目录 ↔ workflow ↔ README 的对账；step 顺序/门/outputs、`run:` 块形状、arch 表、发布树、保留窗口的数字；seed 那一行跑真分支 |
| `test_docs.py` | README 里由程序决定的那两句（运行时间、provides/conflicts）各有 reader；这张测试地图的完整性 |
| `install_block.py` | 不是测试：用户要粘的安装段的唯一读者——README 的块与 Release 说明都过它 |
| `fixtures.py` | 不是测试：包文件名的唯一拼法，`test_pkg_sh.py` 用 `pkgfile_pkgname`/`pkgfile_version` 反向钉住 |
| `frozen.py` | 不是测试：冻结行的测试侧读者——直接跑生产的 `line_field`，其余测试都经它 |
| `workflow.py` | 不是测试：`publish.yml` 的唯一读者（job 的 env、step 列表、cron、`$GITHUB_OUTPUT` 的写与读、它不用的那个载体） |

## 提交

遵循[约定式提交](https://www.conventionalcommits.org/zh-hans/v1.0.0/)：`type(scope): 描述`，描述用中文，句末不加句号。

| 类型 | 用在哪 |
| --- | --- |
| `feat` | 用户可见的新能力 |
| `fix` | 修掉错误行为 |
| `docs` | README、GLOSSARY、ADR、调研笔记 |
| `refactor` | 不改行为的重排 |
| `test` | 只加或改测试，不改行为 |
| `build` | 发布产物与版本号——渲染出的 PKGBUILD、发布流程里碰版本的地方。原来的 `upgpkg:` 一律用这个 |
| `ci` | workflow 与发布流水线本身 |
| `chore` | 配置、工具、依赖 |

一次提交只做一件事：`build` 的渲染结果与流程改动分开提交，否则回滚时会把版本号一起带走。
