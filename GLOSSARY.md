# magpie-repo

把上游 magpie 的发版重打成 Arch 包，再自托管成一个 pacman 软件源。本仓库不含上游代码，只负责同步、打包与发布。

## Language

**上游**：
yetone/magpie 项目以及它发布的构建产物；本仓库只读取，不修改。
_Avoid_: 官方、源仓库

**版本**：
上游 release 的 tag 去掉 `v`，形如 `0.1.3995`，只由数字和点组成。这是渲染前强制的那条不变量。
_Avoid_: 版本号、tag

**包清单**：
本仓库发布的包组成的集合——目录名，以及它们在上游资产名里的前缀。渲染、`--check` 与打包都从它取，`--packages` 是它唯一的出口：一行一个包，`package <相对仓库根的目录> <pkgname>`。目录给打包步骤，`pkgname` 给发布步骤，两者都不在 workflow 里出现第二次。`pkgname` 同时是包的目录名、清单的 key 与模板里 `pkgname=` 的字面值：渲染期把模板钉到 key 上，不等就不写盘；渲染出的 PKGBUILD 又由 `tests/test_templates.py` 钉在模板上，所以 `build-check` 从 PKGBUILD 读出的 `pkgname=`（作为冻结行交出）与发布步骤手里的 key 是同一个值。
_Avoid_: 包列表、packages 变量

**冻结行**：
生产者打印一次、读者不再回头解析的 `name=value` 行：`scripts/upstream.sh` 的 `upstream=` / `target=` / `bump=` / `packages=`、`--render` 的 `ver=`、`--packages` 的 `package <目录> <pkgname>`、`build-check` 的 `pkgname=` / `artifact=`、`release.sh` 的 `assets=` / `sums=` / `notes=`。一条行算不算冻结行由 grep 决定，不由它长得像不像：`build-check` 的 `pkgver=` 曾被列在这里，而它唯一的读者是一条测试。读它只走 `scripts/lib/pkg.sh` 的三个 reader，不由每个调用方自己 split：`line_field`（重复一行是协议断了、空值算失败、缺行也失败）、`line_field_or`（缺行给调用方指定的默认值，但绝不把「写了两行」当成缺行）与 `pkglist_field`。措辞是契约的那几条之外，其余是日志。
_Avoid_: 输出格式、stdout

**入口检查**：
每次运行的第一步：问上游最新、核对这棵树（已提交的 PKGBUILD 还是不是它模板的渲染），并决定这次要不要发布、发哪个版本。三件事都在 `scripts/upstream.sh` 一处回答，它的四条冻结行就是这个答案——`bump=` 是结论，`target=` 是渲染要拿的版本，`packages=` 是包清单的文件。workflow 的 step 只中继，不再自己判：判断写在 YAML 里时，唯一能守它的是对 YAML 文本的 grep。
_Avoid_: 检查步骤、pre-check

**arch 表**：
`ARCHES`——本仓库发布的 arch 与上游资产名后缀的对应（`x86_64` 对 `linux-amd64`）。模板里 `arch=()`、`source_<arch>=`（文件名后缀与下载 URL 两处）、`sha256sums_<arch>=` 与 `package()` 的 `case` 四处都受它约束：加一个 arch 是改这张表，不是给某个模板开例外。渲染前的 `check_arches` 逐条比，不是查子串。表里只有构建并核对过的 arch：渲染会为表里每个 arch 去上游要资产，要不到就整次渲染失败，所以一个不发布的 arch 等于握着发布的开关（ADR-0003）。
_Avoid_: 架构列表、ARCHES 变量

**渲染**：
把 `templates/<包>.PKGBUILD.in` 与算好的校验和填成 `packages/<包>/PKGBUILD`。写盘之前的所有判定——版本合法、校验和齐备、模板与值互相对得上——都属于渲染。`values_of` 是它的逆：把已渲染的文件对回模板，交出被填进占位符的值。
_Avoid_: 生成、打包

