# 安全策略

## 报告漏洞

**不要开公开 issue。** 请通过以下方式私下报告：

- 邮箱：**2363799073@qq.com**
- 或在 GitHub 上用 [Private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)

请包含：复现步骤、影响范围、你的环境（OS / Python 版本）。
我们会在 **7 天内**确认收到，并在修复后致谢（除非你要求匿名）。

## 这个项目处理什么敏感数据

PiCTF 是**授权范围内**的 CTF 解题工具。它会接触：

| 数据 | 落盘位置 | 保护 |
|---|---|---|
| LLM API 密钥 | `.env`（**已 gitignore**） | 不落日志；`costguard` 只读不打印 |
| 平台 token | `~/.config/pi-lab/tsecbench.json`（0600） | 打印时打码 |
| 靶场 VPN 配置 | `*.ovpn`（**已 gitignore**） | 不入库 |
| 解题会话 | `work/`（**已 gitignore**） | 含 flag，不入库 |
| 知识库 | `tools/knowledge/` | 公开 writeup，不含凭据 |

## 使用边界（重要）

本项目仅供：

- **CTF 比赛与练习靶场**
- **你有明确书面授权的目标**

**禁止**用于未授权目标。仓库内 `AGENTS.md` 与 `docs/分工-与dsh.md` 明确了跨线红线：
脱库 / 删库 / 批量拉取真实数据一律禁止；「证明能拿到」而非「拿到」是唯一目标。

## 已知安全设计

- `fgs verify` 是**纯代码**逐字比对，无 LLM 参与 —— 防「模型自述成功」
- 读外部路径一律走 `read_capped()`（只认普通文件 + 限长 32MB），防 `/dev/zero` 打崩 VM
- `selfcheck` §6 扫描跟踪文件里的 apiKey / 私钥特征
