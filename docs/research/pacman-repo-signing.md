# pacman 自建软件源的 GPG 签名,与 SigLevel 的真实语义

调查日期 2026-11-14。对象:pacman 7.1.0(man 页日期 2026-10-24)与 `pacman/pacman` master 分支源码;下文 `文件:行号` 均指当日 master,行号会随后续提交漂移,引用区给出文件 URL。

## 一句话结论

能签,CI 的一次性容器不是障碍——私钥放在 secret 里,step 内导入临时 `GNUPGHOME` 就能签;真正的成本在客户端:用户没见过的 key 必须由用户 `pacman-key --add` + `--lsign-key` 才会被接受,`SigLevel` 写 `TrustAll` 也不能豁免这一步。所以 README:58 的"不能签名"不成立,而"sha256 保证完整性"只对传输/存储损坏成立,对"源被篡改"不成立。

## 一、能不能给自建源签名

### (a) 长期私钥 + 一次性容器:可行

结论:可行。容器的寿命与"能不能签名"无关,只影响私钥在磁盘上停留多久。

签名用的是三件独立的事,没有一件要求容器长期存在:

1. **setup**:在容器里建临时 `GNUPGHOME`,把 secret 里的私钥 `gpg --import` 进去。CI 每次从零导入,恰恰是"一次性容器"的正确用法。
2. **签包**:`makepkg --sign --key <FPR>`,等价于对产物执行 `gpg --detach-sign --no-armor`,产出 `<pkg>.pkg.tar.zst.sig`(`scripts/libmakepkg/integrity/generate_signature.sh.in` 的 `create_signature`;`scripts/makepkg.sh.in:1273-1284` 决定签不签、并先检查私钥存在)。
3. **签库**:`repo-add --sign --key <FPR>`,内部同样是一次 `gpg --detach-sign --no-armor`,对象是数据库文件(`scripts/repo-add.sh.in:159-176`),产出 `magpie.db.tar.gz.sig`,并在 `rotate_db` 里链接成 pacman 实际请求的 `magpie.db.sig`(`:500-537`;请求名见 `be_sync.c:191-211`)。

一个必须纠正的细节:**PKGBUILD 的 `options=('sign')` 不生效。** makepkg 判定是否签名用的是 `check_buildenv`(`scripts/makepkg.sh.in:1273`),它只查 makepkg.conf 的 `BUILDENV`;会读 PKGBUILD `options` 的是 `check_buildoption`(`scripts/libmakepkg/util/option.sh.in:98-113`)。要默认签名就把 `sign` 加进 makepkg.conf 的 `BUILDENV` 并设 `GPGKEY`(`makepkg.conf.5`),或者每次显式 `--sign`。

另一件容易漏的:`repo-add --sign` **只签数据库,不签包**。包签名要么跟着包放进软件源目录(pacman 在 db 里没有内嵌签名时会找包旁边的 `<pkgfile>.sig`,`be_package.c:303-315`),要么用 `repo-add --include-sigs` 把 base64 签名写进 db(`scripts/repo-add.sh.in:268-281`)。

顺带一条文档瑕疵:`repo-add(8)` 说"包旁边有 `.sig` 就会自动嵌入数据库",源码要求显式 `--include-sigs`(`:269` 的 `INCLUDE_SIGS` 门)。以源码为准。

具体命令见文末第四节。

### (b) 每次 CI 现生成临时密钥:产出的是字节,不是信任

结论:不算数。它能生成语法正确的 `.sig`,但在 pacman 的信任链上等于零,并且把成本从维护者转移给每个用户。

pacman 的信任根不在软件源里,在**客户端自己的 keyring** 里(`/etc/pacman.d/gnupg`)。`TrustedOnly` 的定义就是"签名必须是 keyring 里的 key,且 fully trusted"(`pacman.conf.5`;位标志 `ALPM_SIG_PACKAGE` 等见 `alpm.h:382-405`,`conf.c:513-526`)。一个从未见过的 key 会依次撞上两道墙:

