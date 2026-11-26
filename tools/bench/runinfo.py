import json, urllib.request, time
from datetime import datetime, timezone, timedelta
BASE = "https://tsecbench.zc.tencent.com"
TOK = "1b0b3f85-c2ca-4905-8d32-3b7fbccc699a"
for path in ("/openapi/v1/run", "/openapi/v1/runs", "/openapi/v1/status"):
    try:
        req = urllib.request.Request(BASE + path, headers={"BENCHMARK_TOKEN": TOK})
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
        print(path, "->", json.dumps(d, ensure_ascii=False)[:600])
    except Exception as e:
        print(path, "ERR", e)
