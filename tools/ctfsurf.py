#!/usr/bin/env python3
"""ctfsurf.py —— 前端残迹 → 攻击面推演器（离线，先推演再爆破）。

为什么要有它（2026-10-05 msgboard 复盘出来的血教训）：
  在一道 PHP 留言板题上，我们跑了 2.7 万条目录字典 + 全程弱口令爆破 + sqlmap，
  全负；而**真正该看的东西一直摆在页面上**：
    · style.css 里定义了 `.preview` `.actions` `.admin-grid` —— 而渲染出来的 HTML
      **一个都没用**（悬空类名 = 未渲染功能的指纹：还有「预览态 / 操作区 / 后台网格」没露出来）；
    · 表单里已经有 `action=login` —— 说明这套应用用**一个 action 参数**分流业务，
      那么其它 action 取值（preview/draft/publish/approve…）是**个位数**的枚举，不是 2 万条字典；
    · 题面文案「旧版留言板 / 终端桥接维护组 / 值班留言」都在提示业务动词（留言=发布、值班=签到、归档）。
  → 通用字典打小应用是**最低产出**的路；应用自己的**词汇表**才是攻击面。

它做什么（纯解析已有产物，不发请求）：
  1. 表单清单（应用真实的端点/参数词汇表，含 hidden 值 = 已在用的 action 取值）
  2. 链接 / 资源路径
  3. JS 里的路径与方法字符串（fetch/axios/xhr/open/href）
  4. ★ 悬空 CSS 类（定义但未渲染）→ 语义猜测（预览/草稿/上传/后台/提示分支…）
  5. 题面中文业务动词 → 候选 action 取值 / 参数名（含拼音）
  6. 优先级排序的「下一步攻击面」清单 + 反爆破闸门（小应用先穷举动词表，别开字典）

设计纪律（与 probe/authzdiff 一致）：
  · 默认**只读本地文件**；`--url` 才发一次 GET（限速、超时、不跟随到外站由 urllib 默认处理）
  · 任何文件读取走 read_capped()（只认普通文件 + 限长 8MB），拒 FIFO / /dev/zero / 符号链接
  · 不执行页面里的 JS，只做字符串解析
  · 输出人读 + `--json`；`--selftest` 离线自检

用法：
    py tools/ctfsurf.py work/msgboard/index.html work/msgboard/style.css
    py tools/ctfsurf.py --dir work/msgboard --json > work/msgboard/ctfsurf.json
    py tools/ctfsurf.py --url http://target/ --save work/t/index.html
    py tools/ctfsurf.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAX_INPUT = 8 * 1024 * 1024          # 单个文件上限
MAX_FILES = 200                      # --dir 递归文件数上限（防误指到仓库根）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---------------------------------------------------------------- 安全读文件
def read_capped(path, limit=MAX_INPUT):
    """只认普通文件 + 限长（仓库硬规矩：拒 /dev/zero / FIFO / 符号链接 / 超大）"""
    if os.path.islink(path):
        raise ValueError(f"拒绝符号链接: {path}")
    if not os.path.isfile(path):
        raise ValueError(f"不是普通文件: {path}")
    size = os.path.getsize(path)
    if size > limit:
        raise ValueError(f"文件过大 {size}B > {limit}B: {path}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(limit)


# ================================================================ 解析层
# 都是纯函数：输入字符串 → 结构化结果，方便离线测试

_TAG_ATTRS = re.compile(
    r"<(form|input|button|a|link|script|img|iframe|option|select|textarea)\b([^>]*)>",
    re.I | re.S)
_ATTR = re.compile(r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""", re.S)
_CLASS_USE = re.compile(r"""\bclass\s*=\s*("([^"]*)"|'([^']*)')""", re.I)
_ID_USE = re.compile(r"""\bid\s*=\s*("([^"]*)"|'([^']*)')""", re.I)


def _attr_map(raw):
    out = {}
    for m in _ATTR.finditer(raw):
        val = m.group(3)
        if val is None:
            val = m.group(4)
        if val is None:
            val = m.group(5) or ""
        out[m.group(1).lower()] = val
    return out


def parse_tags(html):
    """→ [(tagname, attrs_dict), ...]"""
    return [(m.group(1).lower(), _attr_map(m.group(2))) for m in _TAG_ATTRS.finditer(html)]


