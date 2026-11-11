# magpie-aur

用 GitHub Actions 自动把上游 [magpie](https://usemagpie.ai) 的新版本打进 AUR：定时查版本 → 更新 PKGBUILD → 在干净的 Arch 容器里构建并验证 → 推送 `PKGBUILD` 与 `.SRCINFO`。

本仓库只做打包，不做编译：上游提供的是预编译二进制（`-bin` 包），一次 bump 只需要新版本号和它发布的校验和。

## 流程

```
每 3 小时（cron 23 */3 * * *）
  └─ scripts/bump.py --check      问上游最新版本（GitHub releases → 站点 /api/latest 兜底）
       ├─ 与 packages/*/PKGBUILD 里的 pkgver 相同 → 结束，什么都不推
       └─ 更新了 → scripts/bump.py --render
            ├─ 校验和取自该版本 release 的 SHA256SUMS（与站点 /api/latest 一致）
            ├─ LICENSE、图标按实际字节现算 sha256
            ├─ 生成 packages/magpie-bin/PKGBUILD、packages/magpie-cli-bin/PKGBUILD
            ├─ docker run archlinux:base-devel scripts/build-check.sh <包目录>
            │    makepkg -f --nodeps --nocheck（校验 sha256 + 打包）→ makepkg --printsrcinfo > .SRCINFO
            │    解包取出 usr/bin/magpie，跑 --version 确认就是新版本
            ├─ git commit「upgpkg: magpie <ver>」推回本仓库
            └─ scripts/publish.sh 把 PKGBUILD + .SRCINFO 推到 ssh://aur@aur.archlinux.org/<pkg>.git
```

没有配置 AUR 私钥时，构建与提交照常进行，只跳过最后一步推送（日志里给一条 warning）。

## 目录

| 路径 | 作用 |
|---|---|
| `templates/*.PKGBUILD.in` | PKGBUILD 模板，`{{pkgver}}`、`{{sha_*}}` 由脚本填充；要改打包逻辑改这里 |
| `packages/<包名>/` | 渲染结果，目录名就是 AUR pkgbase；`.SRCINFO` 由 `makepkg` 生成 |
| `scripts/bump.py` | 查上游版本、取校验和、渲染 PKGBUILD（仅用标准库） |
| `scripts/build-check.sh` | 构建一个包目录并验证产物（root 下自动降权到 builder，因为 makepkg 拒绝 root；装好 `depends` 再跑 `--version`，顺带验证依赖表是否完整） |
| `scripts/publish.sh` | 推送到 AUR；`AUR_BASE` 可改远端，方便拿本地裸仓库做测试 |
| `.github/workflows/aur.yml` | 定时与手动触发 |

## 本机使用

```bash
python3 scripts/bump.py --check                  # upstream=0.1.3702 / packaged ... / bump=0|1
python3 scripts/bump.py --render                 # 渲染到上游最新
python3 scripts/bump.py --render --version 0.1.3702   # 指定版本
./scripts/build-check.sh packages/magpie-bin     # 构建 + 验证（自动生成 .SRCINFO）
./scripts/publish.sh packages/magpie-bin packages/magpie-cli-bin   # 需要本机 ~/.ssh/aur
```

推之前先 `git add -A && git commit`，否则 `publish.sh` 推的是工作区里未提交的渲染结果——它只读文件，不看 git 历史。

## 启用推送需要的东西

1. 一个 AUR 账号，以及登记到账号里的 SSH 公钥——见 [docs/aur-setup.md](docs/aur-setup.md)。
2. 仓库 secret `AUR_SSH_PRIVATE_KEY`（对应私钥全文）。
3. 可选 secret `AUR_KNOWN_HOSTS`：钉住 `aur.archlinux.org` 的主机公钥。不设时工作流用 `ssh-keyscan` 现场取，方便但属于首次信任。
4. 可选 repository variable `AUR_GIT_NAME`／`AUR_GIT_EMAIL`：AUR 提交的作者信息，默认 `Eefung`／`eefung@tuta.io`。

> 这个仓库用到的两个包名 `magpie-bin`、`magpie-cli-bin` 在 AUR 上已有维护者（czyt）。AUR 规则不允许重复建包，所以在拿到推送权限之前，这个工作流只能把 PKGBUILD 更新在自己的仓库里。合规的两条路写在 [docs/aur-setup.md](docs/aur-setup.md)。

## 注意事项

- 上游一天发几十个版本，`pkgver` 每次 bump 都是新值；本工作流只在版本变化时提交，不看时间。
- GitHub 的 cron 只是尽力而为，可能延迟几分钟到几十小时；仓库 60 天没有任何活动时 GitHub 会停掉定时工作流（本仓库每次 bump 都在提交，正常情况下不会触发）。
- `arch=('x86_64' 'aarch64')`，CI 只验证 x86_64；aarch64 那份靠上游的 SHA256SUMS 保证。
- 桌面版 `provides`／`conflicts` 了同名包 `magpie`（IPv6 NDP 代理那个），两者不能共存；终端版装成 `magpie-cli`，可与桌面版并列。
