# 来源与许可

- **上游**：RiftX — https://github.com/Ch1nfo/RiftX （本机副本 `/mnt/d/AI/claudecode/_ref/RiftX`）
- **许可**：MIT License, Copyright (c) 2026 Ch1nfo（见同目录 `LICENSE-RiftX-MIT.txt`）
- **搬运范围**：`recommended-skills/` 全目录（36 个 skill 的 SKILL.md + references/ + assets/ + scripts/）
- **剔除**：`*.zip`、`*.swf`（不可读归档，无参考价值）
- **搬运日期**：2026-09-28
- **用途**：pi-lab 离线知识基线（对应 CyberPenda ADR 0037 `/opt/knowledge` 的设计意图）。
  本机无外网时，方法论 / payload / 字典必须落在盘上。
- **注意**：RiftX 的 SKILL.md 正文里含 RiftX 自己的工具调用语法（`browser navigate`、`record_finding`、
  `spawn_subagent`、`request_detail` 等），**那是它工作台的 API，本机没有**。
  读的时候只取「方法论 + payload + 脚本」，工具调用段落直接忽略，换成 pi-lab 的等价物
  （`httpx`/`katana`/`curl`/`sqlmap`/`py`）。