def extract_forms(html):
    """表单清单：应用真实的「端点 + 参数词汇表」。

    返回 [{"method","action","fields":[{name,type,value}], "buttons":[...]}]。
    action 缺省 = 提交到当前 URL（题面里的「本页处理」很常见，别当成没有端点）。
    """
    forms, cur = [], None
    for m in re.finditer(r"<(/?)(form|input|textarea|select|button)\b([^>]*)>", html, re.I | re.S):
        closing, tag, raw = m.group(1), m.group(2).lower(), m.group(3)
        a = _attr_map(raw)
        if tag == "form":
            if closing:
                if cur is not None:
                    forms.append(cur)
                cur = None
            else:
                if cur is not None:
                    forms.append(cur)
                cur = {"method": (a.get("method") or "GET").upper(),
                       "action": a.get("action") or "", "fields": [], "buttons": []}
        elif cur is not None and not closing:
            if tag in ("input", "textarea", "select"):
                cur["fields"].append({"name": a.get("name", ""),
                                      "type": (a.get("type") or tag).lower(),
                                      "value": a.get("value", "")})
            elif tag == "button":
                cur["buttons"].append({"type": (a.get("type") or "submit").lower(),
                                       "name": a.get("name", ""), "value": a.get("value", ""),
                                       "text": a.get("value", "")})
    if cur is not None:
        forms.append(cur)
    return forms


def extract_links(html):
    """链接 / 资源路径（去重保序）。"""
    out, seen = [], set()
    for tag, a in parse_tags(html):
        for k in ("href", "src", "action", "data-url", "data-action", "formaction"):
            v = (a.get(k) or "").strip()
            if not v or v.startswith(("#", "javascript:", "mailto:", "data:")):
                continue
            if v not in seen:
                seen.add(v)
                out.append(v)
    return out


_JS_PATH = re.compile(r"""["'`]((?:/|\.{1,2}/)[A-Za-z0-9_\-./]*\.(?:php|asp|aspx|jsp|do|action|cgi|py|rb|go|json|html?)(?:\?[^"'`]*)?)["'`]""")
_API_STR = re.compile(r"""["'`]([A-Za-z0-9_\-./]{2,60}\.(?:php|aspx?|jsp|do|action|cgi))(?:\?[^"'`]*)?["'`]""")


def extract_js_paths(text):
    """从 JS/HTML 里抠候选端点路径（引号包裹的路径式字符串），按路径（去 query）去重。"""
    out, seen = [], set()
    for rx in (_JS_PATH, _API_STR):
        for m in rx.finditer(text):
            p = m.group(1)
            key = p.split("?", 1)[0]
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out


_CSS_RULE = re.compile(r"([^{}]+)\{")
_CSS_CLASS = re.compile(r"\.(-?[A-Za-z_][-\w]*)")


def extract_css_classes(css):
    """CSS 选择器里定义过的类名 → 出现次数。"""
    counts = {}
    for sel in _CSS_RULE.findall(css):
        for c in _CSS_CLASS.findall(sel):
            counts[c] = counts.get(c, 0) + 1
    return counts


def extract_used_classes(html):
    """HTML 里实际用到的 class / id token。"""
    used = set()
    for m in _CLASS_USE.finditer(html):
        tok = m.group(2) if m.group(2) is not None else m.group(3) or ""
        used.update(t for t in tok.split() if t)
    for m in _ID_USE.finditer(html):
        tok = m.group(2) if m.group(2) is not None else m.group(3) or ""
        used.add(tok.strip())
    return used


