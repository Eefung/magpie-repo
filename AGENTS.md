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

推送前跑 `scripts/check.sh`：它就是 CI `test` job 的全部闸门（离线单测、shellcheck、ruff、actionlint，以及本机装着 makepkg 时的 probe——真 `makepkg` + `repo-add` 跑一遍打包链），三个 linter 由脚本按固定版本下到 `~/.cache/magpie-repo`，所以本机没装也跑得动、且与 runner 同版本。分段提交时每个中间态也跑一次（`./scripts/check-range.sh <base>..HEAD` 替你把每个 commit 建进 worktree 跑一遍，一行一个测试数）：它要十几秒，probe 占大头（真 `makepkg` 跑几个 case，数量看它自己打印的 `assertions=`），而每次 push 都会真的跑流水线。它跑不到的边界只有一个出处：它自己打印的 `not covered here` 段——那份清单会随改动变长或变短，别在这里另抄一份。

`publish.yml` 的 `paths` 是 `templates/**`、`scripts/**`、`tests/**` 与 `.github/workflows/publish.yml`，所以这些改动推送后会触发一次 publish，可能直接发一版。这类提交与别的工作分开推，相关的几笔一起推：一次推送只跑一次流水线，一笔一推就会发一遍。

拉远端只拉 main：`git fetch --no-tags origin main`。默认 refspec 里还有 `gh-pages`，那是 150 MB 的一棵树，全量 `git fetch` 会超时。

## 脚本地图

`scripts/` 里每个文件回答一个问题。改一条链之前先看这里，别从头翻：

| 脚本 | 它回答什么 |
| --- | --- |
| `scripts/bump.py` | 渲染 PKGBUILD；入口检查的两个门；这一趟发不发、发哪个版本；落后（五向）；包清单 |
| `scripts/lib/pkg.sh` | 包文件名的读者、保留窗口、db 条目、载荷核对、冻结行的种类表与三道门、包清单的 reader、版本的形状 |
| `scripts/build-check.sh` | 一个包目录从开工到交出：makepkg、依赖、`--version` 核对、清理 |
| `scripts/build-repo.sh` | 窗口、退役、db 重建（容器里跑） |
| `scripts/release.sh` | Release 的资产、SHA256SUMS、说明 |
| `scripts/release-state.sh` | `v<版本>` 有没有 Release（present/absent 是一个值） |
| `scripts/publish-pages.sh` | 一棵树 → gh-pages 上的一个 squash 提交 |
| `scripts/seed-tree.sh` | 上一棵树的包文件过不过来：有分支／没分支／问不到，三种答案 |
| `scripts/check.sh` | 全部闸门，以及它自己打印的「跑不到什么」 |
| `scripts/check-range.sh` | range 里每个 commit 各自的树过一遍闸门 |
| `scripts/probe-repo-build.sh` | 真 `makepkg` + `repo-add` 的离线探针 |

## 测试地图

`tests/` 的每个文件都在下面这张表上——`tests/test_docs.py` 对账，所以漏一个文件是红测试，不是一个记性。文件名说的是范围，不是它守住的事实。找一条断言时先看这张表，别从头翻：

| 文件 | 守住什么 |
| --- | --- |
| `test_bump.py` | 上游版本怎么选；渲染（模板 ↔ 值）；`--check` 的结论与冻结行 |
| `test_templates.py` | 已提交的 PKGBUILD 仍是它模板填上值的结果 |
| `test_pkg_sh.py` | `pkg.sh`：包名与排序、保留窗口、db 条目、冻结行的表与三道门、载荷核对、残留集合 |
| `test_build_check.py` | `build-check.sh`：一次构建的生命周期，`BUILD_AS` 的两个 adapter，交回来的目录里有什么 |
| `test_build_repo.py` | `build-repo.sh`：保留窗口、db 条目、退役包 |
| `test_release.py` | `release.sh`：这一版的资产、校验和、发布说明 |
| `test_release_state.py` | `release-state.sh`：Release 有没有、唯一的那条 gh 查询、查失败不等于没有 |
| `test_publish_pages.py` | `publish-pages.sh`：一棵树变成 gh-pages 上的一个 squash 提交（裸库当 remote） |
| `test_check_range.py` | `check-range.sh`：每个 commit 三种判定（有 `gates=` 与数、契约在但没数、比契约还老），失败怎么汇总；`check.sh` 的门表两向对账 |
| `test_frozen.py` | 测试侧的冻结行读者走同一道门（同措辞）；种类表 ↔ 读点 ↔ 写点的三向对账；`pkg.sh` 之外没有裸读者 |
| `test_inventory.py` | 包清单 ↔ 目录 ↔ workflow ↔ README 的对账；step 顺序/门/outputs、`run:` 块形状、arch 表、发布树、保留窗口的数字；seed 的三种答案各跑一次真的 |
| `test_docs.py` | README 里由程序决定的那两句（运行时间、provides/conflicts）各有 reader；测试地图与脚本地图的完整性；文档点名的模块与测试类 |
| `test_live.py` | `live.py`：注释边界（整行、行尾、引号内、转义、词首），`live` 不改行数，`naming` 按行而不是按出现次数 |
| `test_docs_claims.py` | `docs_claims.py`：六个前缀各解析一次、`templates/**` 与 `packages/<包名>/` 是形状不是声明、行号指得准、临时树里改名一页文档会被报；`a_b` 与 `name()` 两种写法、`package()` 落在 `templates/`、注释里的名字不算、path 与其余 kind 分开问 |
| `install_block.py` | 不是测试：用户要粘的安装段的唯一读者——README 的块与 Release 说明都过它 |
| `fixtures.py` | 不是测试：包文件名的唯一拼法，`test_pkg_sh.py` 用 `pkgfile_pkgname`/`pkgfile_version` 反向钉住 |
| `frozen.py` | 不是测试：冻结行的测试侧读者——表点名的键走同一道门（域、路径、缺行同措辞），表没点名的日志行走裸读者，其余测试都经它 |
| `workflow.py` | 不是测试：`publish.yml` 的唯一读者（job 的 env、step 列表、cron、push 的 `paths`、`$GITHUB_OUTPUT` 的写与读、它不用的那个载体） |
| `gate.py` | 不是测试：`check.sh` 的唯一读者（门表、`gate_*` 函数、抬头注释、打印的 note） |
| `docs_claims.py` | 不是测试：文档跨度的读者——`claims(document)` 列声明，`unresolved(document, repo)` 只留落不了地的，`unresolved_paths(...)` 从同一个答案里只挑 path（README 与 AGENTS 会点名测试侧的读者）；kind 由前缀与形状决定，不由「解析得出来」决定；路径取首词（后面的参数不算），首词带 `*`/`<`/`>` 才是形状；`a_b` 要在 `scripts/` 的活行里出现，`name()` 要有 `def name(` 或 `name() {`（`templates/` 也算），`…Test`/`…Reader`/`…Error` 要在 `tests/` 里有 `class` 行 |
| `live.py` | 不是测试：活行的读者——注释边界按 bash（词首才算注释，引号内与转义之后不算；那组字符见 `live.py` 的 `_WORD_BREAKS`）；`live` / `live_lines` / `naming` 三个动词。其余读点还在各自切注释，逐批搬过来 |

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

一次提交只做一件事：`build` 的渲染结果与流程改动分开提交，否则回滚时会把版本号一起带走。提交前读一遍 `git diff --cached`，`git add` 指名文件而不是 `-A`：一次提交只装一件事的 hunk，包括那些你没打算改的行。
