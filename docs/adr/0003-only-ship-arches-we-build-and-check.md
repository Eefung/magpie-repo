# 0003 · 只发布构建并核对过的 arch

状态：已采纳 · 2026-11-15

## 背景

`ARCHES` 与两份模板都声明了 x86_64 与 aarch64，而 publish 的 docker 构建只做 x86_64，载荷核对也只跑那个容器里打出来的包。README 把这件事写成一句附注：「PKGBUILD 里还写着 aarch64，但 CI 不构建也不验证」。

这不只是多写一行。渲染逐 arch 去上游的 `SHA256SUMS` 里找资产：arm64 缺失时 `render()` 抛 `the release has no magpie-linux-arm64`，一个 PKGBUILD 都不写，x86_64 的发布跟着停。一个我们从不发布、也从不核对的 arch，握着整个发布的开关。这不是假设——探针 D 用一份只有 amd64 的 SHA256SUMS 复现了它：

```text
BumpError: magpie-bin: the release has no magpie-linux-arm64
packages/magpie-bin: (空)   packages/magpie-cli-bin: (空)
```

## 决定

`ARCHES` 只留 `x86_64`，两份模板同步去掉 aarch64 的 `arch=()`、`source_aarch64`、`sha256sums_aarch64` 与 `package()` 的 case 臂。渲染只依赖要发的 arch。

## 后果

- 上游哪天不再发布 arm64 资产，本仓库照常发布 x86_64。
- PKGBUILD 声明的每个 arch 都真的有服务；README 那句附注删掉。
- 照 PKGBUILD 自建的用户在 aarch64 上拿不到这个包。今天他们从源里也拿不到（源里没有 aarch64 包），差别只在自己构建这条路。
- 反过来，上游若缺 amd64 资产，整次渲染仍然会停——这是我们唯一构建并核对的 arch，为它停下是对的。

## 什么时候重新考虑

- 第二个 arch 的构建与载荷核对有了落点：qemu + 第二个容器，或原生 runner。那时先改 `ARCHES`，模板由双向守卫逼着一起改，再加一个 arch 的 docker 构建。
- 上游停止发布 amd64 资产。那时的问题不在表里，在上游。
