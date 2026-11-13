# 0001 · 发布流水线留在 workflow 里，不折成一个脚本

状态：已采纳 · 2026-11-13

## 背景

`publish.yml` 的 5 个 step 靠 `$GITHUB_ENV` 和磁盘上的 `repo/` 传递事实，先后顺序只写在注释里。架构审阅据此提出把「发布一次」收成一个 module：一个入口、一条带契约的链，workflow 只剩调用与 `if:` 门。

## 决定

不折。Actions 的 step 边界本身就是 seam——`if:` 是它的 interface，`needs` 与 `concurrency` 是它的约束，逐 step 的日志和「只重跑失败的那一步」是它给的 leverage。折成一个脚本之后这层 seam 并不会消失，只会从 YAML 挪进 bash，而 bash 里既没有 `if:` 门，也没有按步粒度的日志。

8579bf0 刚刚反向整理过这里：删掉 `scripts/publish.sh`，把版本号收敛到渲染步骤解析一次写进 `GITHUB_ENV`。在出现第二个发布目标之前，这套顺序还没付过学费。

## 后果

- 顺序约束仍然靠 step 顺序与注释维持，插一个 step 没人拦。
- 失败点停在可重入的状态上（gh-pages 已推 / PKGBUILD 未提交／Release 未建），可由下一次运行接上。
- 「先发布，最后提交」这条不变量写在 `publish.yml:5-6` 的注释里，不在代码里。

## 什么时候重新考虑

- 出现第二个发布目标：aarch64 源、AUR 回潮，或一个仓库同时供两个源。
- 某个 step 需要被 `publish.yml` 以外的调用方复用。
- 顺序约束开始反复出错，也就是这条 ADR 的「后果」真的发生了。
