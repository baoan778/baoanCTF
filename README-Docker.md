# Docker 方式（推荐；与 WSL 方式二选一）

> 本文件是**实测版**：镜像 `pictf-webui:0.2.0` 已在本机（Win11 + WSL2 + Docker Desktop 29.8.2）构建、起服、跑通端到端。
> 详细证据见 `docs/交付-实施报告.md`，故障处置见 `docs/交付-故障处置.md`。

---

## 一、前提

| 项 | 要求 |
|---|---|
| 系统 | Windows 11 / Windows 10 2004+（走 WSL2），或任意 Linux |
| 引擎 | Docker Desktop（含 compose 插件）/ docker + docker compose |
| 磁盘 | 镜像 **6.07 GB**（含 Go 系 Web 工具与 **14154** 条 nuclei 模板），另留 3 GB 给卷与会话 |
| 网络 | 构建时要能上网（Dockerfile 内全部走国内源） |

**三条预检**（必须都通，缺一条就别往下走）：

```bash
docker --version          # 本机实测：Docker version 29.8.2
docker compose version    # 本机实测：Docker Compose version v5.5.1
docker info --format '{{.ServerVersion}} | {{.OSType}}'   # 本机实测：29.8.2 | linux
```

> `docker compose version` 报 `docker: unknown command` 时，是因为 compose 插件不在
> `%USERPROFILE%\.docker\cli-plugins\` —— 处置见 `docs/交付-故障处置.md` 第 1 条。

---

## 二、四步部署

```bash
cd D:\baoan                   # 本包所在目录
cp .env.example .env          # 然后编辑 .env，填 DEEPSEEK_API_KEY（唯一必填项）
docker compose up -d --build  # 首次约 10~20 分钟
```

浏览器打开 **http://127.0.0.1:30142/**。

Linux 上同样命令（把 `cd D:\baoan` 换成实际路径）。

---

## 三、装完怎么验证（三条硬判据）

```bash
# ① 服务活着
docker ps --format '{{.Names}} {{.Status}}'        # 期望：pictf Up ... (healthy)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:30142/   # 期望：200

# ② CTF 弹药齐
docker exec pictf py -c "import pwn, z3, Crypto, gmpy2; print('ok')"   # 期望：ok

