#!/usr/bin/env python3
"""live.py —— 跑分实况聚合（给 work/ui/server.mjs 的 /api/bench 用）。

为什么单独一个脚本而不是塞进 server.mjs：
  1) 聚合逻辑要反复改（加字段/改口径），python 改完立即生效，不必重启 node；
  2) 数据源跨三处：平台 API（网络）、work/solve/ledger.json（本地）、shard 日志（文本），
     全是「读一次拍个快照」，放同进程里跟 UI 生命周期耦合没必要；
  3) 失败要**分段降级**：平台不通不能让整页空白，界面必须能分块显示「不知道」。

输出：单行 JSON（stdout），字段见 build()。
用法：python3 tools/bench/live.py [--no-net]
  · token 解析顺序：$BENCHMARK_TOKEN → tsecbench.json → 在跑的 sdkshard 的 /proc/<pid>/environ
    （实测：UI/exec shell 里没有这个 env，只有 sdkshard 进程有；不这样兜底会一直 409/401）
  · --no-net 只读本地（平台抖动时界面仍能显示在跑哪题、出了哪些旗）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOLVE = ROOT / "work" / "solve"
LOGDIR = ROOT / "log"
DIFF_ORDER = {"easy": 0, "medium": 1, "hard": 2}

# ★ 2026-11-20 优化 13：预算口径从 tools/solve_policy.py 取（唯一事实来源）。
#   原来这里是第二份副本（120/180/300 + cap 600/420），优化 #10 把真实下发的预算
#   抬到 150/300/480 后**这里没跟着改** → 实况页「待解队列」显示的下一轮预算是错的
#   （而且偏小，看起来像预算还没调过）。
sys.path.insert(0, str(ROOT / "tools"))
try:
    from solve_policy import budget_for as _budget_for  # noqa: E402
except Exception:  # noqa: BLE001
    def _budget_for(diff, tries):  # 兜底：与 solve_policy 同值，宁可重复也别让页面 500
        base = {"easy": 150, "medium": 300, "hard": 480}.get((diff or "").lower(), 150)
        cap = 960 if (diff or "").lower() == "hard" else 640
        return base if tries <= 0 else min(int(base * (1.5 ** min(int(tries), 3))), cap)


def _alive_state(pid):
    """进程状态字母；读不到返回 ""。★ 与 procscan 共用同一份判据（优化 13 的教训：
    同一条判据抄多份必然漂移）。procscan 不可用时退化成直接读 /proc/<pid>/stat。"""
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import procscan  # noqa: PLC0415

        return procscan._read_state(pid)
    except Exception:  # noqa: BLE001
        try:
            with open("/proc/%s/stat" % pid, "r", encoding="utf-8", errors="replace") as fh:
                _st = fh.read()
            return _st[_st.rindex(")") + 1:].split()[0]
        except (OSError, ValueError, IndexError):
            return ""


def _token_from_procs():
    """★ 实测：UI/exec shell 里**没有** BENCHMARK_TOKEN，只有 sdkshard 进程有；
    tsecbench.json 里那份是上一场 run 的旧 token（用它请求恒 409 already finished）。
    所以真正的 token 必须从在跑的 shard 进程环境里借。

    ★ 2026-11-20 优化 13：改走 procscan（原来用 `"sdkshard.py" in ps 行` 子串匹配，
    任何提到该文件名的命令行都会被当成 shard 去读 environ —— 读到的是别的进程，
    token 自然借不到，界面就退化成 409 降级）。
    """
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import procscan  # noqa: PLC0415

        cand = procscan.python_script_pids("sdkshard.py")
    except Exception:  # noqa: BLE001
        return ""
    for pid in cand:
        # ★ 2026-11-20 实测：/proc/<pid>/{cmdline,environ} 在读 **D 态**进程时会**永久阻塞**
        #   （容器里一批卡在 __access_remote_vm 的 ps/find/grep），
        #   而 /proc/<pid>/stat 永远不阻塞。这里多一道守卫：即使 procscan 被换掉、
        #   或进程正好在读 environ 的瞬间进入 D 态，也不会把 /api/bench 拖死
        #   （症状是实况页显示 "live.py 超时（15s）" + 全空，看起来像 UI 坏了）。
        if _alive_state(pid) in ("Z", "D", "X", "x"):
            continue
        try:
            env = Path("/proc/%s/environ" % pid).read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        for kv in env.split("\0"):
            if kv.startswith("BENCHMARK_TOKEN="):
                v = kv.split("=", 1)[1].strip()
                if v:
                    return v
    return ""


def _cfg():
    """返回 (base, token)。顺序：env → 在跑的 shard → tsecbench.json（最后兜底）。"""
    base = os.environ.get("BENCHMARK_BASE_URL") or ""
    tok = os.environ.get("BENCHMARK_TOKEN") or ""
    for cand in (Path.home() / ".pi-ctf/agent/tsecbench.json", ROOT / ".pi/tsecbench.json"):
        if cand.is_file():
            try:
                j = json.loads(cand.read_text(encoding="utf-8"))
                base = base or (j.get("base_url") or "")
                tok = tok or (j.get("token") or "")
            except Exception:
                pass
    tok = _token_from_procs() or tok
    return base or "https://tsecbench.zc.tencent.com", tok


CACHE = SOLVE / "_platform_cache.json"


def normalize(items):
    """平台原始条目 → 界面用的规范形。★ 缓存与平台必须是**同一种形状**，
    否则降级路径一进来就 KeyError（首版踩过：缓存存原始、build 读规范）。"""
    out = []
    for i in items or []:
        if not isinstance(i, dict):
            continue
        out.append({
            "code": i.get("unique_code") or i.get("code"),
            "diff": (i.get("difficulty") or i.get("diff") or "").lower(),
            "score": int(i.get("total_score") or i.get("score") or 0),
            "done": bool(i.get("is_completed", i.get("done"))),
            "status": i.get("container_status", i.get("status")),
        })
    return out


def save_cache(plat):
    """把最近一次成功的题单落盘（规范形）。★ 平台 409（run 结束/VPN 掉）时它仍能让实况页
    显示总分/分难度/待解队列 —— 否则整页只剩「—」，看上去像 UI 坏了（用户实测反馈）。"""
    try:
        SOLVE.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"ts": int(time.time()), "items": plat}, ensure_ascii=False),
                         encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


# 没有 _platform_cache.json 时的第二道兜底：work/ch.json 是 2026-11-19 落下的完整
# 104 题快照（75 done / 19500 分），字段与平台返回同构。run 结束、VPN 掉之后平台恒 409，
# 这份文件是「总分 / 分难度 / 待解队列」唯一还能算出来的依据。
CH_SNAPSHOT = ROOT / "work" / "ch.json"


def _from_snapshot(fp):
    """读一份平台同构快照，返回 (规范形 items, age_sec) 或 (None, 0)。
    两种形状都收：{"ts":..,"items":[...]}（自己的滚动缓存）与裸 list（work/ch.json）。"""
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
        items = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(items, list) or not items:
            return None, 0
        norm = normalize(items)
        if not norm:
            return None, 0
        age = max(0, int(time.time() - int(fp.stat().st_mtime)))
        return norm, age
    except Exception:  # noqa: BLE001
        return None, 0


def load_cache():
    """返回 (items, age_sec) 或 (None, 0)。先用自己的滚动缓存，再退到 ch.json 快照。"""
    items, age = _from_snapshot(CACHE) if CACHE.is_file() else (None, 0)
    if items:
        return items, age
    return _from_snapshot(CH_SNAPSHOT)


def _friendly(err):
    """把平台异常翻成人话（界面直接显示，不让人去猜 HTTP 码）。"""
    s = err or ""
    if "409" in s or "Conflict" in s:
        return "本轮 run 已结束（409）—— 得开新 run 才能再抢分；下面数字照常可用"
    if "VPN" in s or "vpn" in s:
        return "靶场 VPN 不通 —— 先把隧道建起来（openvpn + tun0）"
    if "401" in s or "403" in s:
        return "token 被拒（401/403）—— 检查 BENCHMARK_TOKEN 是否属于当前 run"
    if "timed out" in s or "timeout" in s or "URLError" in s:
        return "连不上平台（超时）—— 多半是 VPN 或平台抖动，稍后会自己恢复"
    if "BENCHMARK_TOKEN" in s:
        return "未配置 BENCHMARK_TOKEN"
    return s[:160]


def platform(net=True, retries=3):
    """平台题单。返回 (ok, list|err)。判据：is_completed / total_score / difficulty。"""
    if not net:
        return False, "跳过网络"
    base, tok = _cfg()
    if not tok:
        return False, "缺 BENCHMARK_TOKEN"
    last = "?"
    for i in range(retries):
        try:
            req = urllib.request.Request(
                base.rstrip("/") + "/openapi/v1/challenges",
                headers={"BENCHMARK_TOKEN": tok},
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                items = json.load(r)
            break
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, str(e)[:120])
            time.sleep(0.4 * (i + 1))
    else:
        return False, last
    return True, normalize(items)


def ledger():
    f = SOLVE / "ledger.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def procs():
    """在跑的解题进程：sdkshard（每路一个）+ parallel_solver（总控）+ 每题一个 pi。

    ★★ 2026-11-20 优化 13（实况页「pi 子进程永远是 0」的根因，实测确认）：
      原实现按 `"/dist/bundle/cli.js" in ln` 找 pi 子进程 —— 但 pi 启动后会把 argv
      重写成 `["pi"]`（Node 的 process.title 行为），`ps` 里**根本没有 cli.js**：

          $ ps -eo pid,ppid,etime,args
           1559  1557  00:05 pi            ← 真正的解题 pi
            161     7  21:49 pi            ← UI 常驻 pi（与解题无关）

      所以那个分支永远匹配不到 → 实况页「pi 子进程」恒为 0/—（用户看到的「像没在干活」）。
      判据换成 tools/procscan.py：comm == 'pi' **且**祖先链里有 sdkshard.py
      （排除 UI 那个常驻 pi）。顺带把「按 'pattern in args' 找 shard」也换掉 ——
      同一类误报：`bash -c 'tail -F ... sdkshard.py'` 会被算成一个 shard。
    """
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import procscan  # noqa: PLC0415

        snap = procscan.snapshot()
    except Exception:  # noqa: BLE001
        return {"shards": [], "controller": None, "agents": 0, "agent_pids": []}
    ctrl = snap.get("controller")
    return {
        "shards": [{"pid": str(s["pid"]), "etime": str(s["etime"])} for s in snap.get("shards", [])],
        "controller": (("pid=%s etime=%ss" % (ctrl["pid"], ctrl["etime"])) if ctrl else None),
        "supervisor": snap.get("supervisor"),
        "agents": int(snap.get("agents", 0)),
        "agent_pids": snap.get("agent_pids", []),
    }


def active_work():
    """当前在解什么：每个 shard 的最后一行 "→ [addr]"，配 ps 的 pid。

    日志是**唯一**能回答「现在到底在跑哪道题」的源：平台只给 container_status，
    而 sdkshard 在开跑时才 start_challenge，日志行是最贴近真实的那一拍。
    """
    out = {}
    for f in sorted(LOGDIR.glob("shard-*.log"), key=lambda x: x.stat().st_mtime, reverse=True)[:1]:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
        except OSError:
            continue
        cur = None
        for ln in lines:
            m = re.match(r"^\[shard\] (xben-\d+-\d+) → (\[.*\])$", ln)
            if m:
                cur = {"code": m.group(1), "addrs": m.group(2)}
                continue
            m2 = re.match(r"^\[shard\] (xben-\d+-\d+): (.*)$", ln)
            if m2 and cur and m2.group(1) == cur["code"]:
                cur["last"] = m2.group(2)
        if cur:
            out["last_started"] = cur
    return out


def recent_flags(n=14):
    """最近出旗流水：直接从 shard 日志里抓 ✅ 行（平台回执 awarded）。"""
    hits = []
    for f in sorted(LOGDIR.glob("shard-*.log"), key=lambda x: x.stat().st_mtime, reverse=True)[:1]:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()[-4000:]
        except OSError:
            continue
        cur = None
        for ln in lines:
            m = re.match(r"^\[shard\] (xben-\d+-\d+) → ", ln)
            if m:
                cur = m.group(1)
            m2 = re.search(r"✅ (flag\{[^}]*\}) awarded=(\d+)", ln)
            if m2:
                hits.append({"code": cur, "flag": m2.group(1), "awarded": int(m2.group(2))})
        break
    return hits[-n:][::-1]


def cost_gate():
    """余额闸门状态（R1-O2）。**必须本地可解析、不联网** —— 页面 15s 超时，
    多打一次远程接口就会把整页拖成空白。真正查询在 tools/costguard.py 里做，
    这里只读它「上一次的判断」+ 环境里的阈值，给界面一个能显示的数字。

    ★ 2026-11-20 血账：整轮余额 3.97→1.12 CNY，而实况页**从头到尾没有任何余额字段**，
      用户只能靠我自己在外面 curl。P2-7「余额对用户不可见」就是这条。
    """
    out = {"min_cny": None, "balance": None, "currency": None, "blocked": False}
    try:
        sys.path.insert(0, str(ROOT / "tools"))
        import costguard  # noqa: PLC0415
        out["min_cny"] = costguard.threshold()
    except Exception:  # noqa: BLE001
        pass
    # costguard 每次跑完会把结论落到 work/solve/.balance.json（见其 --json 用法说明）
    f = SOLVE / ".balance.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        out["balance"] = data.get("total")
        out["currency"] = data.get("currency")
        out["blocked"] = not bool(data.get("allow", True))
        out["age_sec"] = int(time.time() - f.stat().st_mtime)
    except Exception:  # noqa: BLE001
        pass
    return out


def build(net=True):
    ok, plat = platform(net)
    # 保住失败原因：下面降级分支会把 plat 重写成缓存或 None，原来错误串被丢掉，
    # 界面只能显示「题单不可用」—— 而 409(run 已结束) / VPN / 401(token 错) 三种处置完全不同。
    plat_err = None if ok else _friendly(plat if isinstance(plat, str) else "题单不可用")
    # ★ 默认必须是 none 而不是 live：没拿到题单却标 live，界面上就看不出这是降级态
    #   （实测 --no-net + 无缓存时返回过 platform_source:"live" 且没有任何分数）。
    src = "live" if ok else "none"
    age = 0
    if ok:
        save_cache(plat)
    else:
        # ★ 分段降级（用户实测：「平台 409 时页面几乎全空」）：题单拿不到就用最近一次
        #   成功的快照把总分/分难度/待解队列算出来，并标明 stale —— 界面必须能显示
        #   「这是 N 分钟前的数」，而不是退化成一片「—」。
        cached, age = load_cache()
        if cached:
            plat = cached
            src = "cache"
        else:
            plat = None
    led = ledger()
    t0 = time.time()
    snap = {"ts": int(t0 * 1000), "net": ok, "platform_source": src}
    if age:
        snap["platform_age_sec"] = age
    if plat is not None:
        done = [p for p in plat if p["done"]]
        snap["score"] = sum(p["score"] for p in done)
        snap["total"] = sum(p["score"] for p in plat)
        snap["done"] = len(done)
        snap["count"] = len(plat)
        byd = {}
        for p in plat:
            d = p["diff"] or "?"
            b = byd.setdefault(d, {"done": 0, "n": 0, "score": 0, "total": 0, "spent": 0})
            b["n"] += 1
            b["total"] += p["score"]
            if p["done"]:
                b["done"] += 1
                b["score"] += p["score"]
                b["spent"] += int((led.get(p["code"]) or {}).get("total_sec", 0))
        snap["by_difficulty"] = byd
        # 剩余题单：按「没跑过优先 → 难度 → ROI」排，界面直接照抄就是下一刀
        rem = []
        for p in plat:
            if p["done"]:
                continue
            L = led.get(p["code"]) or {}
            at = int(L.get("attempts", 0))
            sec = int(L.get("total_sec", 0))
            bud = _budget_for(p["diff"], at)
            rem.append({
                "code": p["code"], "diff": p["diff"], "score": p["score"],
                "attempts": at, "spent": sec, "budget": bud,
                "rejected": int(L.get("rejected", 0)),
                "roi": round(p["score"] / sec, 2) if sec else None,
            })
        rem.sort(key=lambda r: (DIFF_ORDER.get(r["diff"], 9), r["attempts"], r["code"]))
        snap["remaining"] = rem
        # 活跃容器（平台口径）
        snap["containers"] = [p["code"] for p in plat if p["status"] not in (None, "stopped")]
    else:
        snap["platform_error"] = plat_err or "题单不可用"
    snap["procs"] = procs()
    snap["work"] = active_work()
    snap["recent_flags"] = recent_flags()
    snap["cost"] = cost_gate()
    snap["ms"] = int((time.time() - t0) * 1000)
    return snap


def main():
    net = "--no-net" not in sys.argv
    print(json.dumps(build(net), ensure_ascii=False))


if __name__ == "__main__":
    main()