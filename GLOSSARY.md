# magpie-repo

把上游 magpie 的发版重打成 Arch 包，再自托管成一个 pacman 软件源。本仓库不含上游代码，只负责同步、打包与发布。

## Language

**上游**：
yetone/magpie 项目以及它发布的构建产物；本仓库只读取，不修改。
_Avoid_: 官方、源仓库

**版本**：
上游 release 的 tag 去掉 `v`，形如 `0.1.3995`：数字与点，至少两段，每段都不空——`1`、`1.`、`1..2` 都不算。这是渲染前强制的那条不变量，也是 `pkg.sh` 的 `version_shape_ok`（包文件名的读者与 `publish-pages.sh` 手跑的门都问它）用的同一条：shell 侧一份，`bump.py` 的 `VERSION_RE` 一份，两个被 `tests/test_pkg_sh.py` 的一张表钉在一起。
_Avoid_: 版本号、tag

**pkgrel**：
同一个上游版本的「第几次打包」，从 1 起。它是一个整数——Arch 也允许 `<int>.<int>`（依赖 bump 的 `1.1`），本仓库不用那种形状，而且这件事只在一个地方定：模板被读的那一刻（`check_pkgrel`），不是那个值读不懂时悄悄跳过的一支。包文件名字的读者（`pkg.sh` 的 `pkgfile_pkgname`）与「pkgrel 只能往上加」那条拒绝用同一个宽度，否则一个 Arch 合法的字符就能把后者关掉。它不属于**版本**（版本只由数字和点组成，pkgrel 也是渲染前不许出现的形状），但包文件的名字与 db 里的 `%VERSION%` 都是 `<pkgver>-<pkgrel>`——所以重建同一个上游版本，就是把这个数字加一，而「这次要发的东西变了没有」也只有这一对说得清。
_Avoid_: 打包版本号、release number

**包清单**：
本仓库发布的包组成的集合——目录名，以及它们在上游资产名里的前缀。渲染、`--check` 与打包都从它取，`--packages` 是它唯一的出口：一行一个包，`package <相对仓库根的目录> <pkgname>`。目录给打包步骤，`pkgname` 给发布步骤，两者都不在 workflow 里出现第二次。`pkgname` 同时是包的目录名、清单的 key 与模板里 `pkgname=` 的字面值：渲染期把模板钉到 key 上，不等就不写盘；渲染出的 PKGBUILD 又由 `tests/test_templates.py` 钉在模板上，所以 `build-check` 从 PKGBUILD 读出的 `pkgname=`（作为冻结行交出）与发布步骤手里的 key 是同一个值。
_Avoid_: 包列表、packages 变量

**冻结行**：
生产者打印一次、读者不再回头解析的 `name=value` 行：`--check` 的 `upstream=` / `target=` / `bump=` / `pin=`、`upstream.sh` 的 `packages=`、`--render` 的 `ver=`、`--packages` 的 `package <目录> <pkgname>`、`release-state.sh` 的 `release=` / `tag=`、`build-check` 的 `pkgname=` / `artifact=`、`release.sh` 的 `assets=` / `sums=` / `notes=`、`probe-repo-build.sh` 的 `assertions=`、`check.sh` 的 `tests=` / `gates=`。一条行算不算冻结行由 grep 决定，不由它长得像不像：`build-check` 的 `pkgver=` 曾被列在这里，而它唯一的读者是一条测试；`--check` 的 `carried=` 走了同一条路——第五向搬进入口检查之后，读它的只剩日志的读者。那份 grep 现在是 `tests/test_frozen.py` 的一条断言——`line_field` 与 `line_field_or` 的每个调用点都必须在这份清单里出现——所以加一条行而不写在这里，是红测试。读它只走 `scripts/lib/pkg.sh` 的三个 reader，不由每个调用方自己 split：`line_field`（重复一行是协议断了、空值算失败、缺行也失败）、`line_field_or`（缺行给调用方指定的默认值，但绝不把「写了两行」当成缺行）与 `pkglist_field`。措辞是契约的那几条之外，其余是日志。
_Avoid_: 输出格式、stdout

**入口检查**：
每次运行的第一步：问上游最新、核对这棵树，并决定这次要不要发布、发哪个版本。核对有两个门，都在 `scripts/bump.py`：`read_template` 判模板本身合不合法（`pkgname=`、`arch=()` 对 arch 表、`source`/`sha256sums` 数组、`package()` 的 case 臂），`values_of` 判已提交的 PKGBUILD 还是不是它的渲染——两个门都在 `--check` 里，不等渲染，所以一份改坏的模板当次就红，而不是等到上游真动了版本。三件事都在 `--check` 一处回答，它交出的冻结行就是这个答案——`bump=` 是结论，`target=` 是渲染要拿的版本，`pin=` 是这一趟要不要记下**钉住**。`scripts/upstream.sh` 与 workflow 的 step 都只中继这四条行（前者再多交一个包清单的路径 `packages=`），不再自己判：判断写在 YAML 里时，唯一能守它的是对 YAML 文本的 grep。
_Avoid_: 检查步骤、pre-check