| 阶段 | 结果 | 出处 |
| --- | --- | --- |
| key 不在客户端 keyring | `GPG_ERR_NO_PUBKEY` → `ALPM_SIGSTATUS_KEY_UNKNOWN`,**无条件 fatal**;`TrustAll` 也救不了,它只放宽 marginal/unknown *trust*,不放宽"key 不认识" | `signing.c:675-698`、`:856`;错误文案 `error.c:116-119`(`invalid or corrupted package (PGP signature)`) |
| pacman 尝试补救 | 弹问确认后按 WKD → keyserver 抓取并导入(`_alpm_key_import`),导入成功后**重试一次**;临时密钥通常没发布到任何 keyserver,所以这一步就是失败 | `signing.c:890-955`、`_alpm_key_import` |
| key 已导入但没 locally sign | `signature from "..." is unknown trust`,仍是 fatal(除非 `TrustAll`) | `signing.c:917-923`(`ALPM_SIGVALIDITY_UNKNOWN`) |

要让客户端接受,只有一条路:`pacman-key --add <公钥文件>` 或 `--recv-keys`,再 `pacman-key --lsign-key <FPR>`(把该 key 用本地私钥签一次,把信任根扎在本地,`pacman-key(8)`)。每次发版一把新 key,就等于每次发版要求**每台机器上的每个用户**重做这两步,还要自己确认 FPR 没被换掉——这已经不是"自动化发版",而是把一次带外人工操作写进了发版流程。

反过来,一把长期密钥只需每个客户端 lsign 一次。这就是签名对自建源的真实成本结构:**一次性引导,而不是每次发布**。此结论直接由 `pacman-key(8)` 的 `--lsign-key` 语义得出,不是推断。

### (c) 泄漏 / 过期 / 撤销

- **泄漏**:私钥一旦进过任何能读它的地方,攻击者就能签出对所有"已 lsign 该 key"的客户端都有效的包,签名本身无法区分是谁签的。止损只能靠撤销 + 客户端更新 keyring,不存在"只让新版本生效"的撤回机制。私钥进 CI 的暴露面因此需要单独权衡:secret 不会传给 fork 触发的 workflow(GitHub 文档),这是这条路可用的边界条件之一。
- **过期**:校验侧把"key 过期"当成有效——`ALPM_SIGSTATUS_KEY_EXPIRED` 与 `VALID` 同组处理(`signing.c:830-832`),只打印 `signature from "..." is expired`(`:932-939`),**不阻断安装**。也就是说密钥过期不会炸掉用户,而是静默降级:签名还在,但"这个签名来自一个已过期的身份"这个信号被弱化成一条日志。别把 **key 过期**与**签名过期**混为一谈:`ALPM_SIGSTATUS_SIG_EXPIRED` 是 fatal,与 `KEY_UNKNOWN`、`KEY_DISABLED`、`INVALID` 归在同一组(`signing.c:849-859`)。`pacman.conf.5` 那句"两种 TrustAll 都不接受无效或过期的**签名**"指的正是这一组,不含 key 过期——man 页与代码在这一点上不冲突,只是 man 页没把两者分开说。
- **撤销**:撤销信息的载体是 key 本身(撤销证书)。进了客户端 keyring 之后,`validity` 变成 `NEVER`(fatal,`signing.c:849-852`)或 key 被判 disabled(`:856-858`、`:956-959`)。分发撤销的机制是 `pacman-key --populate` 的 `<keyring>-revoked` 文件,或 `--refresh-keys`(`pacman-key(8)`)。
- 轮换同理:新 key 对老客户端仍然是"unknown",必须重新走一次 `--add` + `--lsign-key`。轮换的成本和首次引导一样高,这一点在决定"要不要签"时比"能不能签"更关键。

## 二、SigLevel 到底是什么语义

`SigLevel` 是**两个正交维度**的组合,不是一个档位。两个维度都可以整行写(同时作用于包和库),也可以加 `Package` / `Database` 前缀只作用于一侧(`pacman.conf.5`;解析见 `conf.c:460-553`,前缀切分在 `:475-487`)。

- **检查项**(When to Check):`Never` / `Optional` / `Required`(默认)
- **允许什么**(What is Allowed):`TrustedOnly`(默认)/ `TrustAll`

`conf.c` 的映射是位标志:`Required` 置 `ALPM_SIG_PACKAGE`/`ALPM_SIG_DATABASE` 并清 OPTIONAL 位,`Optional` 置位并置 OPTIONAL 位,`Never` 清位,`TrustAll` 置 `*_MARGINAL_OK | *_UNKNOWN_OK`,`TrustedOnly` 清它们(`conf.c:490-526`;枚举注释见 `alpm.h:382-405`)。同一行内从左到右处理,后面的覆盖前面的;仓库段未写 `SigLevel` 时从 `[options]` 继承(`conf.c:561-564`、`:1420`)。

