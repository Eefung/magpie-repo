# 0005 · 一个模块可以吸收外提的 step 身体

状态：已采纳 · 2026-11-17

## 背景

[ADR-0004](0004-step-bodies-may-move-into-a-script.md) 定下「step 的身体可以进 `scripts/`」，判据是**第二个 adapter 存在**。它举的三个例子里有 `scripts/upstream.sh`：CI 用真的 `bump.py`，`tests/test_upstream.py` 用 stub。

后来入口检查自己吃下了「发不发、发哪个版本」与第五向，`upstream.sh` 只剩中继：跑一次 `--check`、把四条行原样交出去、再让 `--packages` 写下包清单。中继没有第二个 adapter 的价值，也没有自己的判断——ADR-0004 的「什么时候重新考虑」第三条说的就是这一刻。

它给的归宿是「回到 YAML 里」。这一次身体没有回 YAML：判断已经在一个有测试的模块里（`bump.py` 的 `check`，对着假的 `fetch` 跑），YAML 只剩调用与中继。删掉的脚本与它的测试是外壳，不是覆盖。

## 决定

**外提的 step 身体除了「回 YAML」，还可以被一个已有的、有测试的模块吸收。** 判据不变：第二个 adapter 存在。这里那个 adapter 是模块自己的假 `fetch` 与进程边界测试，不再是一个 stub 脚本。

- 判断的落点是模块；YAML 只留调用、`if:` 门、`needs`、逐 step 的日志。
- 被吸收的脚本连同它的测试一起删；`AGENTS.md` 的两张地图、`GLOSSARY.md` 的相关条目、`README` 的流程段跟着改——它们在搬动的那一刻就失真了。
- 冻结行的清单跟着动：这里少了一个生产者（`packages=` 变成 job env 的一个名字），也少了一个读者（`carried=` 改为日志）。
- 失败路径要接手：脚本里那句「把日志打出来再退」随它消失，调用它的 step 自己交（`cat` 在 `set -e` 之前）。

## 后果

- 发布链上少一个只有一条调用路径的脚本；workflow 的入口步骤直接调 `scripts/bump.py --check`。
- 入口步骤里的判断只剩「失败就说为什么」与「把两个模式接上」，两者都有测试（`tests/test_inventory.py` 的 `EntryStepTest` 把这一步的 shell 抽出来对着 stub 跑）。
- ADR-0004 的「后果」里关于 `upstream.sh` 的句子成为历史；那条决定的其余部分（边界留在 YAML、秘密留在 YAML）不变。

## 什么时候重新考虑

- 又一个外提的脚本只剩中继，而它要去的模块没有测试——那时先补测试，再删脚本。
- 入口检查开始需要 Actions 上下文（`GITHUB_*`、`secrets`、`inputs`）才能跑：那说明它和 step 边界是一件事，应该回到 YAML。
