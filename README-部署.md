# PiCTF WebUI —— 部署说明（自己机器上用）

> 这是**浏览器工作台**：本机跑一个引擎，浏览器打开 `http://127.0.0.1:30142/` 就是完整界面。
> 和发行方本机用的是同一套：技能库 / 知识库 / 黑板 / 子代理 / 全套 Linux 工具链。

## 先选一条路

| | **Docker（本文之外）** | **WSL 原生（本文）** |
|---|---|---|
| 一键入口 | 双击 `部署.bat` | 双击 `安装.bat` |
| 要装什么 | Docker Desktop | WSL2 |
| 磁盘 | **~25 GB**（镜像 13.5GB + 缓存） | **~2.5 GB** |
| 首次耗时 | 20~40 分钟（构建镜像） | 5~15 分钟（装依赖） |
| 工具链 | 容器内全套（nmap/sqlmap/ghidra/sage） | WSL 里装，同样是 Linux 全套 |
| 隔离 | **强**（容器，不污染宿主） | 弱（直接改你的 WSL 发行版） |
| 适合 | 怕弄脏系统、要完全可复现 | 磁盘紧、想改代码、已在用 WSL |

**选不准就这样**：磁盘 ≥30GB 空闲选 Docker（省心），否则选本文的 WSL 原生路线。

> Docker 路线完整步骤见主 [README.md](README.md) 的「快速开始」；
> 三条命令是 `bash deploy.sh`（Linux/macOS/WSL）或双击 `部署.bat`（Windows）。

---

## 一、要什么

| 项 | 要求 |
|---|---|
| 系统 | **Windows 10 2004+ / 11**（走 WSL）；或任意 Linux（Debian/Ubuntu 系直接跑） |
| 磁盘 | 解压后约 500 MB，装完依赖再留 ~2 GB |
| 网络 | 装依赖时要能上网（apt / pip 走国内镜像已配好） |
| 模型 | 你自己的 API（baseUrl + apiKey），没有就只能看界面不能干活 |

**唯一的门槛**：Windows 上要先有 WSL。没有的话，用【管理员】PowerShell 跑一次：

```powershell
wsl --install
```

然后**重启一次**。完事。

---

## 二、三步走

1. **解压**这个包到任意目录（比如 `D:\pi-ctf`，**路径别带空格**）
2. **双击 `安装.bat`** —— 首次 5~15 分钟（apt 装系统工具 + pip 装 CTF 库）；
   中途要输 WSL 里的 sudo 密码，正常输就行
3. **双击 `启动UI.bat`** —— 浏览器自动打开工作台

进去后第一件事：**左栏「模型」页 → 添加提供商**，填：
- `baseUrl`：你的接口地址（例：`https://opencode.ai/zen/go/v1`）
- `apiKey`：你的密钥
- 额外请求头（有的服务要，比如 `x-opencode-org-id`）

保存后**新开一个会话**才生效。

Linux 用户：`bash install.sh` → `bash start.sh`，同样开 `http://127.0.0.1:30142/`。

---

## 三、装完你会有什么

- **完整工作台**：聊天 / 黑板（FGS）/ 子代理树 / 技能页 / 模型配置 —— 与发行方同款前端
- **全套工具**：`py`（pwntools/z3/pycryptodome/gmpy2/unicorn）、`pggdb`（gdb+gef）、
  `kb`（本地知识库 ~2.5 万块 + 1 万条 PoC）、`fgs`（完整黑板内核）、`poc`（PoC 检索）、
  nmap / tshark / binwalk / vol3 / ffmpeg / bkcrack / jsteg / outguess …
- **16 个 CTF 技能 + references**，5 个 CTF 子代理角色（recon / route / verify / vision / wp）
- **工作纪律**：根目录 `AGENTS.md`（每个会话自动注入），开局先开黑板、flag 逐字核验

常用命令（在 WSL 里或让 agent 自己敲）：

```bash
kb search "weaver sql" --cat web --top 5     # 查知识库
fgs kickoff 我的题 --kind ctf --goal "拿flag"  # 开黑板
fgs show 我的题                              # 看全图
py -c "from pwn import *; print('ok')"       # 带 pwntools 的 python
```

---

## 四、出问题了看这里

| 现象 | 处理 |
|---|---|
| 双击 bat 一闪而过 / 提示没有 WSL | 管理员 PowerShell：`wsl --install` → 重启 |
| `安装.bat` 里 apt 报错 | 网络问题，重跑一次；或先 `wsl -e sudo apt-get update` |
| 浏览器打不开 / 一直转 | `wsl -e bash -lc "tail -50 /tmp/pictf-ui/server.log"` 看日志 |
| 发消息报 401/403 | 模型页的 baseUrl / apiKey / 额外请求头不对，改完开新会话 |
| 想停服务 | 双击 `停止UI.bat`（或 WSL 里 `bash stop.sh`） |
| 想重置我的配置 | 删掉 WSL 里 `~/.pi-ctf/`（会话、模型配置都在那） |
| 装完提示 `py : pwntools/z3 未就绪` | 网络断了：重跑一次 `安装.bat`（Python 库分核心/扩展两批装，失败不中断，可单独补） |
| pip 装得很慢 | 正常，尤其 Python 3.13+ 要现场编译个别包；Ubuntu 22/24（3.10/3.12）通常几分钟装完 |
| 包挪了目录 | 挪完重跑一次 `安装.bat`（会自动修正路径）；或全删重装 |

---

## 五、包目录说明

```
安装.bat / 启动UI.bat / 停止UI.bat   Windows 入口（内部调 WSL）
install.sh / start.sh / stop.sh       Linux 入口
AGENTS.md                            工作纪律（pi 自动读）
skills/ kb/ prompts/ docs/           技能库 / 知识库索引 / 提示词 / 手册
tools/ctfpoc/pocs/                   PoC 原文（1.45 万个；根 pocs/ 已并入此处）
board/                               黑板（fgs.py + 契约；projects/ 是你的题）
tools/                               bin(命令) gobin(gef等) knowledge wordlists
work/ui/                             工作台服务 + 前端（已构建好，不需要 npm）
vendor/node                          离线自带的 Node（不装也行；pi 运行时改由 npm 安装）
agent-template/                      首次安装时铺到 ~/.pi-ctf/agent
workspace/                           干活目录（题目文件放这里）
```

---

## 六、升级

- 换新包：解压覆盖（`~/.pi-ctf/` 是你的配置，不会被覆盖），重跑 `安装.bat`
- 知识库索引想重建：`kb build`（几秒~几十秒）
