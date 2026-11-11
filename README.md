# magpie-repo

自动把上游 [magpie](https://usemagpie.ai) 的新版本打成 Arch 软件包，发布成 **pacman 仓库 + GitHub Releases**，装完之后和任何官方包一样用 `pacman -Syu` 升级。

之所以不是 AUR：AUR 暂停了新用户注册，拿不到推送权限。这个仓库同样由包管理器托管，只是软件源换成了自己的 GitHub Pages。

## 用这个仓库装

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

验签说明、排错、退回 AUR 的做法都在 [docs/pacman-repo.md](docs/pacman-repo.md)。

## 每次运行做什么

```
每天 04:17 UTC（也可以 gh workflow run publish.yml 手动触发）
  └─ scripts/bump.py --check          问上游最新版本（GitHub releases → 站点 /api/latest 兜底）
       ├─ 与 packages/*/PKGBUILD 的 pkgver 相同 → 结束，什么都不发
       └─ 有新版本 → scripts/bump.py --render
            ├─ 二进制校验和取自该版本 release 的 SHA256SUMS，LICENSE 与图标按实际字节现算
            ├─ 渲染 packages/magpie-bin/PKGBUILD、packages/magpie-cli-bin/PKGBUILD
            ├─ docker run archlinux:base-devel scripts/build-repo.sh
            │    makepkg 校验 sha256 并打包 → 装好该包 depends 后跑 magpie --version
            │    repo-add 重建 magpie.db / magpie.files，写 SHA256SUMS
            ├─ 提交 PKGBUILD + .SRCINFO 回 main
            ├─ 力推 gh-pages（单次提交，每包留最近 2 个版本）→ GitHub Pages 当软件源
            ├─ 建 GitHub Release v<版本>，附两个 .pkg.tar.zst 与 SHA256SUMS
            └─ 若配了 AUR 私钥，再顺手推 AUR（当前注册关闭，属于备用路径）
```

## 目录

| 路径 | 作用 |
|---|---|
| `templates/*.PKGBUILD.in` | PKGBUILD 模板，`{{pkgver}}`、`{{sha_*}}` 由脚本填；改打包逻辑改这里 |
| `packages/<包名>/` | 渲染结果，目录名 = pkgname，仓库与 AUR 共用同一份 |
| `scripts/bump.py` | 查上游版本、取校验和、渲染 PKGBUILD（仅用标准库） |
| `scripts/build-check.sh` | 构建一个包目录并验证产物（root 下自动降权到 builder，因为 makepkg 拒绝 root；装好 `depends` 再跑 `--version`，顺带验证依赖表是否完整） |
| `scripts/build-repo.sh` | 构建 + 组装 pacman 仓库：拷包、裁剪旧版、`repo-add` 重建 db、生成 SHA256SUMS |
| `scripts/publish.sh` | 推 AUR 用；`AUR_BASE` 可改远端，方便拿本地裸仓库测试 |
| `.github/workflows/publish.yml` | 唯一的发布流水线 |
| `docs/pacman-repo.md` | 用户侧：装、验签、排错 |
| `docs/aur-setup.md` | AUR 那条路（注册恢复后才能用）的完整步骤 |

## 本机使用

```bash
python3 scripts/bump.py --check                  # upstream=… / packaged … / bump=0|1
python3 scripts/bump.py --render                 # 渲染到上游最新
./scripts/build-check.sh packages/magpie-bin     # 单包构建 + 验证（顺带生成 .SRCINFO）

# 组装仓库到本地目录，再用临时 pacman.conf 试着从 http 装
./scripts/build-repo.sh /tmp/magpie-repo/x86_64 packages/magpie-bin packages/magpie-cli-bin
cd /tmp/magpie-repo && python3 -m http.server 8765
```

## 发布到哪里

| 位置 | 内容 | 谁用 |
|---|---|---|
| `gh-pages` → `https://eefung.github.io/magpie-repo/x86_64` | `magpie.db`、`magpie.files`、最近 2 版 `.pkg.tar.zst` | `pacman -Syu` |
| GitHub Releases `v<版本>` | 同一次构建的两个包 + `SHA256SUMS` | 手动 `pacman -U`、核对校验和 |
| AUR `magpie-bin`／`magpie-cli-bin` | 配了 `AUR_SSH_PRIVATE_KEY` 才推 | 注册恢复、或成为共同维护者之后 |

## 注意事项

- 包**没有 GPG 签名**，所以仓库要 `SigLevel = Optional TrustAll`：完整性靠 db 里的 sha256 与 release 附带的 `SHA256SUMS`，信任对象是 GitHub 上的这个仓库。
- 只构建并验证 `x86_64`。PKGBUILD 里 `arch=('x86_64' 'aarch64')` 仍然成立，aarch64 那份靠上游 `SHA256SUMS` 保证，但 CI 不验证。
- 每天只发一次，最多滞后一天；上游一天几十个构建，逐版追也没有意义。
- gh-pages 每次都是单提交力推（否则每版 60 MB 的包会把分支撑爆），历史不保留，站点上只有每包最近 2 个版本。
- GitHub 单文件上限 100 MB：当前 `magpie-bin` 包约 31 MB、`magpie-cli-bin` 约 30 MB，还有余量；真超了就得换存储（对象存储或 Release 分片）。
- `push` 到 main 也会触发一次运行，但只有上游版本变了才会真的发布，否则只打印 `nothing to publish`；要强制重发用 `gh workflow run publish.yml`（`version` 参数填**新**版本，填当前版本会因为无改动而在提交步骤失败）。
- GitHub 的 cron 只是尽力而为，可能延迟；仓库 60 天无活动会被停掉定时工作流（本仓库每次发布都在提交，正常不会触发）。
- 桌面版 `provides`／`conflicts` 了同名包 `magpie`（IPv6 NDP 代理那个），两者不能共存；终端版装成 `magpie-cli`，可与桌面版并列。
