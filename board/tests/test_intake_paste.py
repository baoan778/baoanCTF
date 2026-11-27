#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""录题台的粘贴回归：终端粘贴（bracketed paste）+ Ctrl+Y 读剪贴板

为什么单独建这个测试（2026-10-10 用户实测报障）：
  「有些东西粘贴不进录题台，我连文字都粘贴不进去」。
  根因是两条**设计假设撞上终端现实**：
    ① Windows Terminal 默认把 Ctrl+V 绑成 `Terminal.PasteFromClipboard`
       （settings.json 里实测有这条）→ 终端自己吃掉 0x16，pi 收不到 `Key.ctrl("v")`，
       录题台「Ctrl+V 读剪贴板」永远不触发；
    ② 粘贴内容以 `ESC[200~` 开头，而 onKey 末尾那句「只有可见字符才插入」
       （`!data.startsWith("\\x1b")`）把整段**默默丢掉** → 连文字都没反应。
  修法：① 收 `Ctrl+Y`（WT 默认没绑）；② 直接识别终端粘贴序列（含分片送达）。

本测试的边界：
  · 不碰真产物：PI_LAB_ROOT 指向临时目录（内含 readClipboard 走的 log/），
    所以它**不会**去读真剪贴板、也不会写 work/inbox。
  · 用 stub 顶掉 `@earendil-works/pi-tui`（真包只存在于 pi 进程内部，node 直接 import 不到）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _pkgpaths import require_ext, find_node  # noqa: E402

# ★ Windows/发布包适配：控制台默认 cp936(GBK)，直接 print 中文/✓/✗ 会抛
#   UnicodeEncodeError 让整条命令挂掉。两只流改 utf-8+replace（永不报错）。
#   自包含（自己 import sys），不依赖本文件是否已 import sys 或用别名。
try:
    import sys as _sysmod_
    for _s in (_sysmod_.stdout, _sysmod_.stderr):
        _s.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LAB = Path(__file__).resolve().parents[2]
EXT = require_ext("ctf-intake-tui.ts")

passed, failed = [], []


def check(name, ok, detail=""):
    (passed if ok else failed).append(name)
    print(("  ✓ " if ok else "  ✗ ") + name + (f"   [{detail}]" if (detail and not ok) else ""))


def build_sandbox(root: Path):
    """stub pi-tui + 复制扩展（复制不是改：测的是源文件的当前内容）"""
    nj = root / "node_modules" / "@earendil-works" / "pi-tui"
    nj.mkdir(parents=True)
    (nj / "package.json").write_text(json.dumps({"name": "@earendil-works/pi-tui",
                                                 "type": "module", "main": "index.js"}), encoding="utf-8")
    (nj / "index.js").write_text(r"""
// 只实现本测试用得到的语义；其余键一律 false（宁可漏匹配，不要假匹配）
// 注意：Key.ctrl / Key.shift / Key.alt 是**函数**（真 pi-tui 里就是这么用的），
//      直接写成对象会在 onKey 里报 "Key.shift is not a function"（第一版踩过）
const K = (id) => ({ __k: id });
const MAP = {
  "\x16": "ctrl+v", "\x19": "ctrl+y", "\x13": "ctrl+s", "\r": "enter", "\n": "enter",
  "\t": "tab", "\x1b[Z": "shift+tab", "\x1b": "escape",
  "\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left",
  "\x7f": "backspace", "\x1b[3~": "delete", "\x1b[H": "home", "\x1b[F": "end",
};
export const Key = {
  ctrl: (c) => K("ctrl+" + c),
  shift: (c) => K("shift+" + c),
  alt: (c) => K("alt+" + c),
  enter: K("enter"), tab: K("tab"), up: K("up"), down: K("down"),
  left: K("left"), right: K("right"), home: K("home"), end: K("end"),
  backspace: K("backspace"), delete: K("delete"), escape: K("escape"),
};
export function matchesKey(data, key) {
  const want = key && key.__k;
  return want != null && MAP[data] === want;
}
export function visibleWidth(s) { return [...String(s)].length; }
""", encoding="utf-8")
    shutil.copy2(EXT, root / "ext.ts")


