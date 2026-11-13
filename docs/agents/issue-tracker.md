# Issue tracker: GitHub

本仓库的 issue 与 spec 都放在 GitHub Issues 里（`Eefung/magpie-repo`）。一律用 `gh` CLI 操作。

## 约定

- **新建 issue**：`gh issue create --title "..." --body "..."`。多行 body 用 heredoc。
- **读取 issue**：`gh issue view <number> --json number,title,body,labels,comments`。
- **列出 issue**：`gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，配合 `--label`、`--state` 过滤。
- **把 issue 设成某个父 issue 的子 issue**：`gh issue create --parent <parent> ...`，或事后 `gh issue edit <parent> --add-sub-issue <child>`（`gh` 2.94+）。旧版 `gh`：`gh api --method POST repos/<owner>/<repo>/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>`（用 database id，见下面的 **阻塞关系**）。如果子 issue 不被支持，就在子 issue 正文顶部写 `Part of #<parent>`。
- **阻塞关系**：用 GitHub 原生的 issue dependencies，这是唯一在 UI 可见的规范表示。用 `gh issue create --blocked-by <n>,<n> ...` 或 `gh issue edit <child> --add-blocked-by <blocker>` 添加（`gh` 2.94+）。旧版 `gh`：`gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，其中 `<blocker-db-id>` 是阻塞方的数字 **database id**（`gh api repos/<owner>/<repo>/issues/<n> --jq .id`，_不是_ `#number`，也不是 `node_id`）。没有 dependencies 时退回到在子 issue 正文顶部写一行 `Blocked by: #<n>, #<n>`。所有阻塞方都关闭后，该 issue 才算可开工。
- **统计未关闭的阻塞方**：`gh api repos/<owner>/<repo>/issues/<n> --jq .issue_dependencies_summary.blocked_by`（只算未关闭的，0 表示可以开工）。`gh issue view <n> --json blockedBy` 也会列出已关闭的阻塞方，需要再过滤：`--jq '[.blockedBy.nodes[] | select(.state == "OPEN")] | length'`。
- **评论**：`gh issue comment <number> --body "..."`。
- **加 / 去标签**：`gh issue edit <number> --add-label "..."` / `--remove-label "..."`。
- **关闭**：先用 `gh issue comment <number> --body "..."` 说明原因，再 `gh issue close <number>`。对已关闭的 issue，`gh issue close --comment` 会丢掉评论但仍返回 0。

仓库可由 `git remote -v` 推出；在 clone 里跑 `gh` 会自动识别。

只往用户自己的仓库提 issue。**不要**提给 `mattpocock/skills`（这些 skill 的来源仓库），除非用户拥有它。在 fork 里 `gh` 可能默认指向上游父仓库，所以每次会话第一次 `gh issue create` 之前，先用 `gh repo view --json nameWithOwner` 确认目标仓库，不是用户的仓库就问用户。

本仓库打包的是上游 [magpie](https://usemagpie.ai)：上游自己的 bug 不属于本仓库的 issue，本仓库的 issue 只覆盖打包、同步、发布流水线这一层。

## Pull request 作为分流入口

**PRs as a request surface: no.** _（若本仓库把外部 PR 当作功能请求，改成 `yes`；`/triage` 会读这个开关。）_

设为 `yes` 时，PR 走和 issue 相同的标签与状态，用对应的 `gh pr` 命令：

- **读取 PR**：`gh pr view <number>`，再 `gh pr view <number> --comments`（非 TTY 下 `--comments` 只打印评论，不含正文），diff 用 `gh pr diff <number>`。
- **列出待处理的外部 PR**：`gh api --paginate 'repos/{owner}/{repo}/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`。
- **评论 / 打标签 / 关闭**：`gh pr comment`、`gh pr edit --add-label`/`--remove-label`、`gh pr close`。

GitHub 的 issue 和 PR 共用一个编号空间，所以单独的 `#42` 可能是两者之一：先用 `gh pr view 42` 解析，失败再回落到 `gh issue view 42`。

## 当 skill 说"publish to the issue tracker"

新建一个 GitHub issue。

## 当 skill 说"fetch the relevant ticket"

按上面 **读取 issue** 的方式读取。

## Wayfinding 操作

供 `/wayfinder` 使用。**map** 是一个 issue，**ticket** 是它的子 issue。

- **Map**：一个打了 `wayfinder:map` 标签的 issue，正文放 Notes / Decisions-so-far / Fog。`gh issue create --label wayfinder:map`。
- **子 ticket**：把 issue 作为 map 的 GitHub sub-issue 关联起来（见 **把 issue 设成某个父 issue 的子 issue**）。不支持 sub-issue 时，把子 ticket 写进 map 正文的 task list，并在子 issue 正文顶部写 `Part of #<map>`。标签：`wayfinder:<type>`（`research`/`prototype`/`grilling`/`task`）。被认领后，assign 给负责推进的人。
- **阻塞关系**：见 **约定** 里的 **阻塞关系**。
- **Frontier 查询**：列出 map 下未关闭的子 issue（`gh issue list --state open`，限定到 map 的 sub-issue / task list），丢掉有未关闭阻塞方（见 **统计未关闭的阻塞方**，或正文 `Blocked by` 行里还有未关闭 issue）和已有 assignee 的；按 map 顺序取第一个。
- **认领**：`gh issue edit <n> --add-assignee @me`，这是本次会话的第一个写操作。
- **解决**：`gh issue comment <n> --body "<answer>"`，然后 `gh issue close <n>`，再把上下文指针（结论 + 链接）追加到 map 的 Decisions-so-far。