# ★ 语义表：悬空类名 → 它在业务上意味着什么攻击面
CLASS_SEMANTICS = [
    (r"^(preview|draft|pending|unpublished|temp|stash)", "预览/草稿态",
     "未发布对象常只给管理员看 → 越权读(IDOR) / 存储型 XSS 打 admin 审核"),
    (r"^admin", "后台/管理面", "管理员专属视图 → 找它的端点与鉴权判定点"),
    (r"(upload|drop|attach|file|avatar|media)", "上传面", "类型/后缀/内容绕过 → 上传即 RCE 或 XSS"),
    (r"(notice|alert|success|error|warning|flash|toast|msg-box)", "提示分支",
     "同一次请求的不同分支 → 错误 oracle（拿报错/路径/版本）"),
    (r"(modal|dialog|popup|drawer|overlay|confirm)", "交互/确认面", "隐藏 action（删除/审批/改状态）常只在这里"),
    (r"(action|toolbar|controls|menu|ops|tools|btn-group|button-group)", "操作区/按钮组",
     "渲染出来的页面没这个区域 = 还有一组操作没露出来（改状态/删除/发布）"),
    (r"(pager|pagination|page-|load-more|cursor)", "分页参数", "page/offset/cursor → 注入 / 越权枚举"),
    (r"(search|filter|query|keyword|autocomplete)", "查询参数", "查询词 → SQLi / 模板注入 / 反射 XSS"),
    (r"(badge|label|status|state|tag|chip)", "状态字段", "改状态型逻辑越权（未审 → 已通过）"),
    (r"(export|download|print|report|backup)", "导出面", "导出常绕过鉴权 / 路径穿越 / 命令注入"),
    (r"(^|-)(hero-small|hero-card|stack|shell|board|grid|card|panel|row|col|wrap|container|footer|nav)(-|$)",
     "纯布局", "★ 大概率无攻击面（别浪费时间）"),
]

# 排序权重：真攻击面 → 未分类 → 纯布局（噪声压到最后）
_SURFACE_ORDER = {s[1]: i for i, s in enumerate(CLASS_SEMANTICS)}
_SURFACE_ORDER["未分类"] = len(CLASS_SEMANTICS)
_SURFACE_ORDER["纯布局"] = len(CLASS_SEMANTICS) + 1

# ★ 中文业务动词 → 候选 action 取值 / 参数名（题面文案直接是线索）
VERB_MAP = [
    (r"发布|提交|留言|写下|发表", ["post", "create", "msg", "add", "publish", "liuyan"],
     "留言=发布：找发布端点（msgboard 就是死在这里 —— 从没找到发布入口）"),
    (r"预览|草稿|存为草稿", ["preview", "draft", "save"], "草稿/预览：未发布态 → 越权/审核绕过"),
    (r"审核|审批|通过|拒绝|驳回", ["approve", "review", "audit", "pass", "reject"], "审批流：逻辑越权 / admin 审核 → bot"),
    (r"归档|备份|导出|下载|报表", ["archive", "backup", "export", "download", "report"], "导出面：常绕过鉴权 / 穿越"),
    (r"值班|签到|交接|排班", ["checkin", "shift", "duty", "handover"], "值班表：业务对象 IDOR"),
    (r"维护|设置|配置|管理|后台", ["admin", "config", "setting", "manage", "panel"], "后台面：找鉴权判定"),
    (r"登录|注销|退出|注册", ["login", "logout", "register", "signin"], "认证面（多半不是正解，但要知道它的存在）"),
    (r"通知|公告|提醒", ["notice", "announce", "notify"], "公告：常是存储型 XSS 的展示位"),
    (r"升级|会员|充值|积分|VIP", ["upgrade", "vip", "charge", "score", "recharge"], "业务逻辑（价格/数量/负值）"),
    (r"收件|发信|消息|私信|聊天", ["mail", "message", "chat", "pm", "inbox"], "消息面：IDOR / 越权读别人的信"),
]


def dangling_classes(css_classes, used):
    """悬空类名（CSS 定义但 HTML 未渲染）+ 语义猜测。"""
    out = []
    for cls, n in css_classes.items():
        if cls in used:
            continue
        if len(cls) <= 2:          # .a .b 这种极短类名多半是库/工具生成，噪声
            continue
        hit = None
        for rx, name, why in CLASS_SEMANTICS:
            if re.search(rx, cls):
                hit = {"class": cls, "defined": n, "surface": name, "why": why}
                break
        # 没有任何规则命中也要留下：悬空本身就是证据，名字可能就是线索
        out.append(hit or {"class": cls, "defined": n, "surface": "未分类",
                           "why": "渲染层没有它 —— 人工看名字判断是什么功能"})
    out.sort(key=lambda d: (_SURFACE_ORDER.get(d["surface"], 99), d["class"]))
    return out


