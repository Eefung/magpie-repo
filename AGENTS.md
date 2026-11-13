# AGENTS.md

本文件给在这个仓库里干活的 AI agent 看。项目本身的说明见 [README](README.md)。

## Agent skills

### Issue tracker

issue 与 spec 都放 GitHub Issues（`Eefung/magpie-repo`），用 `gh` CLI 操作。详见 `docs/agents/issue-tracker.md`。

### Triage labels

五个规范分流角色，标签串就是角色名本身：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

single-context：根目录一个 `GLOSSARY.md`，决定记在 `docs/adr/`。详见 `docs/agents/domain.md`。
