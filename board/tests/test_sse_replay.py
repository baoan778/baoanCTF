# -*- coding: utf-8 -*-
"""SSE 断线自愈：事件自增 id / Last-Event-ID 覆盖 ?since / 按 id 补偿回放 / 反代缓冲头。

为什么这么测：
  A 纯逻辑 —— 从 server.mjs 切出 pickReplay 交 node 直接跑（不启服务、不打桩）。
    「断线补发」最坏的错误不是崩，而是**静默漏一条**：漏掉的那条可能是最后的空转帧，
    界面就永远停在「正在想」。所以边界（刚好接上 / 差一条 / 缓冲被挤掉 / 首次连接）逐个钉死。
  B 接线 —— 静态断言：id 真的写进帧、last-event-id 真的覆盖 ?since、头真的加上、前端真的存 id。
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRV = os.path.join(ROOT, "work", "ui", "server.mjs")
API = os.path.join(ROOT, "work", "ui", "web", "src", "api.ts")

passed = 0
failed = 0

def ok(cond, label, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("  ok   " + label)
    else:
        failed += 1
        print("  FAIL " + label + (("  <- " + str(extra)) if extra else ""))

src = open(SRV, encoding="utf-8").read()
api = open(API, encoding="utf-8").read()

print("A 纯逻辑：pickReplay（node 直跑源码切片）")
b = src.find("// SSE-REPLAY-BEGIN")
e = src.find("// SSE-REPLAY-END")
ok(b > 0 and e > b, "A0 server.mjs 里有 SSE-REPLAY 切片标记", (b, e))
code = src[b:e] if (b > 0 and e > b) else ""

cases = [
    ("空缓冲 + since", [], 5, 300),
    ("刚好接上（since=5，缓冲 1..10）", list(range(1, 11)), 5, 300),
    ("缓冲被挤掉（缓冲 100..110，since=50）", list(range(100, 111)), 50, 300),
    ("已是最新（since=110）", list(range(100, 111)), 110, 300),
    ("超出上限只回最近 limit 条", list(range(1, 11)), 0, 3),
    ("边界：since=oldest-1 不算丢", list(range(100, 111)), 99, 300),
    ("since 早于缓冲起点一条以上：全发但报丢", list(range(100, 111)), 90, 300),
]
case_json = json.dumps([[c[1], c[2], c[3]] for c in cases])

driver = "\n".join([
    "const raw = " + json.dumps(case_json) + ";",
    "const cases = JSON.parse(raw);",
    "const out = [];",
    "for (const [ids, since, limit] of cases) {",
    "  const recent = ids.map((id) => ({ id, rec: { t: id } }));",
    "  const p = pickReplay(recent, since, limit);",
    "  out.push({ ids: p.items.map((x) => x.id), lost: p.lost, dropped: p.dropped, truncated: p.truncated });",
    "}",
    "const many = [];",
    "for (let i = 1; i <= 200; i++) many.push({ id: i, rec: {} });",
    "const dflt = pickReplay(many, NaN, 300);",
    "console.log(JSON.stringify({ out, dflt: dflt.items.map((x) => x.id) }));",
]).replace(chr(92) + "n", chr(10))

node = None
for cand in ("node", "node.exe"):
    try:
        subprocess.run([cand, "-v"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        node = cand
        break
    except Exception:
        continue

if node is None:
    print("SKIP node 不可用（发布包形态 / 无 node 的机器）：只跑接线断言")

res = None
if node:
    tmp = tempfile.mkdtemp(prefix="sse17-")
    js = os.path.join(tmp, "p.mjs")
    body = code + chr(10) + driver + chr(10)
    if not code.strip().startswith("//"):
        body = "// SSE-REPLAY-BEGIN" + chr(10) + body
    with open(js, "w", encoding="utf-8", newline=chr(10)) as f:
        f.write(body)
    r = subprocess.run([node, js], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ok(r.returncode == 0, "A1 node 跑通切片（rc=0）", r.stderr.decode("utf-8", "replace")[-400:])
    if r.returncode == 0:
        res = json.loads(r.stdout.decode("utf-8"))

def check(idx, label, want_ids, want_lost, want_dropped, want_trunc):
    if res is None:
        return
    got = res["out"][idx]
    ok(got["ids"] == want_ids, "A2.%d %s：补发 id" % (idx + 1, label), got)
    ok(got["lost"] == want_lost, "A2.%d %s：lost" % (idx + 1, label), got)
    ok(got["dropped"] == want_dropped, "A2.%d %s：dropped" % (idx + 1, label), got)
    ok(got["truncated"] == want_trunc, "A2.%d %s：truncated" % (idx + 1, label), got)

check(0, cases[0][0], [], 0, 0, False)
check(1, cases[1][0], [6, 7, 8, 9, 10], 0, 0, False)
check(2, cases[2][0], list(range(100, 111)), 49, 0, True)
check(3, cases[3][0], [], 0, 0, False)
check(4, cases[4][0], [8, 9, 10], 0, 7, True)
check(5, cases[5][0], list(range(100, 111)), 0, 0, False)
check(6, cases[6][0], list(range(100, 111)), 9, 0, True)
if res is not None:
    ok(res["dflt"] == list(range(81, 201)), "A3 无 since 只回最近 120 条", len(res["dflt"]))

print("B 接线：服务端")
ok("const id = ++eventSeq;" in src, "B1 每条帧拿单调自增 id")
ok("recent.push({ id, rec });" in src, "B2 环形缓冲存 {id, rec}（否则无法按 id 过滤）")
ok("const line = `id: ${id}" in src, "B3 帧头真的写 id:（浏览器靠它回传 Last-Event-ID）")
ok("for (const e of plan.items) res.write(`id: ${e.id}" in src, "B4 补偿回放的每一帧也带 id")
ok('"last-event-id"' in src, "B5 读 Last-Event-ID 头")
ok('Number.isFinite(hdrSince) ? hdrSince : (Number.isFinite(qSince) ? qSince : NaN)' in src,
   "B6 头优先于 ?since（自动重连的真相压过查询串）")
ok(src.find('req.headers["last-event-id"]') < src.find('url.searchParams.get("since")'),
   "B7 代码顺序上先取头再取 ?since")
ok('"x-accel-buffering": "no"' in src, "B8 关掉反代缓冲（否则连上却收不到字节）")
ok("res.flushHeaders()" in src, "B9 立刻下发响应头")
ok('res.write("retry: 3000' in src, "B10 给自动重连一个明确节奏")
ok('"ui:gap"' in src and "plan.lost" in src and "plan.dropped" in src,
   "B11 缓冲被挤掉时自报 ui:gap（不许假装连续）")
ok(src.find("retry: 3000") < src.find("ui:hello"), "B12 先 retry 再 hello")
hello_line = [ln for ln in src.split(chr(10)) if "ui:hello" in ln and "res.write" in ln]
ok(len(hello_line) == 1 and "id: ${" not in hello_line[0], "B13 ui:hello 不带 id（不推进 Last-Event-ID）", hello_line)
ok("pickReplay(recent, since, RECENT_MAX)" in src, "B14 回放上限就是缓冲上限")

print("C 接线：前端")
ok('const SSE_SINCE_KEY = "pictf.sseLastId";' in api, "C1 最后 id 落 sessionStorage（页面刷新是新实例，头会丢）")
ok("sessionStorage.getItem(SSE_SINCE_KEY)" in api, "C2 新建连接时读回 since")
ok('"?since=" + encodeURIComponent(since)' in api, "C3 用 ?since= 续接")
ok("m.lastEventId" in api and "sessionStorage.setItem(SSE_SINCE_KEY, m.lastEventId)" in api,
   "C4 每帧回写 lastEventId")
ok("catch { /* 隐私模式禁 storage" in api, "C5 storage 被禁时降级而不是崩")
ok("EventSource(\"/api/events\"" in api, "C6 仍然只连这一个事件通道")

print()
print("%d passed / %d failed" % (passed, failed))
if node is None and failed == 0:
    sys.exit(0)
sys.exit(1 if failed else 0)