def business_verbs(text):
    """从页面中文文案里抽业务动词 → 候选 action 取值。"""
    out = []
    for rx, actions, why in VERB_MAP:
        hits = re.findall(rx, text)
        if hits:
            out.append({"verbs": sorted(set(hits)), "actions": actions, "why": why})
    return out


def rank_surfaces(forms, js_paths, dangling, verbs, links):
    """按「先穷举应用词汇表，再考虑字典」排序下一步攻击面。"""
    surfaces = []
    actions_seen = set()
    param_names = set()
    for f in forms:
        for fld in f["fields"]:
            if fld["name"]:
                param_names.add(fld["name"])
            if fld["name"].lower() in ("action", "do", "op", "mode", "act", "cmd") and fld["value"]:
                actions_seen.add(fld["value"])
    if actions_seen or any(f["fields"] for f in forms):
        vals = ", ".join(sorted(actions_seen)) or "（没写死值，但参数名在）"
        surfaces.append({
            "prio": 0, "surface": f"穷举 action 取值（已在用：{vals}）",
            "why": "表单里的 hidden action 说明业务由该参数分流；候选取值只有个位数，"
                   "配参数名 " + ", ".join(sorted(param_names)[:8]) + " 直接枚举 → 不是 2 万条字典",
            "evidence": f"forms={len(forms)} 参数名={len(param_names)}"})
    hot = [d for d in dangling if d["surface"] not in ("纯布局",)]
    if hot:
        surfaces.append({
            "prio": 0, "surface": "悬空类名指向的未渲染功能：" + " / ".join(
                f"{d['class']}→{d['surface']}" for d in hot[:5]),
            "why": "CSS 定义了但页面没渲染 = 该功能存在却对你不可见；顺着猜它的端点/参数",
            "evidence": f"dangling={len(dangling)} 其中有意义 {len(hot)}"})
    if verbs:
        surfaces.append({
            "prio": 1, "surface": "按题面业务动词补 action 候选：" + " ".join(
                v["actions"][0] for v in verbs[:6]),
            "why": "题面文案不是装饰，出题人用它命名了业务动作 → 直接当 action/参数值试",
            "evidence": f"命中动词组 {len(verbs)}"})
    if js_paths:
        surfaces.append({
            "prio": 1, "surface": "直接请求 JS/HTML 里出现的路径：" + ", ".join(js_paths[:6]),
            "why": "这些是应用自己的真实端点，比字典命中率高一个数量级",
            "evidence": f"路径 {len(js_paths)} 条"})
    surfaces.append({
        "prio": 9, "surface": "（最后才做）通用目录字典 / 弱口令",
        "why": "小应用上产出最低；开之前必须先跑完上面全部，并给自己定请求上限",
        "evidence": f"已见链接 {len(links)} 条"})
    return surfaces


def analyze(sources: dict) -> dict:
    """sources: {filename: text}。名字含 .css/.js 的按对应语法解析。"""
    html = "\n".join(t for f, t in sources.items()
                     if not f.lower().endswith((".css", ".js")))
    css = "\n".join(t for f, t in sources.items() if f.lower().endswith(".css"))
    js = "\n".join(t for f, t in sources.items() if f.lower().endswith(".js"))
    # HTML 里也可能内联 <style>/<script>
    inline_css = "\n".join(m.group(1) for m in re.finditer(r"<style[^>]*>(.*?)</style>", html, re.I | re.S))
    css = css + "\n" + inline_css
    js = js + "\n" + "\n".join(m.group(1) for m in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.I | re.S))
    blob = html + "\n" + css + "\n" + js

    forms = extract_forms(html)
    links = extract_links(html)
    js_paths = extract_js_paths(js or blob)
    css_classes = extract_css_classes(css)
    used = extract_used_classes(html)
    dang = dangling_classes(css_classes, used)
    verbs = business_verbs(blob)
    surfaces = rank_surfaces(forms, js_paths, dang, verbs, links)
    return {"sources": sorted(sources), "forms": forms, "links": links,
            "js_paths": js_paths, "css_classes": css_classes,
            "used_classes": sorted(used), "dangling": dang,
            "verbs": verbs, "surfaces": surfaces}


