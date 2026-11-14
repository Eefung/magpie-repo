# AGENTS.md

本文件给在这个仓库里干活的 AI agent 看。项目本身的说明见 [README](README.md)。

## Agent skills

### Issue tracker

issue 与 spec 都放 GitHub Issues（`Eefung/magpie-repo`），用 `gh` CLI 操作。详见 `docs/agents/issue-tracker.md`。

### Triage labels

五个规范分流角色，标签串就是角色名本身：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

single-context：根目录一个 `GLOSSARY.md`，决定记在 `docs/adr/`。详见 `docs/agents/domain.md`。

## 检查

推送前跑 `scripts/check.sh`：它就是 CI `test` job 的全部闸门（离线单测、shellcheck、actionlint），两个 linter 由脚本按固定版本下到 `~/.cache/magpie-repo`，所以本机没装也跑得动、且与 runner 同版本。它跑不到的是打包——`build-check.sh` 的 root 分支只在 publish 的容器里存在，那一支由发布运行证明。

`publish.yml` 的 `paths` 含 `scripts/**`、`tests/**`、`templates/**`，所以这些改动推送后会触发一次 publish，可能直接发一版。这类提交单独推，一次提交只做一件事。

## 提交

遵循[约定式提交](https://www.conventionalcommits.org/zh-hans/v1.0.0/)：`type(scope): 描述`，描述用中文，句末不加句号。

| 类型 | 用在哪 |
| --- | --- |
| `feat` | 用户可见的新能力 |
| `fix` | 修掉错误行为 |
| `docs` | README、GLOSSARY、ADR、调研笔记 |
| `refactor` | 不改行为的重排 |
| `build` | 发布产物与版本号——渲染出的 PKGBUILD、发布流程里碰版本的地方。原来的 `upgpkg:` 一律用这个 |
| `ci` | workflow 与发布流水线本身 |
| `chore` | 配置、工具、依赖 |

一次提交只做一件事：`build` 的渲染结果与流程改动分开提交，否则回滚时会把版本号一起带走。
