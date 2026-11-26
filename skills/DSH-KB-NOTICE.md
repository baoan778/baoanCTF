# DSH CTF 知识库导入说明

## 来源

- 来源库：`/mnt/c/Users/幺零零/.dsh/.agent-presets/ctf-solver/refs`
- 许可：**MIT License, Copyright (c) 2026 Lukasz Jagiello**（原文见 `DSH-CTF-KB-LICENSE.txt`）
- 该库自述共 142 篇，模块划分：`ctf-ai-ml` / `ctf-malware` / `ctf-osint` / `ctf-web` /
  `ctf-pwn` / `ctf-reverse` / `ctf-crypto` / `ctf-forensics` / `ctf-misc`，
  另有 `discipline/`（跨模块纪律）、`operator-playbook/`、`solve-challenge/`、`ctf-writeup/`。

## 本次导入了什么、为什么

操作者要求：「新建题目里缺一个 ai 类型的题目」+「确保每个题目都有对应的 skill」。
对照下来本项目原本只有 web/pwn/crypto/reverse/forensics/misc/binary 七个题型，
**ai-ml / malware / osint 三个题型既没有分类也没有 skill** —— 界面上选不到、
收单器会拒绝、worker 也没有对应战术卡可读。所以只导入这三个模块：

- `skills/ctf-ai-ml/` — AI/ML（大模型 · 对抗样本 · 模型提取）（SKILL.md + references）
- `skills/ctf-osint/` — OSINT（情报检索/地理定位）（SKILL.md + references）
- `skills/ctf-malware/` — 恶意样本分析（SKILL.md + references）
## 改动了什么（除这三处外正文未动）

1. frontmatter：`compatibility` 改成本项目口径（Windows 宿主 + WSL 执行、按题探测），
   `allowed-tools` 对齐本项目（Bash Read Write Edit Grep Glob）；
2. references 链接：上游是平铺 `x.md`，本项目约定 `references/x.md`，已补前缀；
3. 每篇末尾追加本项目统一的纪律段（能力检查 / 值的三级区分 / 本类特有纪律），
   与 BTFly 那批导入的同名段落逐字一致 —— 让不同来源的题型卡在同一条纪律下工作。

## 没有导入什么（不要以为整个库都进来了）

`discipline/` `operator-playbook/` `solve-challenge/` `ctf-writeup/` 以及
web/pwn/crypto/reverse/forensics/misc 六类**没有**导入：本项目这六类已有 BTFly 版
（见 `BTFly-KB-NOTICE.md`），两套并存会形成两本规则书、冲突时无从裁决。
若要融合纪律层，需要单独一次"口径对齐"的活，不能直接铺文件。

## 运行前提

上游 SKILL.md 里 `pip install torch transformers …` 这类命令是**上游环境**的写法，
本项目 worker 在 WSL 里跑，Python 库是否装好以「工具」页探测为准；没装就先装，
装不上就走 `refs` 里不需要该库的路线（例如只看权重差异、纯 numpy 计算）。
