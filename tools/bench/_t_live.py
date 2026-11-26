import json, subprocess, sys, time
t0 = time.time()
p = subprocess.run([sys.executable, "/opt/pi-ctf/tools/bench/live.py"], capture_output=True, text=True)
dt = time.time() - t0
print("elapsed %.2fs rc=%s" % (dt, p.returncode))
if p.returncode != 0:
    print(p.stderr[-1500:]); raise SystemExit(1)
d = json.loads(p.stdout)
print("net", d["net"], d.get("platform_error", ""))
print("score", d.get("score"), "/", d.get("total"), "done", d.get("done"), "/", d.get("count"))
print("bydiff", json.dumps(d.get("by_difficulty"), ensure_ascii=False))
print("procs", json.dumps(d["procs"], ensure_ascii=False))
print("work", json.dumps(d["work"], ensure_ascii=False))
print("containers", d.get("containers"))
rem = d.get("remaining") or []
print("remaining", len(rem), "first5", json.dumps(rem[:5], ensure_ascii=False))
print("flags", len(d.get("recent_flags") or []))
