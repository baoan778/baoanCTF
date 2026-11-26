# Flag 形态速查（看到就别划过去）

> 用途：任何命令输出 / 文件内容 / 内存 dump / 日志里出现下列形态 →
> **立刻** `fgs verify <pid> <fact> --from <原始输出>` **逐字**确认（byte-exact 才算数）。
> 为什么单列一页：agent 最常见的丢分方式是「**flag 已经出现在输出里，却当成普通文本划过去了**」
> （WgpSec 2026 TCH 实测：加了这个检测 hook 直接挽回数分）。

## 一、形态表

| 来源 | 形态 | 采集用正则 |
|---|---|---|
| 0xGame | `0xGame{...}` | `0xGame\{[^}]{3,}\}` |
| 通用 | `flag{...}` / `FLAG{...}` | `(?i)flag\{[^}]{1,}\}` |
| CTF 平台常见 | `ctfshow{` · `BUUCTF{` · `NSSCTF{` · `DASCTF{` · `hgame{` · `actf{` · `n1ctf{` · `SUCTF{` · `moectf{` · `HNCTF{` · `BMZCTF{` · `QWB{` | `<前缀>\{[^}]{1,}\}` |
| 本仓历史 | `LHFLAG{`（练习平台）· `PKWCTF{`（PKWCTF） | 同左 |
| 校赛/自办 | 任意 `^[A-Za-z0-9_]{2,20}\{[^}]{3,}\}$` 形状 | `[A-Za-z0-9_]{2,20}\{[^}]{3,}\}` |
| 无花括号 | `flag: xxx` · `flag = xxx` · `FLAG-xxx` | `(?i)flag\s*[:=-]\s*\S{4,}` |

## 二、变形（先解一层再判）

- **base64**：`ZmxhZ3s` / `RkxBR3s` 开头 → `py -c "import base64;print(base64.b64decode('...'))"`
- **hex**：`666c61677b` 开头 → `bytes.fromhex(...)`
- **rot13 / 凯撒 / 反转**：`syvt` 这类看着像词的串
- **PNG/JPEG 里塞的**：`strings -n 6 <file> | grep -iE 'flag|\{'`、`zsteg -a`、`binwalk -e`
- **内存/进程里**：`strings` 全量 + `grep -iE '[A-Za-z0-9_]{2,20}\{'`（先看形状，再取证）

## 三、判据（不靠感觉）

1. **形状对 ≠ 是真 flag**：必须 `fgs verify` 逐字溯源（防"自己拼的 / 从别处抄的"）。
2. **变形要亲手解**，别凭印象说"这应该是 flag 的 base64"。
3. **多 flag 题**：拿一个不等于全解，按题面数量核对（有的题要求 flag1/flag2 都交）。
4. **提交只认平台回执**：`fgs confirm` 是**人的动作**（看到平台回显「正确」），自评/HTTP 200/退出码 0 都不算。
