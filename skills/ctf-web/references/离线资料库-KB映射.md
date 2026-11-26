# 离线资料库（$KB 映射，从 SKILL.md 搬出）

> 从 `SKILL.md` 搬出的细节（原文未改）；入口见 SKILL.md。

## 离线资料库（卡住时的第二入口）

`tools/knowledge/riftx/recommended-skills/` 里有按攻击面切好的 **payload 表 + 脚本**，
无外网也能用。**别整读**，按当前卡点直接取：

| 卡在 | 直接取 |
|---|---|
| PHP 弱类型 / 魔术哈希 / 正则绕过 | `security-patterns/references/Pattern-Matching/php-magic-hashes.txt`（1545 行） |
| SQLi：报错/时间盲注 payload | `exploit-sqli/assets/{common_error,time_based}_payloads.txt`、`dbms_fingerprints.txt` |
| SQLi：布尔盲注差异检测 | `exploit-sqli/scripts/boolean_sqli_tester.py`、`response_analyzer.py`（用 `py` 跑） |
| LFI / php wrapper / 路径穿越 | `exploit-lfi/assets/{wrapper,traversal,encoding_bypass}_payloads.txt`、`exploit-lfi/references/php_wrappers.md` |
| XSS：CSP 绕过 / mutation / polyglot | `exploit-xss/assets/{csp_bypass,mutation_xss,polyglot_xss}_payloads.txt` |
| 命令注入词表 | `security-fuzzing/references/Fuzzing/command-injection-commix.txt`（926KB） |
| Java 反序列化 / JNDI / EL | `exploit-java/references/{deserialization,jndi-injection,el-injection}.md` |
| Node 原型链污染 | `exploit-proto-pollution/references/prototype-pollution.md` |
| 请求走私 / 缓存欺骗 / 竞态 | `exploit-smuggling/`、`exploit-cache/`、`exploit-race/references/` |
| 访问控制绕过（ghost bits） | `exploit-access-bypass/references/ghost-bits-cast.md`（743 行） |
| webshell 参考样本 | `security-webshells/references/Web-Shells/` |

```bash
KB=$PI_TOOLS/knowledge/riftx/recommended-skills
grep -i 'magic' $KB/security-patterns/references/Pattern-Matching/php-magic-hashes.txt | head
py $KB/exploit-sqli/scripts/boolean_sqli_tester.py --help
```

索引（36 个 skill → 用途 → 对应 pi-lab 哪个 skill）：`tools/knowledge/INDEX.md`。

