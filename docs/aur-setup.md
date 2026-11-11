# 把推送接上 AUR

> **现状：AUR 已暂停新用户注册，这条路暂时走不通。** 日常用的是自托管 pacman 仓库（见 [pacman-repo.md](pacman-repo.md)）；下面这些步骤等注册恢复、或原维护者把你加成 co-maintainer 之后再用。工作流里的推送步骤已经写好，只要 secret 存在就会生效，不需要改代码。

本仓库的自动化只缺一样东西：一个能写 AUR 的 SSH 私钥。下面按顺序做完，工作流就会开始真正推包。

## 1. 注册 AUR 账号

1. 打开 <https://aur.archlinux.org/register>，填用户名、邮箱、密码。
2. 收邮件点确认链接（没验证过的账号不能提交任何东西）。
3. 登录后进 **My Account**，确认邮箱下面有 **SSH Public Key** 输入框——第 3 步的公钥粘在这里。

## 2. 生成一把只给 AUR 用的密钥

ArchWiki 的建议是单独一把，出问题时能单独吊销，不影响你别的 SSH 用途：

```bash
ssh-keygen -t ed25519 -C "aur@$(hostname)" -f ~/.ssh/aur
```

不设口令最省事（工作流里也是无口令使用）；想在本机用时加口令也行，但那样 `scripts/publish.sh` 每次都会问一次。

## 3. 把公钥登记到 AUR

```bash
cat ~/.ssh/aur.pub
```

把整行（`ssh-ed25519 AAAA... aur@host`）粘进 **My Account → SSH Public Key**，保存。这个框可以换行放多把公钥。

顺手加个别名，后面手推时会舒服些：

```bash
# ~/.ssh/config
Host aur.archlinux.org
  IdentityFile ~/.ssh/aur
  User aur
```

验证：

```bash
ssh -T aur@aur.archlinux.org
```

回 `Interactive shell is disabled.` 就通了；回 `Permission denied (publickey)` 说明公钥没登记上或没被 ssh 用上。已经用过 SSH 的机器可能还要 `ssh -o IdentitiesOnly=yes`（`~/.ssh/config` 里那行 `IdentitiesOnly` 就是干这个的）。

## 4. 把私钥放进 GitHub Actions

```bash
gh secret set AUR_SSH_PRIVATE_KEY --repo Eefung/magpie-aur < ~/.ssh/aur
```

顺便钉一下主机公钥，省掉首次信任：

```bash
ssh-keyscan -t ed25519,rsa aur.archlinux.org > /tmp/aur_known_hosts
gh secret set AUR_KNOWN_HOSTS --repo Eefung/magpie-aur < /tmp/aur_known_hosts
```

想改 AUR 提交的作者信息（默认从 `AUR_GIT_NAME`／`AUR_GIT_EMAIL` 变量取）：

```bash
gh variable set AUR_GIT_NAME --repo Eefung/magpie-aur --body "你的名字"
gh variable set AUR_GIT_EMAIL --repo Eefung/magpie-aur --body "you@example.com"
```

密钥轮换：生成新密钥对 → 先在 AUR 账号里把新公钥加进去（旧公钥先别删）→ 更新 `AUR_SSH_PRIVATE_KEY` → 手动跑一次工作流确认能推 → 再从 AUR 账号里删掉旧公钥。

## 5. 拿到这两个包名的写权限

`magpie-bin` 和 `magpie-cli-bin` 在 AUR 上已经存在，维护者是 czyt，且最近还在更新。AUR 的提交规则明确写了「不要创建重复的包」，所以不能另起一个 `magpie-bin-auto` 之类的包名顶替。两条合规的路：

**A. 申请成为共同维护者（推荐）**

在包的页面用英文留言（AUR 评论是公开的，维护者会收到通知），大意是：愿意帮忙跟进上游一天几十版的节奏，可以提供自动化的 PKGBUILD 与 `.SRCINFO`，请求加为 co-maintainer。示例：

```
Hi, thanks for keeping magpie packaged. Upstream ships dozens of builds a day,
so this package tends to trail by a few days. I maintain an automated workflow
that renders PKGBUILD + .SRCINFO from the release's published SHA256SUMS,
build-checks it in a clean Arch container, and pushes on every upstream
release: https://github.com/Eefung/magpie-aur

Would you be open to adding me as a co-maintainer of magpie-bin and
magpie-cli-bin so the automation can push directly? I'm happy to keep the
current packaging layout and dependencies unchanged.
```

成为 co-maintainer 之后，第 4 步配的私钥就有写权限了，`scripts/publish.sh` 直接推到原包名，`paru -Syu` 的升级路径完全不用改。

**B. 原维护者失联时走接管**

如果包被标记 out-of-date 两周以上、维护者无回应，可以在包页面提交 **Orphan Request**；flag 满 180 天的包会自动同意。接管（adopt）之后写权限归你，同样接第 4 步。

**C. 只想自己用**

不改包名、也不要权限：把本仓库的 `packages/magpie-bin` 当本地 PKGBUILD 用，`cd packages/magpie-bin && makepkg -si`，需要升级时 `git pull` 后重跑。代价是失去 `paru -Syu` 自动升级。

## 6. 首次推送前自检

```bash
gh workflow run aur.yml --repo Eefung/magpie-aur
gh run watch --repo Eefung/magpie-aur
```

日志里应该依次看到：`What is upstream at?` → `Render the PKGBUILDs` → `Build and check both packages`（两个包各打印一次 `magpie <版本>`）→ `Push to the AUR` → `magpie-bin: pushed ... to ssh://aur@aur.archlinux.org/magpie-bin.git`。

推成功后：

```bash
curl -s 'https://aur.archlinux.org/rpc/v5/info?arg[]=magpie-bin' | python3 -m json.tool | grep -E 'Version|LastModified'
paru -Syu     # 或者 paru -S magpie-bin
```

## 常见失败

| 日志 | 原因 |
|---|---|
| `Permission denied (publickey)` | 公钥没登记，或私钥 secret 带了多余空行／被截断 |
| `remote: error: insufficient permissions for user aur` | 密钥有效但你不是该 pkgbase 的维护者／co-maintainer |
| `remote: error: package not found` | 包名拼错；AUR 只认已存在的 pkgbase（新包由第一次 push 创建） |
| `Host key verification failed` | 设了 `AUR_KNOWN_HOSTS` 但内容和 AUR 当前主机键不一致——重新 `ssh-keyscan` 一次 |
| `==> ERROR: One or more files did not pass the validity check` | 上游改了某个产物的内容却没升版本号；手动 `python3 scripts/bump.py --render --version <ver>` 重新取校验和 |