# ③ 知识库能检索
docker exec pictf tools/bin/kb search "nacos 未授权" --kind poc --top 1   # 期望：命中 ≥1 条
```

三条都过 = 环境可用。**模型能不能干活**要另测：浏览器里发一句「只回复四个字：链路正常」，
回「链路正常」即整条链路（UI → 引擎 → DeepSeek）打通。

### 一条命令复现全部判据

懒得逐条敲就跑自检脚本（放在仓库根，跟着包一起交付）：

```bash
bash verify-deploy.sh                # 快速自检，约 10 秒，13 项
bash verify-deploy.sh --full         # 追加 39 条回归（2~4 分钟）
bash verify-deploy.sh --full --e2e   # 再加一次真实模型调用（会消耗一次 API）
```

每项打印 PASS/FAIL 与原始证据，全绿时输出「部署成功」并以 0 退出；有失败项则以 1 退出并指出排查文档。
末尾一行为 `== 结论：PASS=16 FAIL=0 ==` 即通过。

---

## 四、密钥怎么放（别写进镜像）

- 明文**只存在宿主 `.env`** 一个文件里，`chmod 600`，被 `.gitignore` 与 `.dockerignore` 双重排除。
- compose 用 `env_file: .env` 注入为**环境变量**；容器内 `models.json` 里只有占位符 `$DEEPSEEK_API_KEY`。
- 因此 `docker history` 各层查不到明文；`/api/models/keys` 也只回布尔值和变量名。
- **换 key**：改 `.env` → `docker compose up -d`（会重建容器，卷里的会话不动）。

---

## 五、常用命令

```bash
docker compose logs -f              # 跟日志
docker compose restart              # 重启（改 .env 后用它）
docker compose down                 # 停止（数据保留）
docker compose down -v              # 停止 + 删卷（会话与模型配置一起没，慎用）
docker exec -it pictf bash          # 进容器
```

---

## 六、数据在哪

| 内容 | 位置 | 删容器会丢吗 |
|---|---|---|
| 模型配置 / 会话 | `pictf-agent` 命名卷 → 容器 `/home/ctf/.pi-ctf` | 不丢 |
| 题目文件 | 宿主 `./workspace` ↔ 容器 `/opt/pi-ctf/workspace` | 不丢 |
| 黑板（FGS） | 宿主 `./board/projects` ↔ 容器 `/opt/pi-ctf/board/projects` | 不丢 |
| 上传的附件 | 宿主 `./work/intake` ↔ 容器 `/opt/pi-ctf/work/intake` | 不丢 |
| 运行日志（flag 命中记录等） | 宿主 `./log` ↔ 容器 `/opt/pi-ctf/log` | 不丢 |

---

## 七、镜像里有什么

- **基础**：Ubuntu 24.04 + Node 22.23.2 + pi 1.1.0（版本与本包 `vendor/pi` 对齐）
- **Python**：`py`（pwntools 4.15 / z3 5.3.3 / pycryptodome / gmpy2 / unicorn / capstone / volatility3 / yara-python）
- **二进制**：gdb / gef（`pggdb`）/ radare2 / binwalk / foremost / sleuthkit / testdisk / upx / patchelf
- **取证**：tshark / tcpflow / masscan / nmap / exiftool / qpdf / poppler / mupdf / multimon-ng / mediainfo
- **隐写**：java + xvfb（f5 需要，无显示会 HeadlessException）/ steghide / zsteg 依赖
- **Web**：sqlmap / yara / ffuf / httpx / katana / naabu / nuclei（含 **14154** 条模板）
- **信息收集**：gau / urlfinder（替代缺失的 `osint.web` 能力提供者）
- **工作台**：`kb`（30649 块知识库）/ `poc`（1.1 万条 PoC）/ `fgs`（黑板）/ `py` / `pggdb`

---

## 八、已知不支持 / 已知缺口

- **Windows 侧 IDA / Ghidra 直调**：容器里没有 interop 通道。要用那个就走 WSL 方式装（见 `README-部署.md`）。
- **Go 系 Web 工具已装齐**（2026-10-31 起）：`ffuf 2.1.0-dev` / `httpx v1.12.0` / `katana v1.8.0` / `naabu 2.7.0` / `nuclei v3.11.1`，随镜像带 **14154 条** nuclei 模板（`/home/ctf/nuclei-templates`）。
  Go 工具链本身**没有留在镜像里**（编译完即删，省约 500 MB）；要再装别的 Go 工具，把 `docker/Dockerfile` 里 ⑤·5 那段的 `rm -rf /usr/local/go` 去掉重建即可。
  `toolreg check` 实测「可用 **63/63**」，无缺项（含 2026-11-01 补登记并安装的 `gau` / `urlfinder`）。
- **`web_search` / `fetch_content` 两个能力没有内置实现**：pi 本体不带 Web 工具，工作台的这两个能力要第三方扩展 `pi-web-access`（npm 包，非官方，需 Brave/Tavily 等搜索 key）才会出现。
  出于供应链与「没 key 装了也用不了」的考虑，**交付镜像不预装**。要开就自己 `npm i -g pi-web-access` 并配 key，或改用 `curl` / `gau` 这类命令行路径。
- **sage / hashcat / john 未装**（crypto 类可选件）。
- 构建日志**不要**写进本目录（会污染 build context，导致 `transferring context` 慢到几分钟）。写 `$HOME` 下。

---

## 九、Docker 数据盘位置（实测：已在 D 盘）

| 检查项 | 实测值 |
|---|---|
| 数据目录配置 | `C:\Users\<你>\AppData\Roaming\Docker\settings-store.json` → `"CustomWslDistroDir": "D:\\DockerData\\DockerDesktopWSL"` |
| 虚拟磁盘 | `D:\DockerData\DockerDesktopWSL\disk\docker_data.vhdx` = 12096372736 字节（≈11.3 GB；2026-10-31 已用 `Optimize-VHD -Mode Full` 从 25294798848 字节压到此处） |
| C 盘 | `AppData\Local\Docker` 仅 ~15 MB 运行时文件，无 `wsl` 子目录 |

镜像 6.07 GB、构建缓存与三个卷都在这个 vhdx 里。换机时把 Docker Desktop「Settings → Resources → Advanced → Disk image location」指向 D 盘即可。

**磁盘回收（重建镜像后 vhdx 只增不减，需手动压）**：

```powershell
# 必须以管理员身份运行（脚本内含 wsl --shutdown + Optimize-VHD -Mode Full，失败回落 diskpart）
D:\baoan\compact-docker-vhdx.bat
```

压缩前先 `docker compose down`。2026-10-31 实测：25294798848 B（23.56 GiB）→ 12096372736 B（11.27 GiB），释放 **12.29 GiB**；压缩后 `docker compose up -d` 重启，镜像与卷内容不受影响（当日复检 16/16 PASS）。

## 九点五、实战后审计修复（2026-11-01，跑通一道实战题之后做的）

真实跑题（zkRSA）后复查暴露 3 处问题，已全部修好并在镜像里生效：

| 编号 | 问题 | 现象（实测） | 修复 |
|---|---|---|---|
| P0 | bind mount 权限 | 容器内 `ctf` 写 `/opt/pi-ctf/workspace` → `Permission denied`（Docker Desktop 把宿主目录重建成 `root:root 0755`）→ `fgs kickoff` 建不了项目、题面落不了盘 | 入口以 root 起，先 `chown/chmod` 四个挂载目录，再 `setpriv` 降权到 `ctf`（`PICTF_ENTRY_DROPPED=1` 防重入） |
| P1 | `osint.web` 无提供者 | 每个会话都报 `role_warn ctf: 本机没有能提供能力 osint.web 的工具` | 镜像补装 `gau` + `urlfinder` |
| P2 | 附件/日志不持久 | 上传的题面、`flag-watch.jsonl` 都在镜像层，重建容器即丢 | `compose.yaml` 增 `./work/intake`、`./log` 两个 bind mount |

自检脚本同步加了第 14 项「挂载目录可写性」，四项任一不可写就 FAIL。

---

## 十、回归与自检现状（2026-10-31 实测）

| 项 | 结果 |
|---|---|
| `board/tests/test_*.py` 39 条（容器内） | **PASS 39 / FAIL 0 / SKIP 0** |
| `tools/hand/selfcheck --quick` | 通过（17 条警告），9/9 CTF 题型 |
| `tools/skilllint.py` | 无硬失败（5 条「SKILL.md > 9000 字节」告警） |
| `tools/toolreg.py check` | **可用 63/63**（Go 系 5 件 + `gau`/`urlfinder`） |
| 端到端 `POST /api/command` | success，会话记录 model=`deepseek-flash` |
| 密钥泄漏扫描 | 镜像层 0 命中；容器内 0 命中；`models.json` 只有占位符 |
| 挂载目录可写性（ctf 身份实测） | workspace / board.projects / work.intake / log 四项全可写 |