# ================================================================ 渲染层
def render(rep: dict) -> str:
    L = []
    L.append(f"══ ctfsurf 前端残迹 → 攻击面 ══  源: {', '.join(rep['sources'])}")
    L.append("")
    L.append(f"── 表单（应用真实的端点+参数词汇表）: {len(rep['forms'])} ──")
    for f in rep["forms"]:
        names = ", ".join(f"{x['name']}:{x['type']}" for x in f["fields"] if x["name"]) or "（无命名字段）"
        hidden = ", ".join(f"{x['name']}={x['value']}" for x in f["fields"]
                           if x["type"] == "hidden" and x["value"]) or "-"
        L.append(f"  {f['method']} {f['action'] or '(当前URL)'}")
        L.append(f"      字段: {names}")
        L.append(f"      hidden 已在用: {hidden}")
    L.append("")
    L.append("── ★ 悬空 CSS 类（定义但未渲染 = 未露出的功能） ──")
    if not rep["dangling"]:
        L.append("  （无）")
    for d in rep["dangling"]:
        L.append(f"  .{d['class']:<18} → {d['surface']:<10} {d['why']}")
    L.append("")
    L.append("── 题面业务动词 → 候选 action 取值 ──")
    if not rep["verbs"]:
        L.append("  （无中文动词命中）")
    for v in rep["verbs"]:
        L.append(f"  {','.join(v['verbs']):<12} → {', '.join(v['actions'])}")
        L.append(f"      {v['why']}")
    L.append("")
    L.append(f"── JS/HTML 里的候选路径: {len(rep['js_paths'])} ──")
    for p in rep["js_paths"][:20]:
        L.append(f"  {p}")
    L.append("")
    L.append(f"── 链接/资源: {len(rep['links'])} ──")
    for u in rep["links"][:15]:
        L.append(f"  {u}")
    L.append("")
    # ★ 反爆破闸门
    small = (len(rep["forms"]) + len(rep["js_paths"])) <= 6
    L.append("─" * 62)
    if small:
        L.append("🚦 反爆破闸门：**小应用**（端点/表单 ≤6）—— 它的攻击面是自己的**词汇表**")
        L.append("   （表单参数 + action 取值 + 悬空类名 + 题面动词，合计个位数），不是通用字典。")
        L.append("   在把下面的清单跑完之前，**不要**开目录字典 / 弱口令爆破。")
    else:
        L.append("🚦 中等应用：先按下面清单逐条试；字典留给清单跑完之后。")
    L.append("")
    L.append("下一步攻击面（按优先级，不是计划书）:")
    for s in rep["surfaces"]:
        tag = "P0" if s["prio"] == 0 else ("P1" if s["prio"] == 1 else "P9")
        L.append(f"  [{tag}] {s['surface']}")
        L.append(f"       为什么: {s['why']}")
        L.append(f"       依据: {s['evidence']}")
    return "\n".join(L)


