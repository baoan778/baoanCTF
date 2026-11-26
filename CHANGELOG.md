# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/)，版本号遵循 [SemVer](https://semver.org/)。

## [Unreleased] — 首发准备

### Added

**开源交付件**
- README / LICENSE(MIT) / CONTRIBUTING / SECURITY / NOTICE / CHANGELOG / .gitattributes
- 一键部署：`deploy.sh`（Linux/macOS/WSL/Git-Bash）+ `部署.bat`（Windows 双击）
- CI（ubuntu + windows 双平台）：ruff 致命级 → shell 体检 → 测试数一致 → 152 条回归 → 交付物完整性 → 密钥扫描
- Issue / PR 模板（`.github/`）
- `pyproject.toml`：ruff 规则与排除项固化，本地与 CI 同一份配置

**命令与判据**
- `handoff-card`：≤60 行会话交接卡，替代整读状态快照；`--advise` 按 5 条可算判据建议「该开新对话了」
- `bashfind`：Python 里起 bash 的唯一入口（解析绝对路径 + 真跑探针）
- `reflect` 的 `FILTER_BLOCKED` 判定：识别「输入过滤器把空间封死」
- `secret-scan`：密钥泄漏扫描（原为 CI 内联 grep）

### Changed

- **人手写入口清单收成单一可写点**：原 `make-bin.sh` / `hand/selfcheck` / `bin/selfcheck` 各存一份 `HAND_TOOLS`，
  加工具要改三处且历史真漏过 → 现在只有 `make-bin.sh` 写，落盘 `tools/hand/.hand-tools`，两份 selfcheck 读文件
- **部署路线选择**：`README-部署.md` 加 Docker vs WSL 原生对照表（25GB/20~40min vs 2.5GB/5~15min）
- **BASE_URL 统一**：`.env.example` 无 `/v1`，脚本有 → 统一为不带

### Fixed

**判据类（都是「检查器自己站不稳」）**
- **`FILTER_BLOCKED` 在真实数据上误判 4/7** → 改为「极小输入」判据，误判归零（§4b）
- **两份基线断言绑活账本**（真跑几道题就红，同一个病两周犯两次）→ 改读冻结快照，快照不在则 SKIP（§4d）
- **缺清单时打绿**（"0 项检查当然一致"）→ 改为 fail
- **`test_selfcheck` 判据过宽**：`"init"` 裸关键字把 `git init` 误判成 `fgs init` → 只认调用形态
- **`test_replay_env` 的 `py` 判据**：只查命令存在，而 Windows 自带 `py` = Python Launcher → 改看真能不能 import
- **`test_selfcheck` 两组过时判据**：`tools/vendor/` 实际是合法跟踪目录；`.archive-*/` 规则带前导点却被写成 `archive/`

**跨平台类（Windows 开发，Linux CI 第一次真跑才暴露）**
- **Python 里裸名 `bash` 恒失败**（命中 System32 的 WSL 存根）→ `bashfind` 解析绝对路径
- **`verify-deploy.sh` 在 MSYS bash 5.3.20 下语法错**（嵌套引号被截断）→ 改用 heredoc
- **113 个 `tools/bin` 文件缺可执行位**（`core.filemode=false`，chmod 从未进 git）→ `update-index --chmod=+x`
- **329 个带 shebang 的 `.py` 缺可执行位** → 同上，累计 755 共 458 个
- **Windows runner 用 PowerShell 跑 bash 脚本** → `defaults.run.shell: bash`
- **Windows Python 输出 cp1252 崩中文**（9 个测试 UnicodeEncodeError）→ `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`
- **`/tmp` 硬编码**（Windows 没有 /tmp）→ `tempfile.gettempdir()`
- **ruff 递归扫到 `tools/knowledge/` 第三方脚本**（39 个语法错，两平台全红）→ `pyproject.toml` 排除

**业务类**
- **余额闸按端点判**（原按 provider 名字，被「换端点不换名」骗过，导致整条链被拦）
- **「run 已终结」落成状态标记**（原每 60s 重判一次，日志被同一句刷满）
- **CI 密钥扫描自匹配**：模式串本身含 `-----BEGIN ... PRIVATE KEY-----` → 抽成脚本并排除自身
- **CI 失败日志只 tail -20**（真实断言多在中间）→ 先打全部 ✗/Traceback 行
- **`NOTICE` 邮箱写成 HTML 实体** `&lt;...&gt;` → 改回尖括号
- **README 一条错误命令** `python3 tools/../selfcheck`（路径不存在）→ `bash tools/bin/selfcheck`
- `work/` 下临时片段拉红全仓 shell 体检 → 按非仓库脚本跳过
- 2 个 0 字节评测日志被跟踪 → 移出 + `.gitignore` 防复发

### Docs

- `docs/经验-工程纪律.md` 新增 §4b（新判据必须先在历史数据上回放）、
  §4c（环境故障与业务失败必须分流）、§4d（基线断言必须绑冻结快照）
- 新增 6 条踩坑记录（已并入 `docs/经验-工程纪律.md`）
- README 重写：去掉过时数据（59 → 152 条测试）、加 Mermaid 架构图、去掉无出处的性能声明

### Removed

- **实战残留**：`work/solve`（110MB，含 279 个真实 flag 的文件）、`work/_baseline-*`（47MB）
- **失效环境**：`tools/venv`（`bin/python` 坏软链，431MB）、`vendor/node`（Linux 版，Windows 不可执行，120MB）
- 运行日志 12.5MB、`.archive-*`、`__pycache__`、空目录
- 总占用 2.4GB → 1.8GB

### Verified

- `vendor/pi` 删除后 `docker compose build` 成功（新用户必经路径）
- 临时容器实测：pi 1.5.0 / node v22.23.2 / pwn+z3+Crypto 可导入 / 14 个 CTF 工具在 / kb 索引可用
- CI 双平台九步全绿（run 45583965378）