**没有 `PerPackageSig` 这个选项。** man 页里也不存在这个名字。细粒度就是上面那 5 个词 × 3 种写法(裸写 / `Package` 前缀 / `Database` 前缀)的组合。

**`Margin` / `MarginPackage` / `MarginDatabase` 同样不是选项名。** man 页里出现的 `marginally trusted` 只是在解释 GnuPG 术语(`pacman.conf.5:324`),不是 `SigLevel` 的取值;这层能力由 `TrustAll` 提供(允许 marginal 与 unknown trust)。

### 表 1:各写法到底校不校验

| 写法 | 是否请求并校验 `.sig` | 缺签名 | 签名无效/被篡改 | key 不在 keyring | key 在但未 lsign(unknown/marginal) |
| --- | --- | --- | --- | --- | --- |
| `Never` | 不请求、不校验 | 通过 | 通过(不查) | 通过 | 通过 |
| `Optional`(=`Optional TrustedOnly`) | 请求,有则校验 | 通过 | 失败 | 失败 | 失败 |
| `Optional TrustAll` | 请求,有则校验 | 通过 | 失败 | 失败 | 通过 |
| `Required TrustedOnly`(内置默认) | 请求,必须有 | 失败 | 失败 | 失败 | 失败 |
| `Required TrustAll` | 请求,必须有 | 失败 | 失败 | 失败 | 通过 |

表 1 的每一行都能落到代码:`Never` 清掉 `ALPM_SIG_DATABASE`/`ALPM_SIG_PACKAGE` 位,于是连 `.sig` 都不下载(`be_sync.c:210-211`、`sync.c:853-854`);`Optional` 的 OPTIONAL 位在 `_alpm_check_pgp_helper` 里把"签名缺失"折成成功(`signing.c:814-822`、`be_package.c:331-343`);`TrustAll` 只影响 `MARGINAL`/`UNKNOWN` 两个 validity 分支(`signing.c:837-848`),`KEY_UNKNOWN` 仍然 fatal(`:856`)。

### 表 2:整行写法在"完全没签名"的软件源上的实际效果

| 写法 | 包 | 数据库 | 与 `[magpie]` 现状 |
| --- | --- | --- | --- |
| 不写(默认 `Required TrustedOnly`) | 失败,`package missing required signature`(`error.c:118-119`) | 失败,`invalid or corrupted database (PGP signature)`(`error.c:73-74`) | 装不上 |
| `Optional` | 通过 | 通过 | 能装,且信任策略比 `TrustAll` 严 |
| `Optional TrustAll` | 通过 | 通过 | 能装;与上一行在本软件源上无差别 |
| `Required DatabaseOptional` | **失败**(裸 `Required` 作用于包) | 通过 | 装不上 |
| `PackageOptional DatabaseOptional` | 通过 | 通过 | 等价于 `Optional` |

`Optional TrustAll` 与 `Required DatabaseOptional` 的差别就是表 2 第 3、4 行:前者两侧都放宽"必须有签名",后者只放宽数据库、对包仍然强制。本仓库两侧都没签名,所以后者必然在包上失败。

顺带纠正一个常见误解:**`Never` 不等于"什么都不校验"**。没有签名可查时,pacman 仍然拿 db 里的 sha256 校验包文件(`be_package.c:317-328`:有内嵌签名才跳过 sha256,否则照校)。同理,`Optional` 下的"无签名"路径也照校 sha256。

### sha256 保什么、不保什么

- **保**:传输与存储损坏——截断、错位、镜像不同步、单点替换包文件但 db 未同步。这是"一致性"。
- **不保**:来源真实性。`magpie.db` 里的 sha256 与包文件来自同一个 origin、同一条 HTTPS 连接。能改 gh-pages 的人可以同时改包和 db 里的校验和,没有任何一环会发现。Release 的 `SHA256SUMS` 也放在 GitHub 上,不是独立信道,也没有签名:它防不了同一个攻击者,而用户也没有可信渠道去核对 `SHA256SUMS` 本身。
- 一句话:**sha256 让"下载没坏"可判定,签名才让"这是维护者发的"可判定。** 在本仓库的现状下,后者的实际承担者是 GitHub Pages 的 TLS 加上仓库写权限。