**打包**：
用 makepkg 把渲染好的 PKGBUILD 变成 `.pkg.tar.zst`。这些是 `-bin` 包，只下载上游二进制再重新封装。
_Avoid_: 编译

**落后**：
已渲染的版本与上游不一致。这是 `--check` 的主问题；它另答一条前置条件——已渲染的 PKGBUILD 还是不是它模板填上值的结果（`values_of`），不是就报错，而不是报「落后」。这里的「上游」指它最新的、SHA256SUMS 已经传好的那个版本：tag 与资产不一起到，打包只能从取得到的东西开始，所以只打了 tag 的版本既不算落后，也进不了渲染。
_Avoid_: 过期、不同步

**软件源**：
gh-pages 上 `x86_64/` 目录与 `magpie.db` 合起来的东西，pacman 的 `[magpie]` 段指向它。目录按 arch 分，今天只有一个：workflow 的 `ARCH`（目录名）与 `REPO_NAME`（节名，也就是 db 的文件名）是这两个名字唯一的出处，`REPO_NAME` 还跨进容器交给 `build-repo.sh`。README 的安装段是同一组事实的第四份抄写，也是用户唯一照着敲的那份，由 `tests/test_inventory.py` 逐字对账。arch 表 管的是另一半（上游资产后缀）。
_Avoid_: 仓库（会和 git 仓库混淆）

**保留窗口**：
软件源里每个包同时保留的版本数，`KEEP=2`。旧版本只给刚同步过旧 db 的客户端兜底，db 本身只列当前版。数的是版本不是文件：同一个版本的每个 arch 一起留、一起丢，db 里每个 arch 一条。窗口的账上只有包清单里还有的包——从清单删掉的包不会被构建，也就没有谁会数到它，所以 `build-repo.sh` 先问这棵树里有哪些包（`pkgfiles_packages`），这次没构建的一律清掉。包清单是唯一的账本，不是目录里的现状。
_Avoid_: 历史版本、归档

**包文件**：
软件源 `x86_64/` 里的 `.pkg.tar.zst`，名字恒为 `pkgname-pkgver-pkgrel-arch`，由 makepkg 决定。排序、保留窗口、db 条目读的是同一个顺序，这三个判断都由 `scripts/lib/pkg.sh` 回答。「什么算包文件」只有一个拼写：`pkgfile_pattern`（后缀是 `pkgfile_suffix`），`pkgfiles_all` 用它扫目录，workflow 播种 gh-pages 的 `tar --wildcards` 也问它——所以带过来的树与被清理的树是同一个集合，`.sig` 之类不会混进 `repo-add`。与它并排的另一个集合是残留（`pkgfile_residue_pattern`），两者故意不同宽。
_Avoid_: 安装包、构建产物

**残留**：
包目录里构建留下的、但不是包文件的东西：makepkg 的工作树（`src/`、`pkg/`、`.srcdest/`）、旧 checkout 里还躺着的 `.SRCINFO`，以及 `pkgfile_residue_pattern`（`*.pkg.tar.*`）匹配的形状——哪天签名落地会写出的 `.sig`、旧默认值写出的 `.xz`。`build-check.sh` 开工前清一遍、退出时清一遍，交出目录之前还断言里面只剩 `PKGBUILD`、本次的包文件与那组工作树：`packages/<包>/` 是提交进仓库的，publish 最后一步的 `git add packages` 会把谁都带上。`.gitignore` 覆盖同一个集合，为的是 trap 来不及跑的那种死法。
_Avoid_: 垃圾文件、临时文件

**载荷核对**：
包文件里那个程序是不是这次要发的版本：`usr/bin/` 下唯一的成员，跑它的 `--version`，第一个点分数字必须**等于** `pkgver`。由 `scripts/lib/pkg.sh` 回答，容器里用 `bsdtar`，离线测试用 GNU `tar`（`PAYLOAD_TAR` 是那个 seam）。
_Avoid_: 冒烟测试、二进制检查

**builder**：
容器里跑 makepkg 的非特权账户——makepkg 拒绝以 root 运行。
