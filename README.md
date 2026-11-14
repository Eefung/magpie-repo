# magpie 自托管 pacman 源

把上游 [magpie](https://usemagpie.ai) 的新版本自动打成 Arch 包，装好之后用 `pacman -Syu` 升级。

## 原理

Actions 每天 04:17 UTC 跑一次，有新版本才发布：

```text
scripts/bump.py --check   GitHub releases 与 usemagpie.ai/api/latest 较高者为上游
  └ 如果与 packages/*/PKGBUILD 的 pkgver 不同 → scripts/bump.py --render
      checksum 取 release 的 SHA256SUMS，LICENSE 与图标下载后现算 → 生成 PKGBUILD
      scripts/build-repo.sh：makepkg 打包 → 安装该包的依赖项 → 跑 --version → repo-add 重建 db
      强制推送到 gh-pages 分支（每个软件包保留 2 个版本）→ 发布 Release v<版本> → 把 PKGBUILD 提交回 main
```

`gh-pages` 上的 `x86_64/` 就是软件源。先发布后提交：中途失败时 main 上没有新 `pkgver`，下次运行重做。PKGBUILD 里还写着 aarch64，但 CI 不构建也不验证。

## 安装

```bash
# /etc/pacman.conf 末尾
[magpie]
SigLevel = Optional
Server = https://eefung.github.io/magpie-repo/x86_64
```

```bash
sudo pacman -Syu
sudo pacman -S magpie-bin        # 桌面版（GTK3 + WebKitGTK 4.1）
sudo pacman -S magpie-cli-bin    # 终端版（静态，无 GUI 依赖），可与桌面版并存
```

包没有 GPG 签名，所以 `SigLevel = Optional` 就够：缺签名时放行，有签名时仍要求可信。`TrustAll` 是多余的放宽，在这个源上用不上。为什么不签见 [ADR-0002](docs/adr/0002-unsigned-repo-trust-falls-to-github.md)。

桌面版的 `provides` 与 `conflicts` 都写成了 `magpie`，因此和同名包不能共存。

卸载：`sudo pacman -Rns magpie-bin magpie-cli-bin`，再删掉 `pacman.conf` 里那三行。

## 用法

```bash
python3 scripts/bump.py --check                     # upstream=… / packaged … / bump=0|1
python3 scripts/bump.py --render [--version <版本>]  # 生成 PKGBUILD，默认上游最新
python3 -m unittest discover -s tests               # 离线单测，不联网也不进容器
./scripts/build-check.sh packages/magpie-bin        # 单包构建，装依赖后跑 --version 并核对版本号

./scripts/build-repo.sh /tmp/magpie-repo/x86_64 packages/magpie-bin packages/magpie-cli-bin
python3 -m http.server -d /tmp/magpie-repo 8765     # 再用临时 pacman.conf 从 http 试装

gh workflow run publish.yml                         # 立刻发布，version 留空＝上游最新
```

## 问题

- **有 GPG 签名吗？**

  没有，这是 [ADR-0002](docs/adr/0002-unsigned-repo-trust-falls-to-github.md) 的选择。一次性容器不是障碍——私钥放 secret、step 里导入临时 `GNUPGHOME` 就能签。拦路的是客户端：用户必须先 `pacman-key --lsign-key` 认下那把 key，否则 `SigLevel` 写什么都不通过。

  能保证的是「下载到的字节与 `magpie.db` 里的 sha256 一致」，防传输与存储损坏；防不了源被篡改，因为 db 与包同源，Release 的 `SHA256SUMS` 也不是独立信道。源的可信度目前由 GitHub Pages 的 TLS 与仓库写权限承担。完整取证见 [`docs/research/pacman-repo-signing.md`](docs/research/pacman-repo-signing.md)。

- **仓库里有什么？**

  | 有 | 无 |
  | --- | --- |
  | `x86_64` 的 `magpie-bin`、`magpie-cli-bin` | aarch64 包 |
  | 当前版与上一版的文件（给刚同步过旧 db 的客户端兜底；db 里只有当前版） | 历史版本、降级安装 |
  | 与上游同步的 `depends`、桌面文件、图标 | 签名、`-git` 变体 |

- **error: database file for 'magpie' does not exist**

  `Server` 写错或仓库名与 db 文件名不一致，先手动确认：

  ```bash
  curl -sI https://eefung.github.io/magpie-repo/x86_64/magpie.db | head -1   # 期望 200
  ```

- **failed retrieving file 'magpie.db' ... 404**

  Pages 刚发布还在构建，或者 [Actions](https://github.com/Eefung/magpie-repo/actions) 失败了。

- **`key "..." is unknown` 或 `signature from "..." is unknown trust`**

  这个源不签名，正常安装不会出现这两条。先确认 `Server` 指向本仓库，再看 `[magpie]` 段是否继承了 `[options]` 里的 `Required`。注意 `TrustAll` 只放宽信任判定，不赦免「key 不在 keyring」，报 `key "..." is unknown` 时改 `SigLevel` 无效。

- **invalid or corrupted package**

  本机缓存了残缺的包：

  ```bash
  sudo rm -f /var/cache/pacman/pkg/magpie-*
  sudo pacman -Syy magpie-bin
  ```

- **版本比上游旧**

  `gh workflow run publish.yml --repo Eefung/magpie-repo` 立即构建；或从 [Release](https://github.com/Eefung/magpie-repo/releases) 下载 `.pkg.tar.zst`，用 `sudo pacman -U` 装。

## 文档

| 路径 | 作用 |
| --- | --- |
| `templates/*.PKGBUILD.in` | PKGBUILD 模板，`{{...}}` 由 `bump.py` 填 |
| `packages/<包名>/` | 生成结果，请勿手动修改 |
| `.github/workflows/publish.yml` | 发布流水线 |

## 许可

`scripts/` 与 `templates/` 是 MIT，见 [LICENSE](LICENSE)；打包的上游二进制按上游 MIT 分发。
