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
本仓库发布的包组成的集合——目录名，以及它们在上游资产名里的前缀。渲染、`--check` 与打包都从它取，`--packages` 是它唯一的出口，所以包名不在 workflow 里出现第二次。
_Avoid_: 包列表、packages 变量

**渲染**：
把 `templates/<包>.PKGBUILD.in` 与算好的校验和填成 `packages/<包>/PKGBUILD`。写盘之前的所有判定——版本合法、校验和齐备、模板与值互相对得上——都属于渲染。
_Avoid_: 生成、打包

**打包**：
用 makepkg 把渲染好的 PKGBUILD 变成 `.pkg.tar.zst`。这些是 `-bin` 包，只下载上游二进制再重新封装。
_Avoid_: 编译

**落后**：
已渲染的版本与上游不一致。`--check` 只回答这一件事。
_Avoid_: 过期、不同步

**软件源**：
gh-pages 上 `x86_64/` 目录与 `magpie.db` 合起来的东西，pacman 的 `[magpie]` 段指向它。
_Avoid_: 仓库（会和 git 仓库混淆）

**保留窗口**：
软件源里每个包同时保留的版本数，`KEEP=2`。旧版本只给刚同步过旧 db 的客户端兜底，db 本身只列当前版。
_Avoid_: 历史版本、归档

**包文件**：
软件源 `x86_64/` 里的 `.pkg.tar.zst`，名字恒为 `pkgname-pkgver-pkgrel-arch`，由 makepkg 决定。排序、保留窗口、db 条目读的是同一个顺序，这三个判断都由 `scripts/lib/pkg.sh` 回答。
_Avoid_: 安装包、构建产物

**builder**：
容器里跑 makepkg 的非特权账户——makepkg 拒绝以 root 运行。