# ================================================================ IO 层
def fetch_once(url, timeout=15):
    """只发一次 GET（只读）。返回 (final_url, status, text)。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(MAX_INPUT)
        return r.geturl(), getattr(r, "status", r.getcode()), raw.decode("utf-8", "replace")


def collect_dir(root):
    """收集目录下 html/css/js（限量），返回 {relpath: text}。"""
    exts = (".html", ".htm", ".css", ".js")
    out, n = {}, 0
    for base, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.lower().endswith(exts):
                continue
            p = os.path.join(base, fn)
            try:
                out[os.path.relpath(p, root)] = read_capped(p)
            except ValueError:
                continue
            n += 1
            if n >= MAX_FILES:
                return out
    return out


def selftest() -> int:
    """离线自检：不发请求、不读真实产物。"""
    html = """<html><head><link href="/assets/style.css"></head><body>
    <div class="shell"><form method="post" class="stack">
      <input type="hidden" name="action" value="login">
      <input type="text" name="username"><input type="password" name="password">
      <button type="submit">登录</button></form>
      <p>旧系统内部留言板，值班留言请按时发布。</p>
      <script>fetch('/api/preview.php?draft=1'); var u='/msg/post.php';</script>
    </div></body></html>"""
    css = ".shell{}.preview{}.actions{}.admin-grid{}.hero-card{}.message-card{}"
    rep = analyze({"index.html": html, "style.css": css})
    errs = []
    # 表单
    f = rep["forms"][0]
    if (f["method"], f["action"]) != ("POST", ""):
        errs.append(f"表单解析错: {f['method']} {f['action']}")
    names = {x["name"] for x in f["fields"]}
    if not {"action", "username", "password"} <= names:
        errs.append(f"字段缺失: {names}")
    if not any(x["name"] == "action" and x["value"] == "login" for x in f["fields"]):
        errs.append("hidden action=login 没抓到")
    # 悬空类
    dc = {d["class"]: d["surface"] for d in rep["dangling"]}
    for want in ("preview", "actions", "admin-grid"):
        if want not in dc:
            errs.append(f"悬空类漏抓: {want}")
    if dc.get("preview") != "预览/草稿态":
        errs.append(f"preview 语义错: {dc.get('preview')}")
    for cls in ("shell", "stack"):
        if cls in dc:
            errs.append(f"已渲染的类被误报为悬空: {cls}")
    # 语义表：纯布局/未分类不该混进 P0 悬空类清单
    hot_names = [d["class"] for d in rep["dangling"] if d["surface"] not in ("纯布局", "未分类")]
    p0_text = " ".join(p["surface"] for p in rep["surfaces"] if p["prio"] == 0)
    for cls in ("hero-card", "message-card"):
        if cls in p0_text:
            errs.append(f"纯布局类 {cls} 混进了 P0 清单: {p0_text[:80]}")
    if not hot_names:
        errs.append("有意义的悬空类一个都没分出来")
    # JS 路径
    if not any("preview.php" in p for p in rep["js_paths"]):
        errs.append(f"JS 路径漏抓: {rep['js_paths']}")
    if not any("post.php" in p for p in rep["js_paths"]):
        errs.append(f"JS 变量里的路径漏抓: {rep['js_paths']}")
    # 业务动词
    joined = " ".join(v["why"] for v in rep["verbs"])
    if "留言" not in joined:
        errs.append("题面动词没抽到（留言）")
    # 闸门
    if not any(p["prio"] == 0 for p in rep["surfaces"]):
        errs.append("P0 攻击面没排出来")
    if not any("穷举 action" in p["surface"] for p in rep["surfaces"]):
        errs.append("没把 action 取值穷举排成 P0")
    # 安全读文件
    for bad in ("/dev/zero", "/dev/null"):
        try:
            read_capped(bad)
            errs.append(f"read_capped 没拒绝 {bad}")
        except ValueError:
            pass
    # 静态自检：确认单次 GET 入口还在（真正的静态守卫在 board/tests/test_ctfsurf.py）
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    if "urlopen" not in src:
        errs.append("fetch_once 不见了")
    print(f"ctfsurf selftest: {'FAIL ' + str(len(errs)) if errs else 'OK'} "
          f"({len(rep['dangling'])} 悬空类 / {len(rep['verbs'])} 动词组 / "
          f"{len(rep['surfaces'])} 攻击面)")
    for e in errs:
        print("  ✗", e)
    if not errs:
        print(render(rep).split("─" * 62)[0][:1200])
    return 1 if errs else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="前端残迹 → 攻击面推演（离线优先，先推演再爆破）")
    ap.add_argument("files", nargs="*", help="本地 html/css/js 文件")
    ap.add_argument("--dir", help="分析目录下所有 html/css/js")
    ap.add_argument("--url", help="只读 GET 一次并分析（限速 1 次，不爬站）")
    ap.add_argument("--save", help="配合 --url：把响应原文存到该文件")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    sources = {}
    for f in args.files:
        try:
            sources[f] = read_capped(f)
        except ValueError as e:
            print(f"跳过 {f}: {e}", file=sys.stderr)
    if args.dir:
        sources.update(collect_dir(args.dir))
    if args.url:
        try:
            time.sleep(0)                       # 单次请求，无需限速；留位以便将来扩展
            final, status, text = fetch_once(args.url)
            key = "url:" + final
            sources[key] = text
            print(f"[fetch] {status} {final} ({len(text)}B)", file=sys.stderr)
            if args.save:
                with open(args.save, "w", encoding="utf-8") as fh:
                    fh.write(text)
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"[fetch] 失败: {e}", file=sys.stderr)
    if not sources:
        print("没有输入。给文件、--dir 或 --url（或 --selftest）。", file=sys.stderr)
        return 2

    rep = analyze(sources)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    else:
        print(render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
