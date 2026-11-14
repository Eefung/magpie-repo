# 0002 · 发布物不签名，信任落在 GitHub 与 TLS 上

状态：已采纳 · 2026-11-14

## 背景

`[magpie]` 源里的包与数据库都没有 GPG 签名。README 原本给的理由是「签名要维护者长期持有私钥，而 CI 跑在一次性容器里」——这条因果不成立，已由 `docs/research/pacman-repo-signing.md` 推翻。

一次性容器与「能不能签」无关：私钥放 secret、step 内导入临时 `GNUPGHOME`，`makepkg --sign` 签包、`repo-add --sign` 签库，容器寿命只决定私钥的暴露窗口。真正的门槛在客户端：pacman 的信任根是**客户端本地 keyring**，一把用户没见过的 key 撞上 `ALPM_SIGSTATUS_KEY_UNKNOWN` 就无条件 fatal，`TrustAll` 只放宽 marginal/unknown 的 trust，不放宽「key 不认识」。要让客户端接受，必须先 `pacman-key --add` 再 `--lsign-key`。

所以这件事的真实形状是一次权衡，不是一次不可能：签名能挡住什么、代价落在谁身上。

## 决定

不签名。包与数据库都不签，用户端 `SigLevel = Optional`。

签名能挡住的是一类具体威胁：**只能写 `gh-pages` 分支、写不了 `main` 的人**——凭据泄漏、Pages 构建链被劫持。这一层是真的，不是零。

代价则是一次性、不可省略、且落在每个用户身上：每台机器两个命令，还要自己确认指纹没被换过。在这个源的用户规模下，这条防线不值得让每个读者做一次带外引导。轮换更贵——新 key 对老客户端就是 unknown，换一次钥匙等于重做一次引导。

同时把 `TrustAll` 去掉。不签名时它一无所用，只会在将来出现签名时静默放行未受信的 key；`Optional` 与本源的现状等价，而且更严。

## 后果

- 完整性只到「下载到的字节与 `magpie.db` 里的 sha256 一致」，防传输与存储损坏。**不防源被篡改**：db 与包同源，Release 的 `SHA256SUMS` 也在 GitHub 上，不是独立信道。
- 源的真实性由 GitHub Pages 的 TLS 与 `Eefung/magpie-repo` 的写权限承担。比签名窄，但它明确、无需用户操作。
- 用户端必须显式写 `SigLevel = Optional`；不写会继承 `[options]` 的 `Required`，安装直接失败。
- 上游投毒原样转发。签名也拦不住这种情况，所以这条 ADR 不声称自己解决了供应链上游。
- 引导成本记在 README 的安装段，不在代码里——没有一处 CI 会因为你忘了写 `SigLevel` 而失败。

## 什么时候重新考虑

- 源的用户超出「能照着 README 做两行操作的人」，或有人报告那两行很难照做。
- 出现比「每个客户端 lsign 一次」更省的引导方式，例如公钥进入用户已信任的分发渠道。
- `gh-pages` 与仓库写权限不再是唯一写入路径：第二条发布目标、第三方镜像。
- 上游开始发布自带签名的产物，本仓库只剩转发它的签名。