## 三、对 README:58 的判定

原文:"没有。签名要维护者长期持有私钥,而 CI 跑在一次性容器里。完整性由 `magpie.db` 里的 sha256(pacman 下载后校验)与 Release 的 `SHA256SUMS` 保证。"

**判定:事实部分对,因果部分不成立,结论表述过头。**

- **"没有签名"——成立,可核实**:`scripts/build-repo.sh:55` 的 `repo-add` 没有 `--sign`,`packages/magpie-bin/PKGBUILD` 的 `options` 里没有签名相关项(而且如上所述 `options=('sign')` 本来也不管用),软件源里不存在 `.sig`。
- **"签名要维护者长期持有私钥,而 CI 跑在一次性容器里"——不成立**。前半句对(签名确实需要一把长期存在的私钥),后半句推出"所以没有"是错的:一次性容器可以在启动时从 secret 导入那把长期私钥,签完销毁。容器寿命决定的是**私钥暴露窗口**,不是**可行性**。这是把运维风险写成了技术不可能。
- **真正的理由没有被写出来**:签了名之后,客户端必须先带外导入公钥并 `pacman-key --lsign-key`,否则 `SigLevel` 写 `Optional TrustAll` 也照样失败(表 1 的“key 不在 keyring”列)。这把成本放在了每个用户身上,才是自建小源普遍不签名的实际原因。
- **"完整性由 sha256 … 保证"——半对**。若把"完整性"理解为"下载到的字节与 db 记录一致",成立且有代码依据(`be_package.c:317-328`)。若理解为"内容没被篡改",则不成立:db 与包同源,篡改者可同时改两者。缺的是威胁模型限定语。
- **附带一处 FAQ 不准确**:README 把 `signature from "..." is unknown trust` 的解法写成"把 `SigLevel` 改成 `Optional TrustAll`"。这只对"key 已在 keyring、只是没 lsign"有效;key 根本不在 keyring 时报的是 `key "..." is unknown`(`signing.c:948-949`),`TrustAll` 对它无效,必须先导入再 lsign。

如果只改一句话,准确的写法是:"没有签名,因为客户端要先 `pacman-key --lsign-key` 才认,自建小源不值得让每个用户做这一次引导。sha256 防的是传输与存储损坏,源本身的可信度由 GitHub Pages 的 TLS 和仓库写权限承担。"

## 四、(可选)若将来要签,最小可行步骤

只列命令形态,不涉及本仓库改造。

```bash
# 1) 维护者一次性:生成长期密钥并发布公钥(离线备份私钥)
gpg --quick-gen-key "magpie-repo <eefung@tuta.io>" ed25519 sign

# 2) CI:临时 GNUPGHOME + 从 secret 导入(私钥 base64 存 secret)
export GNUPGHOME="$(mktemp -d)" && chmod 700 "$GNUPGHOME"
printf '%s' "$GPG_PRIVATE_KEY_B64" | base64 -d | gpg --batch --import

# 3) 签包(makepkg --sign,或 makepkg.conf 的 BUILDENV 里加 sign + 设 GPGKEY)
makepkg --sign --key "$GPGKEY"

# 4) 签库:--sign 只签 db,不签包
repo-add --sign --key "$GPGKEY" -q "$out/magpie.db.tar.gz" "$out"/*.pkg.tar.*
#    需要把包签名嵌进 db 时再加 --include-sigs

# 5) 软件源里要同时有:magpie.db、magpie.db.sig、*.pkg.tar.zst、*.pkg.tar.zst.sig
#    (repo-add 会自动建 magpie.db.sig → magpie.db.tar.gz.sig 的链接)

# 6) 客户端一次性引导,之后 SigLevel 可以收紧到 Required TrustedOnly
sudo pacman-key --add ./magpie-repo.asc
sudo pacman-key --lsign-key <FPR>
```

要点:第 6 步是这条路不可省的入口;不做,前五步全部无效。若只签库、不签包,`SigLevel` 要写成 `PackageOptional DatabaseRequired`——包侧放宽、库侧收紧;两侧都写 `Required` 会在包上失败。

## 五、引用来源

