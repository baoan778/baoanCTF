# 来源与许可

- **上游**：NOPTrace 应急响应手册 — `https://books.noptrace.com/linux/` 、 `/windows/`
- **许可**：上游**未声明开源许可** → 本目录只作**本机离线查阅**，不外发、不再分发。
- **到达路径**：操作者提供的 CyberStrikeAI 工作区
  `.../ir_skill_extract/企业应急响应指导Skill/`（该技能包把手册整理成了 SOP）
- **体量**：Linux 282KB / Windows 466KB（扫描格式的整本手册）
- **怎么用**：**不要整读** —— 先按 `INDEX.md` 或本目录的章节标题定位，再 `grep -n` 进到那一段。
  例如：
  ```bash
  grep -n "^#" tools/knowledge/noptrace/NOPTrace-Linux.md | head -60     # 看目录
  grep -n "内存马\|无文件\|memfd" tools/knowledge/noptrace/NOPTrace-Linux.md
  ```
- **叠加关系**：`skills/ir-response/references/*.md` 是从这套手册 + Solar 题解提炼的**可执行 SOP**；
  手册本体用于查**某个具体技术的细节**（eg. 某类隧道怎么定位、某个日志字段在哪）。
