# 0004 · 一个 step 的身体可以外提，流水线不折

状态：已采纳 · 2026-11-15

## 背景

[ADR-0001](0001-publish-pipeline-stays-in-the-workflow.md) 决定"发布流水线留在 workflow 里，不折成一个脚本"，理由是 step 边界本身就是 seam：`if:` 是它的 interface，`needs` 与 `concurrency` 是它的约束，逐 step 日志和"只重跑失败那一步"是它给的 leverage。

后来三笔提交陆续把几个 step 的**内容**搬了出去：入口判定进 `scripts/upstream.sh`（7115cf2）、Release 的 45 行判断进 `scripts/release.sh`（6efa568）、gh-pages 的 15 行进 `scripts/publish-pages.sh`（0486880）。这正是 `docs/agents/domain.md` 说的那种"和既有 ADR 相悖要先摆出来"的时刻：和 ADR-0001 冲突的部分（它说"不折"）值得重开，因为它要挡的东西和这些提交动的东西不是同一件。

## 决定

**step 的边界留在 YAML，step 的身体可以进 `scripts/`。**

- 边界：step 列表、`if:` 条件、`needs:`、`concurrency:`、`env:`、逐 step 的日志、只重跑失败那一步的能力。这些是 ADR-0001 说的 interface，一处都不动。
- 身体：一段只有一种调用方式的 shell，当且仅当它有一个能在离线测试里坐上的 adapter 时可以外提。已经坐在那里的三个：
  - `publish-pages.sh` 的 `<remote>`：CI 传 token URL，`tests/test_publish_pages.py` 传一个本地裸库；
  - `build-repo.sh` 的 `BUILD_CHECK`：CI 用真的 `build-check.sh`，`tests/test_build_repo.py` 用 stub；
  - `upstream.sh` 的 `BUMP`：CI 用真的 `bump.py`，`tests/test_upstream.py` 用 stub。
- 判据是**第二个 adapter 存在**，不是行数多少。ADR-0001 的"什么时候重新考虑"第二条（"某个 step 需要被 `publish.yml` 以外的调用方复用"）就是这条：离线测试就是那个调用方。

## 后果

- ADR-0001 的其余部分照旧：`if:` 仍然是门，`needs`/`concurrency` 仍然是约束，"先发布、最后提交"的顺序仍然靠 step 顺序（这条顺序后来由 `tests/test_inventory.py` 的 `PublishTransactionTest` 按住）。
- 外提的脚本，argv 与冻结行变成契约：`publish-pages.sh <tree> <版本> <remote>`、`build-repo.sh` 的 `-e REPO_NAME -e KEEP`、`upstream.sh` 的四条冻结行。改它们的接口等于改 workflow。后来 `publish-pages.sh` 的接口又多了一条：`GIT_NAME` / `GIT_EMAIL` 必填（签名 gh-pages 那次提交的身份），由 workflow 的 job env 声明——一个在 `publish.yml` 里声明一次、脚本拒绝自己编的值。
- 秘密留在 YAML：token URL 由 step 拼好传进去，脚本自己不认识 token。一个需要 `GITHUB_*` 或 `secrets` 才能跑的脚本，是外提错了方向。
- 一个 step 的"顺序约束"并没有因此进代码——它进了测试。ADR-0001 的"后果"第一条因此不再只是注释。

## 什么时候重新考虑

- 外提的脚本要读 Actions 上下文才能跑（`GITHUB_*`、`secrets`、`inputs`）——那说明它和 step 边界其实是一件事，应该搬回去。
- 第二个 adapter 消失了（某个脚本只剩 CI 一条调用路径，测试也不再驱动它），那它就该回到 YAML 里，注释比脚本离读者更近。
- 出现第二个发布目标（aarch64 源、镜像），ADR-0001 要整条重开——那时这条会跟着重写。
