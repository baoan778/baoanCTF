# =============================================================================
# PiCTF WebUI —— 全环境镜像（与发行方本机同款 Linux 工具链）
#
# 构建： docker compose build          国内源已配：清华 apt/pip + npmmirror node/pi
#
# 相对上游的修改（本次 Docker 化适配，逐条都有验证）：
#   ① 加 USER ctf（上游 entrypoint 引用了 /home/ctf 却以 root 运行，HOME 对不上）
#   ② 补 java + xvfb（f5 隐写必需）、sqlmap / yara / testdisk / qpdf / poppler /
#      mupdf / multimon-ng / mediainfo / tcpflow / masscan（TOOLS.md 里登记过的命令）
#   ③ 把 tools/bin 加进 PATH（server.mjs 用 spawn("pi") / spawn("fgs") 直接找命令，
#      上游镜像里它们不在 PATH → 打开工作台就 500）
#   ④ 不再从 npm 拿 pi 的「运行期」版本号漂移风险：锁定 1.1.0（与本包 vendor/pi 同版）
#   ⑤ 首次启动由 docker/provision-agent.sh 用环境变量生成 models.json
#      （apiKey 写成 $DEEPSEEK_API_KEY 占位符 —— 明文密钥永不落盘进镜像层）
#   ⑥ 瘦身：不带 gcc/cmake；apt 缓存与文档全清
# =============================================================================
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=Asia/Shanghai

# ① CA 证书（裸镜像没有，没它 HTTPS 源全废）
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# ② 换清华源
RUN sed -i 's|http://archive.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g; s|http://security.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/ubuntu.sources

