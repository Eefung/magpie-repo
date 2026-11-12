# magpie 源的信任模型与排错

安装见 [README](../README.md#安装)。

## 有和无

| 有 | 没有 |
|---|---|
| `x86_64` 的 `magpie-bin`、`magpie-cli-bin` | aarch64 包 |
| 当前版与上一版的文件（给刚同步过旧 db 的客户端兜底；db 里只有当前版） | 历史版本、降级安装 |
| 与上游同步的 `depends`、桌面文件、图标 | 签名、`-git` 变体 |

## 为什么 SigLevel 是 Optional TrustAll

包没有 GPG 签名：签名要维护者长期持有私钥，而 CI 跑在一次性容器里。完整性由 `magpie.db` 里的 sha256（pacman 下载后校验）与 Release 的 `SHA256SUMS` 保证，信任链条是「你信 GitHub 上的 `Eefung/magpie-repo`」。介意的话自己从源码构建，或用官方 `curl -fsSL https://usemagpie.ai/install.sh | sh`（装到 `~/.local/bin` 并自更新，不受 pacman 管理）。

## 排错

**`error: database file for 'magpie' does not exist`** —— `Server` 写错或仓库名与 db 文件名不一致：

```bash
curl -sI https://eefung.github.io/magpie-repo/x86_64/magpie.db | head -1   # 期望 200
```

**`failed retrieving file 'magpie.db' ... 404`** —— Pages 刚发布还在构建（几秒到一分钟），或那次 Actions 失败，看 <https://github.com/Eefung/magpie-repo/actions>。

**`signature from "..." is unknown trust`／`invalid or corrupted package`** —— `SigLevel` 不是 `Optional TrustAll`，或本机缓存了残缺的包：

```bash
sudo rm -f /var/cache/pacman/pkg/magpie-*
sudo pacman -Syy magpie-bin
```

**版本比上游旧** —— `gh workflow run publish.yml --repo Eefung/magpie-repo` 立刻构建，或从 <https://github.com/Eefung/magpie-repo/releases> 下包 `sudo pacman -U`。
