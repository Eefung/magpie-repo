# magpie 自托管 pacman 源

把上游 [magpie](https://usemagpie.ai) 的新版本自动打成 Arch 包，装好之后和 Arch 官方仓库的包一样用 `pacman -Syu` 升级。

## 原理

Actions 每天 04:17 UTC 跑一次，有新版本才发布：

```
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
SigLevel = Optional TrustAll
Server = https://eefung.github.io/magpie-repo/x86_64
```

```bash
sudo pacman -Syu
sudo pacman -S magpie-bin        # 桌面版（GTK3 + WebKitGTK 4.1）
sudo pacman -S magpie-cli-bin    # 终端版（静态，无 GUI 依赖），可与桌面版并存
```

包没有 GPG 签名，所以必须 `SigLevel = Optional TrustAll`，见 [docs/pacman-repo.md](docs/pacman-repo.md)。

桌面版的 `provides` 与 `conflicts` 都写成了 `magpie`（IPv6 NDP 代理那个），两者不能共存。

卸载：`sudo pacman -Rns magpie-bin magpie-cli-bin`，再删掉 `pacman.conf` 里那三行。

## 用法

```bash
python3 scripts/bump.py --check                     # upstream=… / packaged … / bump=0|1
python3 scripts/bump.py --render [--version <版本>]  # 生成 PKGBUILD，默认上游最新
./scripts/build-check.sh packages/magpie-bin        # 单包构建，安装该包的依赖项后跑 --version

./scripts/build-repo.sh /tmp/magpie-repo/x86_64 packages/magpie-bin packages/magpie-cli-bin
python3 -m http.server -d /tmp/magpie-repo 8765     # 再用临时 pacman.conf 从 http 试装

gh workflow run publish.yml                         # 立刻发布，version 留空＝上游最新
```

## 文档

| 路径 | 作用 |
|---|---|
| `templates/*.PKGBUILD.in` | PKGBUILD 模板，`{{...}}` 由 `bump.py` 填 |
| `packages/<包名>/` | 生成结果，请勿手动修改 |
| `.github/workflows/publish.yml` | 发布流水线 |
| `docs/pacman-repo.md` | 信任模型、有和无、排错 |

## 许可

`scripts/` 与 `templates/` 是 MIT，见 [LICENSE](LICENSE)；打包的上游二进制按上游 MIT 分发（构建时从上游下载 LICENSE）。