# ③ 系统工具
#    pwn 调试：gdb patchelf binutils strace ltrace
#    取证/流量：tshark binwalk squashfs-tools testdisk sleuthkit foremost tcpflow masscan
#    杂项：      ffmpeg sox multimon-ng mediainfo qpdf poppler-utils mupdf-tools yara
#    java（f5）：default-jre（**不能是 -headless**，它没有 libawt_xawt.so）+ xvfb-run
#               （JpegEncoder 继承 awt.Frame，无显示会 HeadlessException）
#    web：      sqlmap（tools 清单里登记过）
#    vpn：      openvpn iproute2 sudo
#               ★ 2026-11-04：容器里原来一个都没有 → `tsec vpn` 连 status 都崩。
#               现在装上，隧道在容器内起时只剩「devices + NET_ADMIN」这一个前提
#               （compose.yaml 里有注释掉的两行，需要时打开）。默认仍按 Q18=A 归宿主。
# ★ 2026-11-19 补 iputils-ping + dnsutils：xben-039/078/083 三题的侦察脚本第一步就是
#   `ping -c1 <target>`，而镜像里没有 /bin/ping → 直接 command not found，白丢一轮。
#   dig/nslookup 同理，是 recon 常用件。
RUN set -eu; \
    ok=0; \
    for i in 1 2 3; do \
      if apt-get update -o Acquire::Retries=5 && \
         apt-get install -y --no-install-recommends -o Acquire::Retries=5 \
      python3 python3-venv python3-pip \
      gdb patchelf file binutils xxd bc netcat-openbsd curl wget git jq zip unzip p7zip-full xz-utils \
      rsync \
      nmap tshark binwalk squashfs-tools testdisk sleuthkit foremost tcpflow masscan ffmpeg sox multimon-ng \
      qpdf poppler-utils mupdf-tools mediainfo yara sqlmap strace ltrace socat \
      radare2 gdb-multiarch \
      default-jre xvfb fontconfig libzbar0 libmagic1 libgmp10 \
      libimage-exiftool-perl apktool steghide tesseract-ocr wabt \
      libcapstone-dev libssl-dev pkg-config \
      iproute2 openvpn sudo hashcat john upx-ucl \
      iputils-ping dnsutils \
      sshpass php-cli sqlite3 redis-tools tcpdump mariadb-client \
      qemu-user-static ruby ruby-dev cmake make g++ \

      ; then ok=1; break; fi; \
      echo "[retry] apt 第 $i 轮失败（DNS/镜像抖动），10s 后重试"; sleep 10; \
    done; \
    [ "$ok" = 1 ] || { echo "[FATAL] apt 三轮都失败，请检查网络/镜像"; exit 1; }; \
    ln -sf /usr/bin/mariadb /usr/bin/mysql; \
    ln -sf /usr/bin/mariadb-dump /usr/bin/mysqldump; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/* /usr/share/locale/* /var/cache/apt/*

# ④ Node 22（npmmirror）——pi 要求 >=22.19
#   ★ 2026-10-30 实测事故：某次 build 拉到的 node 二进制被截断成**恰好 60MiB**，
#     `node -v` 直接段错误（rc=139）→ 容器 "Restarting (139)" 无限重启，
#     而 build 仍报成功。现在下载后**强制验完整性**：大小下限 + tar 解包 + 真跑一次。
RUN set -eu; \
    curl -fsSL --retry 3 --retry-delay 3 --connect-timeout 15 -m 460 \
      --speed-limit 20480 --speed-time 60 \
      -o /tmp/n.tar.xz \
      https://registry.npmmirror.com/-/binary/node/v22.23.2/node-v22.23.2-linux-x64.tar.xz; \
    sz=$(stat -c %s /tmp/n.tar.xz); \
    echo "[node] tarball = $sz bytes"; \
    [ "$sz" -gt 20000000 ] || { echo "[FATAL] node tarball 只有 $sz 字节（应 >20MB），下载被截断"; exit 1; }; \
    tar -xJf /tmp/n.tar.xz -C /usr/local --strip-components=1; \
    rm -f /tmp/n.tar.xz; \
    sz=$(stat -c %s /usr/local/bin/node); \
    echo "[node] binary = $sz bytes"; \
    [ "$sz" -gt 90000000 ] || { echo "[FATAL] node 二进制只有 $sz 字节（应 >90MB），解包被截断"; exit 1; }; \
    node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 22 ? 0 : 1)" \
      || { echo "[FATAL] node 跑不起来/版本过低"; exit 1; }; \
    node -v; npm -v

# ⑤ pi 运行时（npmmirror）+ 清 npm 缓存
#   2026-11-03 升级 1.1.0 → 1.5.0（与宿主 vendor/pi 同版）。
#   升级前已验：扩展清单 9 个与 1.1.0 逐项 diff 为空；flag-watch 依赖的 tools/flagwatch.mjs 可 import。
RUN npm i -g @earendil-works/pi-coding-agent@1.5.0 --registry=https://registry.npmmirror.com \
 && npm cache clean --force 2>/dev/null; rm -rf /root/.npm /usr/local/lib/node_modules/npm/docs

# ⑤·5 Go 系 Web 工具：ffuf / httpx / katana / nuclei / naabu
#   这 5 个是 toolreg 里仅剩的「不可用」项（装完 61/61）。
#   做法：临时拉 Go 工具链 → GOPROXY 走国内 → 编译安装静态二进制 → **删掉工具链与模块缓存**
#   （只留可执行文件，省 ~500MB；后续想再装 Go 工具，把这一层改成保留 /usr/local/go 即可）
#   ⑥·5 之后的 gau / urlfinder 用于补 osint.web 能力（原镜像缺提供者，每个会话都报 role_warn）。
#   注意：RUN 内部**不能写 # 注释行**（续行会被 join 成一行，注释会把后面所有命令吃掉），
#   版本自检一律带 || true —— 各工具打印版本的参数不统一（gau 只认 --version，写 -version 会退出码 2）。
#   ★ 2026-11-04：这层曾挂死 34 分钟零日志（curl 无超时，镜像端半开连接就一直等）。
#     现在每跳都带 --connect-timeout 15 -m 420 --speed-limit 10240/60 —— 卡住就换源，不再无限等。
RUN set -eu; \
    ok=0; \
    for u in https://mirrors.aliyun.com/golang https://golang.google.cn/dl; do \
      if curl -fsSL --retry 2 --retry-delay 3 --connect-timeout 15 -m 420 \
              --speed-limit 10240 --speed-time 60 \
              -o /tmp/go.tgz "$u/go1.27.3.linux-amd64.tar.gz"; then ok=1; break; fi; \
      echo "[retry] $u 不通，换下一个 Go 镜像"; \
    done; \
    [ "$ok" = 1 ] || { echo "[FATAL] Go 工具链下载失败（两个镜像都不通）"; exit 1; }; \
    tar -xzf /tmp/go.tgz -C /usr/local; rm -f /tmp/go.tgz; \
    export PATH=/usr/local/go/bin:$PATH GOPATH=/tmp/gopath GOBIN=/usr/local/bin \
           GOMODCACHE=/tmp/gopath/pkg/mod GOCACHE=/tmp/gocache \
           GOPROXY=https://goproxy.cn,https://mirrors.aliyun.com/goproxy/ GOFLAGS=-trimpath; \
    go version; \
    install_tool() { n="$1"; shift; \
      for i in 1 2 3; do if timeout 900 go install "$@"; then echo "[ok] $n"; return 0; fi; \
        echo "[retry] $n 第 $i 轮失败（含 900s 超时）"; \
        find "$GOMODCACHE/cache/download" -name "*.tmp" -delete 2>/dev/null; \
        sleep $((i * 10)); done; \
      echo "[FATAL] $n 安装失败"; return 1; }; \
    install_tool ffuf   github.com/ffuf/ffuf/v2@latest; \
    install_tool httpx  github.com/projectdiscovery/httpx/cmd/httpx@latest; \
    install_tool katana github.com/projectdiscovery/katana/cmd/katana@latest; \
    install_tool naabu  github.com/projectdiscovery/naabu/v2/cmd/naabu@latest; \
    install_tool nuclei github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest; \
    install_tool gau github.com/lc/gau/v2/cmd/gau@latest; \
    install_tool urlfinder github.com/projectdiscovery/urlfinder/cmd/urlfinder@latest; \
    rm -rf /usr/local/go /tmp/gopath /tmp/gocache; \
    echo "--- 版本自检 ---"; \
    for bin in ffuf httpx katana naabu nuclei gau urlfinder; do \
      command -v "$bin" >/dev/null || { echo "[FATAL] $bin 没装上"; exit 1; }; \
    done; \
    ffuf -V | head -2; (httpx -version | head -2) || true; (katana -version | head -2) || true; \
    (naabu -version) || true; (nuclei -version | head -8) || true; (gau --version) || true; (urlfinder -version | head -2) || true; \
    echo "[ok] Go 系 Web 工具就位"

# ⑥ Python 环境（先装依赖，再拷内容）
#   ★ 2026-11-01 实测：原顺序是「COPY . → venv+pip」，任何一处源码改动都会让 pip 层
#     全部重跑（十几分钟），且 pip 单源卡住时整次构建静默挂死（日志 12 分钟零增长）。
#     现在把 requirements 先拷进来、venv 单独成层 → 只要 requirements*.txt 不变，
#     改代码不影响这一层；pip 走「多镜像轮询 + 单次超时」，卡住会快速失败换下一个源。
#   ★ 2026-11-04：pip 自升级后必须清 __pycache__，并锁 pip<27 —— 否则下次 install
#     读到旧 frozen 模块的 .pyc 直接崩（实测 `SystemError: <frozen posixpath>:169:
#     unknown opcode 151`），pip 停在 retry 不动、构建挂死 1 小时且日志零增长。
COPY requirements-core.txt requirements-extra.txt /opt/pi-ctf/

RUN set -eu; \
    python3 -m venv /opt/pi-ctf/tools/venv; \
    PIP=/opt/pi-ctf/tools/venv/bin/pip; \
    MIRRORS="https://pypi.tuna.tsinghua.edu.cn/simple https://mirrors.aliyun.com/pypi/simple https://mirrors.ustc.edu.cn/pypi/simple https://pypi.org/simple"; \
    pip_try() { \
      for m in $MIRRORS; do \
        for i in 1 2; do \
          if "$PIP" install -q --no-cache-dir --prefer-binary --timeout 30 --retries 2 -i "$m" "$@"; then \
            echo "[pip ok] $m :: $*"; return 0; fi; \
          echo "[pip retry] $m (第 $i 次) :: $*"; sleep 3; \
        done; \
      done; \
      echo "[pip FAIL] 所有镜像都装不上: $*"; return 1; }; \
    /opt/pi-ctf/tools/venv/bin/python -m pip install -q --no-cache-dir -U 'pip<27' || echo '[warn] pip 自升级失败，用 venv 自带版本继续'; \
    find /opt/pi-ctf/tools/venv -name '__pycache__' -type d -prune -exec rm -rf {} + ; \
    pip_try -r /opt/pi-ctf/requirements-core.txt; \
    pip_try -r /opt/pi-ctf/requirements-extra.txt; \
    pip_try setuptools wheel; \
    "$PIP" --version; echo "[ok] venv 就绪"


# ⑥·1b ★ 2026-11-19 修（P0 实测事故）：让系统 python3 也能 import venv 里的库
#   现象：全库 111 条命令用 `python3`、仅 21 条用 `py`，于是 requests/PIL/numpy/jinja2
#         全部 ModuleNotFoundError。**不是缺库，是解释器选错**：库都在 tools/venv 里，
#         而 venv 的 pyvenv.cfg 是 include-system-site-packages=false → 两套互不可见。
#   修法：在系统 python 的 user site-packages 放一个 .pth，把 venv 的 site-packages 追加上去。
#     .pth 里的路径被 site.py **追加到 sys.path 末尾**，因此 ★ 绝不遮盖标准库
#     （实测 json/re/os/subprocess/pathlib/http/socket/ssl/hashlib/email/xml 全部仍来自
#      /usr/lib/python3.12，无一来自 venv）。
#   为什么不用 symlink tools/bin/python3 -> venv python：那会改变 `python3` 的 sys.prefix，
#     pip 安装位置跟着漂，系统脚本（binwalk 等）行为也可能变。
#   为什么用 user site 而非 dist-packages：镜像里 dist-packages 归 root，
#     entrypoint 降权到 ctf 后写不进去（实测 Permission denied）。
#   ★ 2026-11-19 构建实测（第一次构建失败在这里）：`ENV HOME=/home/ctf` 定义在本文件
#     第 396 行 —— 比这一层晚得多，于是构建期 HOME=/root，
#     site.getusersitepackages() 返回 /root/.local/...，.pth 写到了 ctf **看不见的位置**，
#     紧接着的 `import requests` 断言必然失败（exit 1）。
#     修法：这一层显式带 `HOME=/home/ctf`，让 python 的 user site 与写入目录一致。
#     ★ 不能靠"把 ENV HOME 提到前面"解决：后面还有好几层以 root 跑，
#       改它们的 HOME 会牵连 nuclei 模板缓存（:344 显式 HOME=/home/ctf）等行为。
#   ★ 2026-11-19 构建实测（第二次构建失败在这里）：这一层**不能**写
#     `chown -R ctf:ctf /home/ctf/.local` —— ctf 用户要到 ⑧·4（本文件 :336）才
#     `useradd` 出来，构建期执行到这里时 chown 报 `chown: invalid user: 'ctf:ctf'`，
#     整层 exit 1（而 import 断言其实早就全过了，报错行很容易被误读成「库没装上」）。
#     属主交给 ⑧·4 的 `chown -R ctf:ctf /opt/pi-ctf /home/ctf` 收口（递归覆盖 .local），
#     运行期 entrypoint ⓪ 段还有 tools/home-fix.sh 再兜一次。
#     目录与 .pth 默认 root:root 755/644 —— ctf 只读即可，不影响 site.py 加载。
RUN set -eu; \
    export HOME=/home/ctf; \
    D=/home/ctf/.local/lib/python3.12/site-packages; \
    mkdir -p "$D"; \
    echo /opt/pi-ctf/tools/venv/lib/python3.12/site-packages > "$D/_pi_venv.pth"; \
    /usr/bin/python3 -c "import requests, PIL, numpy, jinja2, pwn, z3, Crypto, gmpy2, lxml, bs4, flask" \
      || { echo "[fatal] python3 加了 .pth 仍 import 失败"; \
           echo "[diag] python3=$(/usr/bin/python3 -V 2>&1)"; \
           echo "[diag] usersite=$(/usr/bin/python3 -c 'import site;print(site.getusersitepackages())' 2>&1)"; \
           echo "[diag] pth=$(cat "$D/_pi_venv.pth" 2>&1)"; \
           echo "[diag] venv has: $(ls /opt/pi-ctf/tools/venv/lib/python3.12/site-packages | tr '\n' ' ' | cut -c1-400)"; \
           /usr/bin/python3 -c "import sys;print('[diag] sys.path:', sys.path)"; \
           exit 1; }; \
    /usr/bin/python3 -c "import json, re, os, subprocess, pathlib, sys; \
      v = '/opt/pi-ctf/tools/venv'; \
      bad = [m.__name__ for m in (json, re, os, subprocess, pathlib) if (getattr(m, '__file__', '') or '').startswith(v)]; \
      print('[fatal] 标准库被 venv 遮盖:', bad) if bad else None; \
      sys.exit(1 if bad else 0)"; \
    echo "[ok] python3 可见 venv 库（标准库未被遮盖，已断言）"; \
    /usr/bin/python3 -c "import cv2" 2>/dev/null \
      || echo "[warn] cv2 不可用（opencv 装失败：图片题会退化为手写解析，不阻断构建）"

# ⑥·2 sage 命名空间（passagemath 专用）
#   ★ 2026-11-04：passagemath 没有上游的 sage/all.py 与 all_cmdline.py —— 缺了它
#     `from sage.all import *` 和 `sage script.sage` 都崩（实测）。
#     shim 扫 site-packages 里现成的 all__sagemath_*.py 拼出全量命名空间，并冒烟一次。
#     单成一层：sage 装失败不该拖垮整个 venv（缺 sage 只是 crypto 少把刀，不是起不来）。
COPY docker/sage-shim.sh /opt/pi-ctf/docker/sage-shim.sh
RUN set -eu; \
    bash /opt/pi-ctf/docker/sage-shim.sh /opt/pi-ctf/tools/venv \
      || echo "[warn] sage 命名空间补全失败（crypto 大格/Coppersmith 会缺；其余不受影响）"

# ⑦ 内容（技能/知识库/黑板/前端…）
#    注意 .dockerignore 已排除 tools/venv、vendor/node、.env —— 宿主产物不会污染镜像
#    COPY 是合并语义：上面建好的 tools/venv 不会被覆盖掉（.dockerignore 里也不含它）
WORKDIR /opt/pi-ctf
COPY . /opt/pi-ctf

# ⑦·2 内容完整性断言（★ 2026-11-01 实测事故：buildx 缓存层被截断，
#     COPY 进镜像的 work/ui/server.mjs 变成 0 字节、entrypoint.sh 变 0 字节，
#     容器启动报 'exec format error' / 工作台白屏，而 build 报成功。
#     下面几个文件是「镜像必须非空」的硬锚点，任一为 0 就当场让 build 失败。）
RUN set -eu; \
    check() { sz=$(stat -c %s "$1"); if [ "$sz" -lt "$2" ]; then echo "[FATAL] $1 只有 $sz 字节（应 ≥$2）→ 缓存层被截断，先 docker builder prune -af 再重建"; exit 1; fi; echo "[size ok] $1 = $sz"; }; \
    check /opt/pi-ctf/work/ui/server.mjs 100000; \
    check /opt/pi-ctf/work/ui/web/dist/index.html 200; \
    check /opt/pi-ctf/board/fgs.py 100000; \
    check /opt/pi-ctf/tools/kb.py 20000; \
    check /opt/pi-ctf/docker/entrypoint.sh 1000; \
    check /opt/pi-ctf/docker/provision-agent.sh 1000; \
    check /opt/pi-ctf/requirements-core.txt 100; \
    echo "[ok] 内容完整性通过"

# ⑦·3 前端产物：在**镜像内**重建 dist（★ 2026-11-18 加）
#   为什么必须在这里建：⑦·1 的 COPY . 只是把宿主 dist 原样搬进来，而 dist 在宿主的
#   更新节奏取决于「谁最后跑了 uibuild」。镜像若直接吃宿主那份，就会出现「镜像里的
#   前端是几个月前的」，且**没有任何构建环节会纠正它**。
#   实测事故（2026-11-18）：镜像是 11-13 建的，层里躺着一份**带已删口令闸门**的老
#   bundle（grep 命中「口令」与 /api/auth），而 compose 当时不挂 dist → 只要
#   docker compose up --force-recreate（不 build）就静默退回老前端：不白屏，只有口令框
#   悄悄回来。现在的两道保险是「compose 只读挂宿主 dist」+「这条在镜像内重建」。
#   依赖装进镜像（不是临时删掉）：容器里跑 uibuild 时就能**离线**重建，不必再连 npm 源。
#   失败策略：npm 拉不到包时 warn 并**保留 COPY 进来的那份**（仍要过下面的一致性子句），
#   不让一个网络问题把整次构建打死；但如果 rebuild 声称成功、产物却引不到 JS，则硬失败。
RUN set -eu; \
    cd /opt/pi-ctf/work/ui/web; \
    ok=0; \
    if [ ! -d node_modules ]; then \
      for reg in https://registry.npmmirror.com https://registry.npmjs.org; do \
        if npm install --no-audit --no-fund --registry="$reg" >/tmp/npm-ui.log 2>&1; then ok=1; break; fi; \
        echo "[npm retry] $reg 不通"; \
      done; \
    else ok=1; fi; \
    if [ "$ok" = 1 ]; then \
      if bash /opt/pi-ctf/work/ui/build.sh >/tmp/ui-build.log 2>&1; then \
        echo "[ok] 镜像内重建前端产物"; \
      else \
        echo "[warn] 镜像内 build.sh 失败（保留 COPY 进来的 dist）"; tail -20 /tmp/ui-build.log; \
      fi; \
    else \
      echo "[warn] npm 装不上依赖（保留 COPY 进来的 dist）"; tail -20 /tmp/npm-ui.log; \
    fi; \
    js=$(grep -o 'assets/[A-Za-z0-9._-]*\.js' dist/index.html 2>/dev/null | head -1); \
    if [ -z "$js" ] || [ ! -s "dist/$js" ]; then \
      echo "[FATAL] dist/index.html 引不到可用的 JS（$js）→ 容器起来必白屏"; exit 1; \
    fi; \
    echo "[ok] 前端产物自洽：$js（$(stat -c %s "dist/$js") 字节）"

# ⑦·5 RsaCtfTool（rsactf 依赖它；上游 requirements 没列，装不上只告警不阻断交付）
RUN set -eu; \
    PIP=/opt/pi-ctf/tools/venv/bin/pip; \
    for m in https://pypi.tuna.tsinghua.edu.cn/simple https://mirrors.aliyun.com/pypi/simple https://pypi.org/simple; do \
      if "$PIP" install -q --no-cache-dir --timeout 30 --retries 2 -i "$m" -e /opt/pi-ctf/tools/gobin/RsaCtfTool; then \
        echo "[ok] RsaCtfTool via $m"; exit 0; fi; \
      echo "[pip retry] RsaCtfTool via $m"; \
    done; \
    echo "[warn] RsaCtfTool 未装上（rsactf 会不可用）"

# ⑧ 非 root 用户（entrypoint 与 compose 卷都指向 /home/ctf）
#   ★ 2026-10-30 实测事故：并行两次 build 共用 buildx cache 时，这一层被复用成了
#     坏层（/etc/passwd 与 /etc/group 变成 0 字节）→ 容器启动报
#     "unable to find user ctf: no matching entries in passwd file"，
#     而 build 本身"成功"。所以这里**加断言**：passwd 空了就直接让 build 失败，
#     绝不允许产出一个起不来的镜像。
# ⑧·1 sudo 免密（只给 ctf 用户，且只放开建隧道/改路由真正需要的那几条）
#   ★ 2026-11-04：容器里原来**没有 sudo** → tools/tsecbench.py 的 _sudo() 三级降级
#     走到第三级直接以 ctf 身份跑 openvpn，必然 EPERM。装了 sudo 但没配 NOPASSWD，
#     非交互场景还是 `sudo: a password is required`（实测）。这里给最小授权：
#     不是 ALL=(ALL) NOPASSWD:ALL（那等于给容器内 root，没必要），只放 openvpn/ip/pkill/pgrep。
RUN set -eu; \
    id ctf >/dev/null 2>&1 || useradd -m -s /bin/bash ctf; \
    printf '%s\n' \
      'Defaults:ctf !requiretty' \
      'ctf ALL=(root) NOPASSWD: /usr/sbin/openvpn, /sbin/ip, /usr/bin/pkill, /usr/bin/pgrep, /usr/bin/tee, /bin/cp, /bin/chmod, /usr/bin/mkdir, /usr/bin/tail' \
      > /etc/sudoers.d/ctf-vpn; \
    chmod 0440 /etc/sudoers.d/ctf-vpn; \
    visudo -c -f /etc/sudoers.d/ctf-vpn >/dev/null && echo '[ok] sudoers 校验通过'

# ⑧·2 zsteg + pycdc（misc PNG 隐写 / .pyc 反编译）
#   ★ 2026-11-04：selfcheck 的 misc/reverse 两项一直点名缺这两个。
#   zsteg 是 ruby gem（rubygems.org 直连可用，不需换源）；
#   pycdc 要 C++ 编译，源码走 ghproxy（GitHub 直连不通 —— 本机已知事实）。
#   两者都**不阻断构建**：失败只 warn，镜像照出（缺它们只是少把刀）。
RUN set -eu; \
    # qemu-user-static 只装 *-static，补软链成常用名（selfcheck / pwn 脚本按 qemu-aarch64 找）
    ln -sf /usr/bin/qemu-aarch64-static /usr/bin/qemu-aarch64; \
    gem install zsteg --no-document >/tmp/gem-zsteg.log 2>&1 \
      && echo '[ok] zsteg 就位' \
      || echo '[warn] zsteg 装失败（misc PNG 隐写少把刀）'; \
    cd /tmp && rm -rf pycdc; \
    ok=0; \
    for u in https://ghproxy.net/https://github.com/zrax/pycdc.git \
             https://gh-proxy.com/https://github.com/zrax/pycdc.git; do \
      if git clone --depth 1 "$u" /tmp/pycdc 2>/dev/null; then ok=1; break; fi; \
      echo "[retry] $u 不通，换下一个"; \
    done; \
    if [ "$ok" = 1 ]; then \
      mkdir -p /tmp/pycdc/build && cd /tmp/pycdc/build \
        && cmake .. >/tmp/cmake-pycdc.log 2>&1 \
        && make -j4 >/tmp/make-pycdc.log 2>&1 \
        && install -m755 pycdc pycdas /usr/local/bin/ \
        && echo '[ok] pycdc/pycdas 就位' \
      || echo '[warn] pycdc 编译失败（.pyc 反编译少把刀）'; \
    else echo '[warn] pycdc 源码拉不到（GitHub 全镜像不通）'; fi; \
    rm -rf /tmp/pycdc

RUN test -s /etc/passwd && test -s /etc/group \
 && (id ctf >/dev/null 2>&1 || useradd -m -s /bin/bash ctf) \
 && mkdir -p /opt/pi-ctf/workspace /opt/pi-ctf/board/projects /home/ctf/.pi-ctf \
 && chown -R ctf:ctf /opt/pi-ctf /home/ctf \
 && id ctf && test -s /etc/passwd && echo "[ok] 非 root 用户就位"

# ⑧·5 nuclei 模板（离线可用）
#   ★ 2026-11-01 实测：只跑 `nuclei -update-templates` 会「静默成功但 0 条」——
#     它走 raw.githubusercontent.com，本机被墙；rc=0 让人以为装好了，实际 /home/ctf/nuclei-templates 是空的。
#     所以：按实测速度排序走镜像（gh-proxy.com 2.6MB/s > ghfast.top 1.8MB/s > ghproxy.net 37KB/s），github 直连兜底；
#     全都不通才回落 CLI。
RUN set -eu; \
    tmp=/tmp/nt; rm -rf "$tmp" /tmp/nt.tgz; mkdir -p "$tmp"; \
    ok=0; \
    for u in \
      "https://gh-proxy.com/https://github.com/projectdiscovery/nuclei-templates/archive/refs/heads/main.tar.gz" \
      "https://ghfast.top/https://github.com/projectdiscovery/nuclei-templates/archive/refs/heads/main.tar.gz" \
      "https://ghproxy.net/https://github.com/projectdiscovery/nuclei-templates/archive/refs/heads/main.tar.gz" \
      "https://github.com/projectdiscovery/nuclei-templates/archive/refs/heads/main.tar.gz" ; do \
      echo "[templates] try $u"; \
      if curl -fsSL --retry 5 --retry-delay 5 --retry-all-errors --connect-timeout 15 -m 900 -C - \
              --speed-limit 10240 --speed-time 90 -o /tmp/nt.tgz "$u" \
         && tar -xzf /tmp/nt.tgz -C "$tmp" --strip-components=1; then ok=1; break; fi; \
      rm -rf "$tmp"; mkdir -p "$tmp"; \
    done; \
    if [ "$ok" != 1 ]; then echo "[templates] 两个源都不通 → 回落 nuclei CLI"; HOME=/home/ctf nuclei -update-templates -silent || true; fi; \
    mkdir -p /home/ctf/nuclei-templates; \
    if [ "$ok" = 1 ]; then cp -a "$tmp"/. /home/ctf/nuclei-templates/; fi; \
    rm -rf "$tmp" /tmp/nt.tgz; \
    n=$(find /home/ctf/nuclei-templates -name "*.yaml" | wc -l); echo "[templates] 模板数=$n"; \
    if [ "$n" -lt 1000 ]; then echo "[warn] 模板不足 1000 条：运行期可再跑 nuclei -update-templates"; fi; \
    chown -R ctf:ctf /home/ctf

# ⑧·6 pi-web-access（联网搜索 web_search / 抓网页 fetch_content）
#   ★ 为什么预装而不是让 pi 运行时 npm 装：交付要「开箱可用 + 离线可复现」。
#     pi 的扩展解析路径是 agentDir/npm/node_modules/<name>（dist/bundle 里 getNpmInstallPath），
#     入口脚本会把这里的目录软链过去，pi 检测到已存在就跳过联网安装。
#   版本锁 0.46.0（与 npm 上最新一致，锁版避免漂移）；--legacy-peer-deps 跳过 host 侧 peer。
#   零配置即可用（内置 Exa MCP）；要更强的搜索再加 ~/.pi-ctf/agent/web-search.json 的 key。
RUN set -eu; \
    mkdir -p /opt/pi-ctf/vendor/pi-agent-npm; \
    cd /opt/pi-ctf/vendor/pi-agent-npm; \
    npm install --omit=dev --no-audit --no-fund --legacy-peer-deps \
        --registry=https://registry.npmmirror.com pi-web-access@0.46.0; \
    test -f node_modules/pi-web-access/dist/index.js; \
    node -e "const p=require('./node_modules/pi-web-access/package.json'); if(p.version!=='0.46.0') { console.error('[fail] 版本不符 '+p.version); process.exit(1); } console.log('[ok] pi-web-access '+p.version)"; \
    du -sh /opt/pi-ctf/vendor/pi-agent-npm

# ⑧·7 pi-web-access 的 peer 依赖（@earendil-works/pi-tui 等）从 pi 宿主就地软链
#   ★ 2026-11-01 实测：pi-web-access 的 dist 里有静态 import {Box,Text} from "@earendil-works/pi-tui"，
#     而 --legacy-peer-deps 不装 peer → 容器启动报
#     'Failed to load extension ...: Cannot find module p-limit / @earendil-works/pi-tui' → pi 退出 code=1，
#     UI 重启 3 次后彻底起不来（工作台 200 但 agent 全废）。
#   修法：把 pi 宿主自带的同名包软链进 vendor 的 node_modules，版本天然与 pi 一致，且不用联网再拉。
#   ★ 但 @earendil-works/pi-coding-agent **不能**软链：扩展里 import 它时，运行时由 pi 自己的
#     bundle 注入（软链到包入口会拉到 dist/index.js，它又 import pi-codemode/declarations 的
#     MCP_TYPESCRIPT_PREAMBLE，宿主 1.4.0 不导出 → 'does not provide an export named'）。
RUN set -eu; \
    HOSTMOD=/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/node_modules; \
    VEND=/opt/pi-ctf/vendor/pi-agent-npm/node_modules; \
    mkdir -p "$VEND/@earendil-works"; \
    for pk in pi-ai pi-tui; do \
      src=""; \
      for cand in "$HOSTMOD/@earendil-works/$pk" "/usr/local/lib/node_modules/@earendil-works/$pk"; do \
        [ -d "$cand" ] && { src="$cand"; break; }; \
      done; \
      if [ -n "$src" ]; then \
        ln -sfn "$src" "$VEND/@earendil-works/$pk"; \
        echo "[peer] $pk <- $src"; \
      else \
        echo "[peer][warn] 宿主没有 @earendil-works/$pk"; \
      fi; \
    done; \
    if [ -d "$HOSTMOD/typebox" ]; then ln -sfn "$HOSTMOD/typebox" "$VEND/typebox"; echo "[peer] typebox <- host"; fi; \
    cd /opt/pi-ctf/vendor/pi-agent-npm; \
    node -e "import('./node_modules/pi-web-access/dist/index.js').then(()=>console.log('[ok] pi-web-access 扩展可加载')).catch(e=>{console.warn('[warn] 构建期预检未过（运行时由 pi 自身注入 pi-coding-agent，可能仍可加载）: '+e.message)})"

ENV PATH=/opt/pi-ctf/tools/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    UI_HOST=0.0.0.0 \
    UI_PORT=30142 \
    PI_LAB_ROOT=/opt/pi-ctf \
    PI_LAB_KB_DIR=/opt/pi-ctf/kb \
    PI_CODING_AGENT_DIR=/home/ctf/.pi-ctf/agent \
    PI_SESSION_DIR=/home/ctf/.pi-ctf/sessions \
    PI_CODING_AGENT_SESSION_DIR=/home/ctf/.pi-ctf/sessions \
    HOME=/home/ctf

COPY docker/entrypoint.sh docker/provision-agent.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/provision-agent.sh

# ⑨ 入口以 root 起（只为了修 bind mount 属主），entrypoint 内 setpriv 降权到 ctf。
#    ⚠ 不能在这里 USER ctf：compose 会把 ./workspace 与 ./board/projects 重建成
#      root:root 0755，ctf 写不进去（2026-11-01 实测 Permission denied）。
WORKDIR /opt/pi-ctf
EXPOSE 30142

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS -o /dev/null http://127.0.0.1:${UI_PORT}/ || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
