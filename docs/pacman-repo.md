# 用这个仓库装 magpie

这是 `magpie-repo` 自己托管的 pacman 仓库，装完之后的升级和官方包一样走 `pacman -Syu`，不需要 AUR、不需要谁有 AUR 账号。

## 一次配置

```bash
# /etc/pacman.conf 末尾追加
[magpie]
SigLevel = Optional TrustAll
Server = https://eefung.github.io/magpie-repo/x86_64
```

```bash
sudo pacman -Syu            # 同步所有源，也包括新加的 magpie
sudo pacman -S magpie-bin        # 桌面版（GTK3 + WebKitGTK 4.1）
sudo pacman -S magpie-cli-bin    # 只要终端版（静态，无 GUI 依赖），可和桌面版并存
```

两个包名与上游一致，装过的 AUR 版本会被直接升级，不需要先卸载。

之后升级就是 `sudo pacman -Syu`：仓库每天 04:17 UTC 由 Actions 重新构建一次，有新上游版本时才发布。

## 为什么 SigLevel 是 Optional TrustAll

这里的包没有做 GPG 签名（签名要求维护者长期持有一把私钥，而 CI 每次跑在一次性容器里）。完整性由两件事保证：

- `magpie.db` 里记录每个包自己的 sha256，pacman 下载后校验；
- `SHA256SUMS` 与本仓库 Releases 里附带的校验和一致。

也就是说，信任链条是「你信 GitHub 上的 `Eefung/magpie-repo`」而不是「你信一把长期密钥」。介意的话可以自己从源码构建，或者只用官方 `curl -fsSL https://usemagpie.ai/install.sh | sh`（它装到 `~/.local/bin` 并自更新，但不受 pacman 管理）。

## 这里有什么、没有什么

| 有 | 没有 |
|---|---|
| `x86_64` 的 `magpie-bin`、`magpie-cli-bin` | aarch64 包（PKGBUILD 支持，但 CI 只验 x86_64） |
| 当前版本，外加上一版的文件（给刚同步过旧 db 的客户端兜底；db 里只登记当前版本） | 更早的历史版本、降级安装 |
| 与上游同步的 `depends`、桌面文件、图标 | 签名、`-git` 变体 |

## 排错

**`error: database file for 'magpie' does not exist`** —— `pacman.conf` 里的 `Server` 写错，或者仓库名与 db 文件名不一致。先手动确认：

```bash
curl -sI https://eefung.github.io/magpie-repo/x86_64/magpie.db | head -1   # 期望 200
curl -s  https://eefung.github.io/magpie-repo/SHA256SUMS | head
```

**`error: failed retrieving file 'magpie.db' ... 404`** —— GitHub Pages 刚发布还在构建（几秒到一分钟），或者 Actions 那次运行失败了；看 <https://github.com/Eefung/magpie-repo/actions>。

**`signature from "..." is unknown trust` / `invalid or corrupted package`** —— `SigLevel` 没设成 `Optional TrustAll`，或者本机缓存了残缺的包：

```bash
sudo rm -f /var/cache/pacman/pkg/magpie-*
sudo pacman -Syy magpie-bin
```

**版本比上游旧** —— 这个仓库每天构建一次，最多滞后一天（AUR 的 `magpie-bin` 通常滞后两三天）。想立刻要新版：`gh workflow run publish.yml --repo Eefung/magpie-repo`，或直接去 <https://github.com/Eefung/magpie-repo/releases> 下 `pkg.tar.zst` 用 `sudo pacman -U` 装。

**想退回 AUR 版本** —— 把 `[magpie]` 那三行注释掉，`sudo pacman -Syu`，之后由 `paru -S magpie-bin` 接管（届时版本号可能更低，需要 `sudo pacman -U` 或 `--ignore` 处理降级）。

**彻底卸载** —— `sudo pacman -Rns magpie-bin magpie-cli-bin`，再删掉 `pacman.conf` 里那三行。
