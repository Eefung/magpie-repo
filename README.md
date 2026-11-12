# magpie 自托管 pacman 源

自动把上游 [magpie](https://usemagpie.ai) 的新版本打成 Arch 软件包，发布成 **pacman 仓库 + GitHub Releases**，装完之后和任何官方包一样用 `pacman -Syu` 升级。

## 原理

每天 04:17 UTC 跑一次（也可以 `gh workflow run publish.yml` 手动触发）：

```
scripts/bump.py --check          问上游最新版本（GitHub releases → 站点 /api/latest 兜底），
   │                             并读一次线上 magpie.db，确认站点实际发布的是哪一版
   ├─ 与 packages/*/PKGBUILD 的 pkgver 相同、且站点也是这一版 → 结束，什么都不发
   └─ 有新版本，或站点落后于 packages/（上次发布中途失败）→ scripts/bump.py --render
        ├─ 二进制校验和取自该版本 release 的 SHA256SUMS，LICENSE 与图标按实际字节现算
        ├─ 渲染 packages/magpie-bin/PKGBUILD、packages/magpie-cli-bin/PKGBUILD
        ├─ docker run archlinux:base-devel scripts/build-repo.sh
        │    makepkg 校验 sha256 并打包 → 装好该包 depends 后跑 magpie --version
        │    repo-add 重建 magpie.db / magpie.files，写 SHA256SUMS
        ├─ 提交 PKGBUILD + .SRCINFO 回 main
        ├─ 力推 gh-pages（单次提交，每包留最近 2 个版本）→ GitHub Pages 当软件源
        └─ 建 GitHub Release v<版本>，附两个 .pkg.tar.zst 与 SHA256SUMS
```

产物落在两个地方：

| 位置 | 内容 | 谁用 |
|---|---|---|
| `gh-pages` → `https://eefung.github.io/magpie-repo/x86_64` | `magpie.db`、`magpie.files`、当前版 `.pkg.tar.zst`，外加上一版的文件 | `pacman -Syu` |
| GitHub Releases `v<版本>` | 同一次构建的两个包 + `SHA256SUMS` | 手动 `pacman -U`、核对校验和 |

几个刻意的取舍：

- **每天一次，不追版次。** 上游一天几十个构建，逐版追没有意义，所以最多滞后一天。
- **只验 `x86_64`。** PKGBUILD 里 `arch=('x86_64' 'aarch64')` 仍然成立，aarch64 那份靠上游 `SHA256SUMS` 保证，但 CI 不构建也不验证。
- **gh-pages 每次单提交力推。** 否则每版 65 MB 的包会把分支撑爆，历史不留。站点上每包留 2 个版本的文件（约 127 MB），但 `repo-add` 的 db 里每个包名只有一条记录（最新版）——留着上一版文件是为了让「刚同步完旧 db 的客户端」仍能下完包，不是让 pacman 能装旧版。
- **存储还有余量。** GitHub 单文件上限 100 MB，当前 `magpie-bin` 约 31 MB、`magpie-cli-bin` 约 30 MB；真超了就得换存储（对象存储或 Release 分片）。
- **`push` 到 main 也会跑一次**，但只有上游版本变了、或线上 `magpie.db` 落后于 `packages/` 时才会真的发布，否则只打印 `nothing to publish`。要强制重发用 `gh workflow run publish.yml`：`version` 留空就发上游最新，填当前 `pkgver` 也可以——提交步骤发现没有改动会跳过，仓库与 Release 照常重建。
- **cron 只是尽力而为**，可能延迟；仓库 60 天无活动会被停掉定时工作流（本仓库每次发布都在提交，正常不会触发）。

## 安装

```bash
# /etc/pacman.conf 末尾
[magpie]
SigLevel = Optional TrustAll
Server = https://eefung.github.io/magpie-repo/x86_64
```

```bash
sudo pacman -Syu
sudo pacman -S magpie-bin        # 桌面版
sudo pacman -S magpie-cli-bin    # 终端版（静态，可与桌面版并存）
```

包没有 GPG 签名，所以仓库必须 `SigLevel = Optional TrustAll`：完整性靠 db 里的 sha256 与 Release 附带的 `SHA256SUMS`。理由、验签、排错都在 [docs/pacman-repo.md](docs/pacman-repo.md)。

桌面版 `provides`／`conflicts` 了同名包 `magpie`（IPv6 NDP 代理那个），两者不能共存；终端版装成 `magpie-cli`，可与桌面版并列。

## 用法

本机跑同一套脚本（`bump.py` 只用标准库）：

```bash
python3 scripts/bump.py --check                  # upstream=… / packaged … / bump=0|1
python3 scripts/bump.py --render                 # 渲染到上游最新
./scripts/build-check.sh packages/magpie-bin     # 单包构建 + 验证（顺带生成 .SRCINFO）

# 组装仓库到本地目录，再用临时 pacman.conf 试着从 http 装
./scripts/build-repo.sh /tmp/magpie-repo/x86_64 packages/magpie-bin packages/magpie-cli-bin
python3 -m http.server -d /tmp/magpie-repo 8765
```

## 文档

| 路径 | 作用 |
|---|---|
| `templates/*.PKGBUILD.in` | PKGBUILD 模板，`{{pkgver}}`、`{{sha_*}}` 由脚本填；改打包逻辑改这里 |
| `packages/<包名>/` | 渲染结果，目录名 = pkgname |
| `scripts/bump.py` | 查上游版本、取校验和、渲染 PKGBUILD |
| `scripts/build-check.sh` | 构建一个包目录并验证产物（root 下自动降权到 builder，因为 makepkg 拒绝 root；装好 `depends` 再跑 `--version`，顺带验证依赖表是否完整） |
| `scripts/build-repo.sh` | 构建 + 组装 pacman 仓库：拷包、裁剪旧版、`repo-add` 重建 db、生成 SHA256SUMS |
| `.github/workflows/publish.yml` | 唯一的发布流水线 |
| `docs/pacman-repo.md` | 用户侧：装、验签、排错 |
| `docs/handoff.md` | 交接：当前状态、实测数字、复核方法 |

## 许可

本仓库的 `scripts/` 与 `templates/` 是 MIT，见 [LICENSE](LICENSE)。`magpie-bin`、`magpie-cli-bin` 打包的上游二进制按上游自己的许可（MIT）分发，构建时现取上游 LICENSE 装进包。