RUN_JS = r"""
import { createForm, createPasteAssembler } from "./ext.ts";


// 真扩展的契约：onKey 只返回动作，**调用方**负责 move()
// （第一版测试忘了这步 → 粘贴全落进第一栏，白报 3 条失败）
function press(f, key) {
  const act = f.onKey(key);
  if (act === "next") f.move(1);
  else if (act === "prev") f.move(-1);
  return act;
}

const out = [];
const t = (name, ok, detail = "") => out.push({ name, ok, detail });

// ── ① 装配器：分片 / 整段 / 非粘贴 ──
{
  const a = createPasteAssembler();
  t("非粘贴输入 → null（不能吞掉打字）", a.push("hello") === null);
  const b = createPasteAssembler();
  t("半截粘贴 → null 且 waiting=true", b.push("\x1b[200~abc") === null && b.waiting === true);
  t("分片收全 → 返回正文", b.push("def") === null && b.push("\x1b[201~") === "abcdef");
  t("收完后不再 waiting", b.waiting === false);
  const c = createPasteAssembler();
  t("整段一次到 → 返回正文", c.push("\x1b[200~x\ny\x1b[201~") === "x\ny");
  const d = createPasteAssembler();
  d.push("\x1b[200~one");
  t("连续两次粘贴互不污染", d.push("\x1b[201~") === "one" && d.push("\x1b[200~two\x1b[201~") === "two");
}

// ── ② 表单：粘贴进「题目内容」栏（多行保留）──
{
  const f = createForm();
  for (let i = 0; i < 5; i++) press(f, "\t");          // 移动到最后一栏「题目内容」
  f.onKey("\x1b[200~第一行\nflag{test}\n第三行\x1b[201~");
  const v = f.cur().value;
  t("粘贴落进当前栏", f.cur().key === "desc" && v.includes("flag{test}"), "值=" + JSON.stringify(v.slice(0, 40)));
  t("多行保留（题面要能整段灌）", v.split("\n").length === 3, "行数=" + v.split("\n").length);
  t("有回执消息", /灌入|填入/.test(f.st.msg), "msg=" + f.st.msg);
}

// ── ③ 分片粘贴（大段题面会分多次到）也不许丢半截 ──
{
  const f = createForm();
  f.onKey("\x1b[200~part1 ");
  f.onKey("part2 ");
  f.onKey("part3\x1b[201~");
  t("分片粘贴收全", f.cur().value.trim() === "part1 part2 part3", JSON.stringify(f.cur().value));
}

// ── ④ 打字仍然生效；方向键等控制序列不污染 ──
{
  const f = createForm();
  f.onKey("a"); f.onKey("b");
  const typed = f.cur().value;
  f.onKey("\x1b[A"); f.onKey("\x1b[B");                 // ↑↓（首栏 = 换栏，不该插入字符）
  t("可见字符照常插入", typed === "ab", JSON.stringify(typed));
  t("控制序列不写进字段", !/[\x1b\[\]]/.test(f.cur().value), JSON.stringify(f.cur().value));
}

// ── ⑤ Ctrl+Y / Ctrl+V 都走「读剪贴板」分支（stub 下 winPath 失败 → 给提示，不崩）──
{
  const f = createForm();
  f.onKey("\x19");                                      // Ctrl+Y
  const y = f.st.msg;
  f.onKey("\x16");                                      // Ctrl+V（真终端里多半到不了这）
  const v = f.st.msg;
  t("Ctrl+Y 触发读剪贴板分支", /剪贴板/.test(y), "msg=" + y);
  t("Ctrl+V 同样触发", /剪贴板/.test(v), "msg=" + v);
}

// ── ⑥ 附件栏：按行追加（粘贴多个路径）──
{
  const f = createForm();
  for (let i = 0; i < 4; i++) press(f, "\t");             // → 附件路径
  f.onKey("\x1b[200~C:\\a.zip\nD:\\b.zip\x1b[201~");
  t("附件按行追加", f.cur().value.split("\n").length === 2, JSON.stringify(f.cur().value));
}

console.log(JSON.stringify(out));
"""


def main():
    print("═══ 录题台粘贴回归 ═══")
    if not EXT.exists():
        print("  ✗ 找不到扩展文件:", EXT)
        return 1
    node = find_node()
    if not node:
        print("  (跳过：本机没有 node)")
        return 0

    src = EXT.read_text(encoding="utf-8")
    # 源码级判据：两条修法必须在（行为测试靠 stub，这里防"改法被回退"）
    check("识别终端粘贴序列 ESC[200~", "\\x1b[200~" in src)
    check("识别粘贴结束 ESC[201~", "\\x1b[201~" in src)
    check("onKey 里粘贴优先于可见字符规则",
          src.index("paste.push(data)") < src.index('data >= " "'))
    check("收 Ctrl+Y（WT 没绑的键位）", 'Key.ctrl("y")' in src)
    check("三条路共用一个 fillFromText", src.count("fillFromText(") >= 3)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        build_sandbox(root)
        (root / "run.mjs").write_text(RUN_JS, encoding="utf-8")
        env = dict(os.environ)
        env["PI_LAB_ROOT"] = str(root)          # ★ 隔离：不读真剪贴板、不写真 inbox
        r = subprocess.run([node, "--experimental-strip-types", "run.mjs"],
                           cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, env=env)
        if r.returncode != 0 or not r.stdout.strip():
            print("  ✗ node 跑挂了:\n" + (r.stderr or "")[-1200:])
            return 1
        try:
            rows = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception as e:
            print("  ✗ 结果解析失败:", e, r.stdout[-300:])
            return 1
        for row in rows:
            check(row["name"], bool(row["ok"]), row.get("detail", ""))

    print(f"\n通过 {len(passed)} / 失败 {len(failed)}")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("✓ 录题台：终端粘贴 / Ctrl+Y / 分片 / 附件 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
