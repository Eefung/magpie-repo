# HANDOFF — magpie-repo（自托管 pacman 源）

给下一个人（或下一次的我）接手用。写作时间 2026-11-11 15:20 CST，文中每个数字都是当时实测的，重跑方法见最后一节。

## 一句话

每天检查上游 [magpie](https://usemagpie.ai) 的新版本，把它打成 `magpie-bin`（桌面，GTK3 + WebKitGTK 4.1）和 `magpie-cli-bin`（静态终端版）两个 Arch 包，发布成自托管 pacman 仓库（GitHub Pages）与 GitHub Release；客户端 `pacman -Syu` 即可升级。

## 当前状态

| 项 | 值 |
|---|---|
| 代码仓库 | <https://github.com/Eefung/magpie-repo>，本地 `~/Projects/magpie-repo`，分支 `main`（写作时 tip `7a35fc3 upgpkg: magpie 0.1.3711`，本文档为其后的一个提交） |
| 已发布版本 | `v0.1.3711`（gh-pages 的 `magpie.db` 里有 2 个包，Release 同名） |
| 上游最新 | `0.1.3758`（`bump.py --check` 输出，上游一天几十版，这个数字很快会变） |
| 本机安装 | `magpie-bin 0.1.3711-1`、`magpie-cli-bin 0.1.3711-1`，来自仓库 `[magpie]`，`pacman -Qu` 为 0 |
| 软件源 | `https://eefung.github.io/magpie-repo/x86_64`（`magpie.db` 200，`SHA256SUMS` 200） |
| Pages | 源分支 `gh-pages`、路径 `/`、状态 `built` |
| Actions | 计划 `17 4 * * *`（每天 04:17 UTC），另在 push 到 `main`（限 `templates/**`、`scripts/**`、workflow 本身）与手动 dispatch 时触发；最近 8 次运行全 success |
| Secrets / Variables | **空**。`AUR_SSH_PRIVATE_KEY` 没配，所以 AUR 推送步骤始终跳过 |
| 包体积 | `magpie-bin` 34,158,349 B / `magpie-cli-bin` 32,062,255 B（0.1.3711，x86_64，zstd） |
| 保留策略 | 每个包保留最近 2 个版本（`KEEP=2`） |

## 为什么不是 AUR

- AUR 暂停了新用户注册，拿不到写权限。
- `magpie-bin`、`magpie-cli-bin` 这两个名字在 AUR 上已存在且有活跃维护者（czyt），AUR 规则明确禁止建重复包，所以不能换个名字绕开。

结论：自己托管软件源，pacman 的升级体验与 AUR 版一致。AUR 那条路**没有删**，代码（`scripts/publish.sh`）和文档（`docs/aur-setup.md`）都在，注册恢复或拿到 co-maintainer 权限后配上 secret 即自动生效，不需要改工作流。

## 数据流

```
cron 04:17 UTC / push / workflow_dispatch
  └─ scripts/bump.py --check        上游最新版本（GitHub releases/api == 站点 /api/latest 的兜底）
       ├─ 与 packages/*/PKGBUILD 的 pkgver 相同 → 结束（"nothing to publish"）
       └─ 有新版本 → scripts/bump.py --render
            ├─ 二进制校验和：该版本 release 的 SHA256SUMS 资产
            ├─ LICENSE、图标：每次按实际字节现算 sha256
            ├─ 渲染 templates/*.PKGBUILD.in → packages/<pkg>/PKGBUILD
            ├─ docker run archlinux:base-devel scripts/build-repo.sh
            │    build-check.sh：makepkg（校验 sha256 + 打包）→ 生成 .SRCINFO
            │                    → 装该包自己的 depends → `<bin> --version` 验证 → timeout 90
            │    build-repo.sh：拷包、裁剪到 KEEP 版、repo-add 重建 magpie.db/.files、写 SHA256SUMS
            ├─ 提交 packages/ 回 main
            ├─ 力推 gh-pages（单提交；先 git archive 取上一版，保证保留窗口）
            ├─ 建 Release v<ver>：只挂本版两个包 + 本版 SHA256SUMS
            └─ 若存在 AUR_SSH_PRIVATE_KEY：scripts/publish.sh 再推 AUR
```

## 仓库结构

| 路径 | 作用 |
|---|---|
| `templates/magpie-{bin,cli-bin}.PKGBUILD.in` | 打包逻辑唯一来源，`{{pkgver}}`、`{{sha_*}}`、`{{maintainer}}` 占位 |
| `packages/<pkg>/PKGBUILD` `.SRCINFO` | 渲染结果；目录名 = pkgname |
| `scripts/bump.py` | 查版本、取校验和、渲染（仅标准库，可直接在 CI 的 py3 跑） |
| `scripts/build-check.sh` | 构建单包并验证；root 下自动 `useradd builder` 降权，装 depends 后跑 `--version` |
| `scripts/build-repo.sh` | 构建 + 组装仓库（`REPO_NAME=magpie`、`KEEP=2`） |
| `scripts/publish.sh` | 推 AUR（备用）；`AUR_BASE` 可指向本地裸仓库测试 |
| `.github/workflows/publish.yml` | 唯一流水线 |
| `docs/pacman-repo.md` | 用户侧：装、验签说明、排错、退回 AUR |
| `docs/aur-setup.md` | AUR 路径（注册、密钥、共同维护者申请、失败对照表） |

## 日常操作

```bash
# 客户端（本机已配好）
sudo pacman -Syu

# 立刻发一版（不用等 cron）
gh workflow run publish.yml --repo Eefung/magpie-repo
gh run watch --repo Eefung/magpie-repo

# 重发当前版本（version 填当前 pkgver：跳过提交，但仓库/Release 会重建）
gh workflow run publish.yml --repo Eefung/magpie-repo -f version=0.1.3711

# 本地检查与构建
cd ~/Projects/magpie-repo
python3 scripts/bump.py --check
python3 scripts/bump.py --render --version 0.1.3711
./scripts/build-check.sh packages/magpie-bin
./scripts/build-repo.sh /tmp/repo/x86_64 packages/magpie-bin packages/magpie-cli-bin
```

## 本机已有的改动（别人接手这台机器需要知道）

- `/etc/pacman.conf` 末尾追加了：
  ```
  [magpie]
  SigLevel = Optional TrustAll
  Server = https://eefung.github.io/magpie-repo/x86_64
  ```
  原始备份：`/etc/pacman.conf.bak-20261111-150739`。
- `magpie-bin`、`magpie-cli-bin` 由该源安装，`/usr/bin/magpie`（81 MB 左右）与 `/usr/bin/magpie-cli`（74 MB 左右）都在；`~/.local/bin` 里**没有**上游 `install.sh` 装的自更新副本，不存在两份打架。
- `/usr/share/applications/magpie.desktop` 的 `Categories=Development;`（单主分类，已修）。
- 想回退：注释掉那三行 → `sudo pacman -Syu` → 需要时 `paru -S magpie-bin`（版本号会回退，需 `pacman -U` 或 `--ignore` 处理降级）。

## 设计决策与理由

| 决策 | 理由 |
|---|---|
| 每天一次而不是每版一次 | 上游一天 30+ 个构建；每版要重推 60 MB，逐版追没有价值 |
| 每包保留 2 版 | 升级途中不至于 404；再多就是给 gh-pages 加负担 |
| gh-pages 单提交力推 | 每版 60 MB，保留历史会把分支撑到几个 GB |
| 未签名，`SigLevel = Optional TrustAll` | 签名要长期持有 GPG 私钥并做 `pacman-key` 分发；当前完整性靠 db 内 sha256 + Release 的 `SHA256SUMS`，信任对象是 GitHub 仓库 |
| 只构建/验证 x86_64 | PKGBUILD 仍声明 aarch64，靠上游 `SHA256SUMS` 保证，但 CI 不跑 ARM |
| 校验和取自 release 的 `SHA256SUMS` | 与站点 `/api/latest` 的 digest、以及 AUR 人工维护者钉的 shasum 三处交叉验证过 |
| 渲染模板而不是补丁式改 PKGBUILD | 幂等、diff 可审、逻辑只有一份 |
| `pkgver` 变才发布，`pkgrel` 恒为 1 | AUR 与仓库都靠 pkgver 识别升级；上游版本号单调递增，够用 |

## 已知限制

- **无签名**：`TrustAll` 是明示取舍，用户文档里写清楚了。
- GitHub Pages 站点上限 1 GB、单文件 100 MB；当前每版 60 MB，理论上 3 版左右就见底，所以 `KEEP=2` 不能随便调大。
- 定时任务只是尽力而为；仓库 60 天无活动 GitHub 会停定时工作流（这里每次发布都有提交，正常不会触发）。
- 上游个别 release 缺 `SHA256SUMS`（例如 `v0.1.3706`）——`bump.py` 现在会明确报错，需要换版本重试。
- 本机 `pacman -Syu` 之外不要用 `-Sy` 单独装包（局部升级），除非只装这两个 `-bin` 包。
- AUR 注册关闭是外部事实（写作时），恢复时间未知。

## 本次会话踩过的坑

| 现象 | 原因 | 修法（提交） |
|---|---|---|
| `makepkg`: `Failed to change to directory /tmp/…: Permission denied` | root 先建了 SRCDEST，`chown -R builder` 覆盖不到 | SRCDEST 挪进包目录 `.srcdest`（`ed5adfe`） |
| 容器里 `libwayland-client.so.0` 缺失，二进制跑不起来 | 裸 `archlinux:base-devel` 没有 GTK 运行时 | 从 `.SRCINFO` 读 `depends` 装上再跑 `--version`，顺带验证依赖表完整（`04a4aba`） |
| runner 删不掉 builder 建的产物 | 属主是容器 uid 1000 | 容器内 root 清理后 `chown -R "$(stat -c %u /src)"`（`5e5ebde`） |
| `! [rejected] main -> main (fetch first)` | 运行期间有人手推 main | `git push \|\| { git pull --rebase --autostash && git push; }`（`f9be0ea`） |
| 手动重发当前版本时任务挂在提交步骤 | 无改动 → 空提交失败 | `git diff --cached --quiet` 守卫（`c145904`） |
| `--render --version 0.1.3706` 抛 traceback | 该 release 没有 `SHA256SUMS` | 明确报错并提示换版本（`8ef54e5`） |
| 应用菜单可能出现两条 | `Categories=Development;Utility;` 两个主分类 | 改为 `Categories=Development;`（`33055b0`），已在 0.1.3711 包里生效 |
| 每个 Release 里混进上一版的包 | 挂的是 `repo/x86_64/*.pkg.tar.zst`，而该目录按保留策略装着两版 | 只挂 `*-$ver-1-x86_64.pkg.tar.zst`，并为它单独生成 `SHA256SUMS`（本文档所在提交）。注意 `v0.1.3709`、`v0.1.3711` 两个旧 Release 仍带多余资产，不影响安装 |

## 待办（按价值排序）

1. 想给 `pacman -S` 一个真正可信的签名：生成 GPG 密钥 → 私钥进 secret → CI 里 `makepkg --sign` / `repo-add --sign` → 文档换成 `pacman-key --add` 流程。这是唯一还算重要的缺口。
2. aarch64 构建（GitHub 已提供 ARM runner），需要 matrix 与环境变量调整。
3. AUR 注册恢复后：配 `AUR_SSH_PRIVATE_KEY`（+ 可选 `AUR_KNOWN_HOSTS`），或先按 `docs/aur-setup.md` 申请 co-maintainer。
4. 可选：`pkgrel` 递增能力（现在恒为 1，纯打包修正无法单独发版，得等下一个上游版本）。

## 参考

- 上游站点 <https://usemagpie.ai>，发布仓库 `yetone/magpie-releases`，`/api/latest` 给出当前版本与各产物的 sha256
- 用户侧文档 `docs/pacman-repo.md`；AUR 路径 `docs/aur-setup.md`
- 技能：`aur-package-vetting`（判断某个包是否新鲜可用）、`aur-auto-bump-workflow`（打包自动化的通用做法与本项目踩的坑）

## 下次接手的验证清单

```bash
cd ~/Projects/magpie-repo && git pull
python3 scripts/bump.py --check                       # upstream / packaged / bump
gh run list --limit 5 --repo Eefung/magpie-repo       # 最近运行是否 success
curl -s -o /dev/null -w '%{http_code}\n' https://eefung.github.io/magpie-repo/x86_64/magpie.db
pacman -Sl magpie                                     # 本机看到的版本
pacman -Q magpie-bin magpie-cli-bin
/usr/bin/magpie --version && /usr/bin/magpie-cli --version
```

`pacman -Sl magpie` 的版本落后于 `bump.py --check` 的 upstream 是正常的（最多一天）。如果落后超过一天，看 Actions 是否失败，或手动 `gh workflow run publish.yml`。