**arch 表**：
`ARCHES`——本仓库发布的 arch 与上游资产名后缀的对应（`x86_64` 对 `linux-amd64`）。模板里 `arch=()`、`source_<arch>=`（文件名后缀与下载 URL 两处）、`sha256sums_<arch>=` 与 `package()` 的 `case` 四处都受它约束：加一个 arch 是改这张表，不是给某个模板开例外。渲染前的 `read_template` 逐条比，不是查子串。表里只有构建并核对过的 arch：渲染会为表里每个 arch 去上游要资产，要不到就整次渲染失败，所以一个不发布的 arch 等于握着发布的开关（ADR-0003）。
_Avoid_: 架构列表、ARCHES 变量

**渲染**：
把 `templates/<包>.PKGBUILD.in` 与算好的校验和填成 `packages/<包>/PKGBUILD`。写盘之前的所有判定——版本合法、校验和齐备、模板与值互相对得上——都属于渲染。plain `source=` 里那两条 URL 是模板自己给的，渲染就按它们去取字节算摘要：包下载的与摘要算的是同一份，这个一致由构造保证，不是另有一条对账。`values_of` 是它的逆：把已渲染的文件对回模板，交出被填进占位符的值。
_Avoid_: 生成、打包

**打包**：
用 makepkg 把渲染好的 PKGBUILD 变成 `.pkg.tar.zst`。这些是 `-bin` 包，只下载上游二进制再重新封装。
_Avoid_: 编译

**钉住**：
人挑的那个**版本**在树里留下的记录：`PINNED`，一行，由把 PKGBUILD 提交回 main 的那一步写下——条件是 `--check` 的 `pin=1`，也就是这一趟要发的版本不是**上游**那个；同版本的 dispatch 是重发（第五向），不写。由 `bump.py` 的 `pinned_of` 读。它回答的是**落后**答不了的另一半：树比**上游**旧，可能是树忘了往前走，也可能是有人让它停在那里，而「与上游不一致」这一个值分不出这两件事——回滚落地之后，下一趟定时运行会把上游的版本推回去，回滚只活一天。钉住只压住第一个方向（上游动了那一个），其余四个照常：树带着被钉住的版本，而它不是**软件源**服务的那一份时，它照样要发。它不是常驻状态：每一趟都在日志里打一行 `pinned=` 说明它压住了什么，写错是按名的拒绝，解除是删掉那一行做一次提交（或者那一趟因为别的方向要发，`pin=0`，提交那一步顺手删掉它）。
_Avoid_: 锁版本、冻结
**落后**：
这棵树要发的东西与软件源服务的不一致。五个方向都算，`bump=1` 是它们的并集（人用 `WANTED` 点了版本时另算：那一趟总要发，与方向无关）：待渲染的 pkgver 不是上游那个（上游动了版本，渲染该重做）——除非这个 pkgver 正是**钉住**的那个，那一向就不是落后，而是有人让它停在那里；已渲染的 `<pkgver>-<pkgrel>` 不是 `magpie.db` 里那一条（提交了却从未到达客户端——一次重建、一次在推送与提交之间失败的运行、或者第一次发布）；或者上游对同一个 tag 重传了资产，已渲染的校验和不是它现在发布的那份（只有 release 自己的 `SHA256SUMS` 说得清）；或者软件源还在服务一个包清单里已经没有的包——它的文件与 db 条目留着，而清掉它们的退役只在 `bump=1` 时跑得到，所以它必须算作第四向，否则删一个包就要等下一次上游动版本；或者这一趟该发的版本没有 Release——重建之后最容易落到这一格，因为重建的 PKGBUILD 是人提交进 main 的，分支推送与 db 都已是这一版的，「发布 Release」那一步失败一次，上面四向就全静。这一向要问 GitHub，所以 `--check` 读一个仓库名（`RELEASE_REPO`）：其余四向都静、也没人用 `WANTED` 点版本时，它问树里那一版有没有 Release——`404` 是发布理由，`200` 才是真的没事可做，两者都说不出时（没给仓库名，或者查询失败）只打一行让人去查，结论不变。树里那一版不是这一趟要渲染的 `target=`：钉住把树按住时两者不同，那一格只打一行让人去 dispatch，不发布，否则上游一动就把回滚顶掉。五个方向各打印一行 `behind <包>=<方向>`（`upstream` / `unpublished` / `checksums` / `retired` / `no-release`），一次运行因此说得清自己为什么要发。只有第二个方向说得清「重建」：pkgver 没变，变了的是 pkgrel。它另答一条前置条件——已渲染的 PKGBUILD 还是不是它模板填上值的结果（`values_of`），不是就报错，而不是报「落后」。这里的「上游」指它最新的、SHA256SUMS 已经传齐的那个版本——表在，而且列着本仓库要打的每一个资产：tag 与资产不一起到，表也可能只列了一半（上传没完，或者上游改了名），两种都打包不了，所以既不算落后，也进不了渲染，入口检查会去要更早的那个完整版本，一个都不齐就按名拒绝。软件源读不到（这次运行没声明地址，或者 Pages 出错）时只剩上游那一半：日志打 `compared=upstream`，原因在它上面一行；404 是「还没有发布过」，仍算落后。三个名字（`PAGES_URL` / `REPO_NAME` / `ARCH`）只声明两个是配置错误，按名拒绝，不是「手跑就没地址」。
_Avoid_: 过期、不同步

