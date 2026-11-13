# 领域文档

探索代码库时，工程类 skill 应该怎样读取本仓库的领域文档。

## 探索之前先读

- 仓库根目录的 **`GLOSSARY.md`**，或者
- 如果存在 **`GLOSSARY-MAP.md`**：它指向每个 context 各自的 `GLOSSARY.md`，读与当前话题相关的那些。
- **`docs/adr/`**：读和即将改动的区域相关的 ADR。多 context 仓库还要看 `src/<context>/docs/adr/` 里该 context 专属的决定。

这些文件不存在就**静默跳过**：不要点出它们缺失，也不要主动建议先创建。`/domain-modeling` skill（由 `/grill-with-docs` 和 `/improve-codebase-architecture` 触达）会在术语或决定真正定下来时才按需创建它们。

## 目录结构

**本仓库是 single-context**（根目录一个 `GLOSSARY.md` + 一个 `docs/adr/`）：

```
/
├── GLOSSARY.md
├── docs/adr/
│   ├── 0001-....md
│   └── 0002-....md
├── packages/          ← 各 Arch 包的 PKGBUILD 目录
├── templates/         ← PKGBUILD 模板
└── scripts/           ← bump.py、构建与建仓库脚本
```

多 context 仓库的样子（根目录存在 `GLOSSARY-MAP.md` 即判定）留作参考：

```
/
├── GLOSSARY-MAP.md
├── docs/adr/                          ← 系统级决定
└── src/
    ├── ordering/
    │   ├── GLOSSARY.md
    │   └── docs/adr/                  ← context 专属决定
    └── billing/
        ├── GLOSSARY.md
        └── docs/adr/
```

## 用 glossary 的词汇

输出的内容提到某个领域概念时（issue 标题、重构提案、假设、测试名），用 `GLOSSARY.md` 里定义的那个词，不要漂移到 glossary 明确回避的同义词。

需要的概念还没进 glossary，这本身是个信号：要么你在发明项目不用的说法（重新想一下），要么确实有缺口（记下来交给 `/domain-modeling`）。

## 撞上 ADR 冲突要明说

如果你的输出和既有 ADR 相悖，明确摆出来，不要偷偷覆盖：

> _和 ADR-0007 冲突（那个决定……），但值得重开，因为……_