1. pacman.conf(5),SigLevel 一节 — https://man.archlinux.org/man/pacman.conf.5.en
2. repo-add(8),`--sign` / `--key` / `--verify` / `--include-sigs` — https://man.archlinux.org/man/repo-add.8.en
3. pacman-key(8),`--add` / `--lsign-key` / `--populate` / `--refresh-keys` — https://man.archlinux.org/man/pacman-key.8.en
4. makepkg(8),`--sign` / `--nosign` — https://man.archlinux.org/man/makepkg.8.en
5. makepkg.conf(5),`BUILDENV` 的 `sign` 与 `GPGKEY` — https://man.archlinux.org/man/makepkg.conf.5.en
6. PKGBUILD(5),`options` 与打包选项 — https://man.archlinux.org/man/PKGBUILD.5.en
7. pacman 源码 `src/pacman/conf.c`(SigLevel 解析、前缀、位标志、继承) — https://gitlab.archlinux.org/pacman/pacman/-/blob/master/src/pacman/conf.c
8. pacman 源码 `lib/libalpm/alpm.h`(`alpm_siglevel_t` 位标志定义) — https://gitlab.archlinux.org/pacman/pacman/-/blob/master/lib/libalpm/alpm.h
9. pacman 源码 `lib/libalpm/signing.c`(gpgme 状态映射、optional/marginal/unknown 判定、key 导入) — https://gitlab.archlinux.org/pacman/pacman/-/blob/master/lib/libalpm/signing.c
10. pacman 源码 `lib/libalpm/be_sync.c`(数据库签名校验、`.sig` 下载开关) — https://gitlab.archlinux.org/pacman/pacman/-/blob/master/lib/libalpm/be_sync.c
11. pacman 源码 `lib/libalpm/be_package.c`(包签名校验、无签名时仍校 sha256) — https://gitlab.archlinux.org/pacman/pacman/-/blob/master/lib/libalpm/be_package.c
12. pacman 源码 `lib/libalpm/sync.c`(包与 `.sig` 的下载开关) — https://gitlab.archlinux.org/pacman/pacman/-/blob/master/lib/libalpm/sync.c
13. pacman 源码 `lib/libalpm/error.c`(用户可见错误文案) — https://gitlab.archlinux.org/pacman/pacman/-/blob/master/lib/libalpm/error.c
14. pacman 源码 `scripts/repo-add.sh.in`(`create_signature`、`rotate_db`、`--include-sigs`) — https://gitlab.archlinux.org/pacman/pacman/-/blob/master/scripts/repo-add.sh.in
15. pacman 源码 `scripts/makepkg.sh.in`(签名判定、`--sign` 解析) — https://gitlab.archlinux.org/pacman/pacman/-/blob/master/scripts/makepkg.sh.in
16. pacman 源码 `scripts/libmakepkg/util/option.sh.in`(`check_buildenv` 只读 BUILDENV,`check_buildoption` 才读 PKGBUILD `options`) — https://gitlab.archlinux.org/pacman/pacman/-/blob/master/scripts/libmakepkg/util/option.sh.in
17. pacman 源码 `scripts/libmakepkg/integrity/generate_signature.sh.in`(`create_signature`) — https://gitlab.archlinux.org/pacman/pacman/-/blob/master/scripts/libmakepkg/integrity/generate_signature.sh.in
18. GitHub Docs,Using secrets in GitHub Actions(fork 触发的 workflow 不传 secret) — https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions

## 六、标为【推断】与未核实的部分

- 【推断】撤销信息不会自动抵达客户端:客户端 keyring 里的公钥不会自行更新,需要 `--populate` 的 `-revoked` 列表或 `--refresh-keys`。pacman-key(8) 只描述机制,没有描述"自动传播",此句是从机制推出的。
- 【推断】gpg 拒绝用已过期的私钥创建新签名(校验侧对"key 过期"宽松,与此不同)。本次未核对 gnupg 手册或源码,只核对了 pacman 侧的校验行为。
- 【推断】带口令的私钥在 CI 里需要 `--pinentry-mode loopback` 加口令注入;未在本仓库验证,也未引用 gnupg 手册。
- 未实测:以上命令未在容器里端到端跑过,签名字节、`gh-pages` 上 `.sig` 的可达性、客户端引导流程均为文档与源码推导。第四节 `gpg` 命令的参数形态(`--quick-gen-key` 的用法、口令处理)也未在本机执行验证。
- 未核实:GitHub secret 的 48 KB 上限是否影响私钥长度(常见 ed25519 私钥导出约数 KB,按此推断够用,但未实测)。