**软件源**：
gh-pages 上 `x86_64/` 目录与 `magpie.db` 合起来的东西，pacman 的 `[magpie]` 段指向它。目录按 arch 分，今天只有一个：workflow 的 `ARCH`（目录名）、`REPO_NAME`（节名，也就是 db 的文件名）、`TREE`（这次运行组装它的那棵树）与 `PAGES_BRANCH`（它被服务的分支）是这几个名字唯一的出处，`REPO_NAME`、`TREE` 与 `PAGES_BRANCH` 还跨进容器或脚本交给下一步：播种读分支名，`publish-pages.sh` 收它做 argv，两处没有第二个拼法。用户可见的安装段有两份抄写，都不许自己拼名字：README 的安装段由人敲，`tests/test_inventory.py` 逐字对账；Release 说明里那一段由 `release.sh` 用 `ARCH`/`REPO_NAME`/`PAGES_URL` 生成，`tests/test_release.py` 用同一个读者（`tests/install_block.py`）对账。arch 表管的是另一半（上游资产后缀）。
_Avoid_: 仓库（会和 git 仓库混淆）

**保留窗口**：
软件源里每个包同时保留的版本数，`KEEP=2`。旧版本只给刚同步过旧 db 的客户端兜底，db 本身只列当前版。数的是版本不是文件：同一个版本的每个 arch 一起留、一起丢，db 里每个 arch 一条。窗口锚在这一趟发布的构建上，不是目录里最大的版本：它自己与它下面 `KEEP-1` 个版本留下，比它新的也走——按最大值数会让一次回滚（`WANTED` 钉住一个旧版本）把自己刚打出来的包删掉，Release 便什么也找不到。窗口的账上只有包清单里还有的包——从清单删掉的包不会被构建，也就没有谁会数到它，所以 `build-repo.sh` 先问这棵树里有哪些包（`pkgfiles_packages`），这次没构建的一律清掉。包清单是唯一的账本，不是目录里的现状。
_Avoid_: 历史版本、归档

**包文件**：
软件源 `x86_64/` 里的 `.pkg.tar.zst`，名字恒为 `pkgname-pkgver-pkgrel-arch`，由 makepkg 决定。排序、保留窗口、db 条目读的是同一个顺序，这三个判断都由 `scripts/lib/pkg.sh` 回答：`pkgfile_version` 读 `<pkgver>`（保留窗口的计数单位），`pkgfile_evr` 读 `<pkgver>-<pkgrel>`（db 条目与 Release 说的是这一次构建——同一个版本重建之后，两者才分得开）。「什么算包文件」只有一个拼写：`pkgfile_pattern`（后缀是 `pkgfile_suffix`），`pkgfiles_all` 用它扫目录，workflow 播种 gh-pages 的 `tar --wildcards` 也问它——所以带过来的树与被清理的树是同一个集合，`.sig` 之类不会混进 `repo-add`。与它并排的另一个集合是残留（`pkgfile_residue_pattern` 与 `pkgfile_leftovers`），两者故意不同宽。
_Avoid_: 安装包、构建产物

**残留**：
包目录里构建留下的、但不是包文件的东西：makepkg 的工作树（`src/`、`pkg/`、`.srcdest/`）、旧 checkout 里还躺着的 `.SRCINFO`，以及 `pkgfile_residue_pattern`（`*.pkg.tar.*`）匹配的形状——哪天签名落地会写出的 `.sig`、旧默认值写出的 `.xz`。两组名字各由一个函数回答：`pkgfile_residue_pattern` 是那个 glob，`pkgfile_leftovers` 是那四棵工作树与 `.SRCINFO` 的名字（名字不是 glob 能问出来的）。`build-check.sh` 开工前清一遍、退出时清一遍，交出目录之前还断言里面只剩 `PKGBUILD`、本次的包文件与那组工作树：`packages/<包>/` 是提交进仓库的，publish 最后一步的 `git add packages` 会把谁都带上。`.gitignore` 覆盖同一个集合（两组都由测试钉着），为的是 trap 来不及跑的那种死法。
_Avoid_: 垃圾文件、临时文件

**载荷核对**：
包文件里那个程序是不是这次要发的版本：`usr/bin/` 下唯一的成员，跑它的 `--version`，第一个点分数字必须**等于** `pkgver`。由 `scripts/lib/pkg.sh` 回答，容器里用 `bsdtar`，离线测试用 GNU `tar`（`PAYLOAD_TAR` 是那个 seam）。
_Avoid_: 冒烟测试、二进制检查

**builder**：
容器里跑 makepkg 的非特权账户——makepkg 拒绝以 root 运行。
