import { useState } from "react";
import { api } from "../api";
import { usePoll } from "../lib/poll";

/**
 * 作战条（W-P0-1）—— 信条 P1：时间是一等公民。
 *
 * 数据来自 /api/ops（服务端聚合 fgs deadline / cost / next / workers）。
 * ★ 每一路都可能失败（没黑板 / 没 token / fgs 超时），这里**一律显示「—」或原因**，
 *   绝不把失败渲染成 0：写死 "pi 1.1.0" 那次就是拿默认值冒充事实。
 *
 * ★ 2026-11-05 信息密度重排：原来 4 项平铺、右侧一大片空。
 *   现在按「会不会改变下一步动作」排序并分区：
 *     时间（第一变量） → 得分 / 每旗成本（值不值） → 在跑（有没有人在干） → 下一刀（做什么）
 *   分区用发丝线隔开，读的时候是一条扫下来的线，不是一个一个找。
 */

type One<T> = { ok: true; data: T } | { ok: false; error: string };

type Units = { lanes?: unknown[]; subagents?: unknown[]; error?: string };

type Ops = {
  ts?: number;
  pid?: string | null;
  board?: { nodes?: number | null; verified?: number | null } | null;
  deadline?: One<{ left_seconds?: number | null }>;
  cost?: One<{ points?: number | null; flags?: number | null; solved?: boolean; confirmed?: boolean }>;
  costImage?: {
    points?: number | null; flags?: number | null; perFlag?: number | null;
    verifiedPerStep?: number | null; solved?: boolean | null; confirmed?: boolean | null;
  } | null;
  units?: Units | null;
  flagsLeft?: { known?: boolean; left?: number; tasks?: number; reason?: string } | null;
  next?: One<{ task?: string; target?: string | null; why?: string }>;
  flags?: { total?: number; unique?: number; error?: string };
  piVersion?: string;
  error?: string;
};

function hhmmss(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const x = s % 60;
  const p = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? p(h) + ":" + p(m) + ":" + p(x) : p(m) + ":" + p(x);
}

export default function OpsBar() {
  const [ops, setOps] = useState<Ops | null>(null);

  // 与服务端 10s 缓存对齐，别空转；#22 再加一层「payload 没变就不 setState」——
  // 原来每 10s 无条件重渲染一次，展开态/滚动位置都被顶。
  usePoll(
    () => api.ops() as Promise<Ops>,
    10000,
    (d) => setOps(d),
    { onError: () => setOps({ error: "拿不到作战数据（服务端 /api/ops 失败）" }) },
  );

  const left = ops?.deadline?.ok ? ops.deadline.data.left_seconds : undefined;
  const deadlineUnknown =
    !ops ? "…" : ops.deadline && !ops.deadline.ok ? "未设" : left === null || left === undefined ? "未设" : null;
  const urgent = typeof left === "number" && left > 0 && left < 3600;
  const soon = typeof left === "number" && left >= 3600 && left < 3 * 3600;

  const ci = ops?.costImage || null;
  const cost = ops?.cost?.ok ? ops.cost.data : null;
  const points = ci?.points ?? cost?.points;
  const next = ops?.next?.ok ? ops.next.data : null;

  const lanes = ops?.units?.lanes?.length ?? 0;
  const subs = ops?.units?.subagents?.length ?? 0;
  const running = lanes + subs;
  const unitsKnown = !!ops?.units && !ops.units.error;
  const fl = ops?.flagsLeft || null;

  return (
    <div className="opsbar">
      {/* ① 剩余时间：限时赛第一变量 —— 唯一给到 15px 的数 */}
      <span className={(urgent ? "ops urgent " : soon ? "ops soon " : "ops ") + "time"}>
        <em>剩余</em>
        <b className="mono">{deadlineUnknown ?? hhmmss(left as number)}</b>
        {deadlineUnknown ? (
          <span
            className="hint"
            title={ops?.deadline && !ops.deadline.ok ? ops.deadline.error : "用 fgs deadline set 设定总截止时间"}
          >
            设 deadline
          </span>
        ) : null}
      </span>

      <span className="ops-sep" />

      {/* ② 得分（平台口径）+ 每旗成本（值不值；边际递减陡是实测，不是猜） */}
      <span className="ops" title="平台得分（fgs cost 的 points）">
        <em>得分</em>
        <b className="mono">{typeof points === "number" ? points : "—"}</b>
        {ci?.solved ? <span className="pill ok">已解</span> : null}
        {ci?.confirmed ? <span className="pill ok">已确认</span> : null}
      </span>
      <span className="ops optional" title="每拿下一个 flag 的花费 —— 边际递减陡时该换题，不是加钱">
        <em>每旗</em>
        <b className="mono">{typeof ci?.perFlag === "number" ? "¥" + Math.round(ci.perFlag) : "—"}</b>
      </span>

      <span className="ops-sep" />

      {/* ③ 工作单元（W-P1-1）：派出去的活现在到底有没有人在跑 */}
      <span className="ops" title={"黑板活路 " + lanes + " · 子代理 " + subs + "（在跑才计）"}>
        <em>在跑</em>
        <b className="mono">{unitsKnown ? running : "—"}</b>
        {running > 0 ? <span className="live-dot" /> : null}
      </span>

      {/* ③b 剩余可交次数（W-P1-1）：错交是硬扣分，这个数直接决定「敢不敢交」。
          平台拿不到就显示 ? 并给原因 —— 绝不写 0（0 会让人以为一次都不能交了）。 */}
      <span className="ops" title={fl?.known ? "平台题单里还能提交的次数（错交扣分，交之前看它）" : ("不知道：" + (fl?.reason || "平台无响应"))}>
        <em>可交</em>
        <b className="mono">{fl?.known ? fl.left : "?"}</b>
      </span>

      <span className="ops-sep" />

      {/* ④ 下一刀：机器给的唯一答案，吃掉剩余宽度 */}
      <span className="ops grow" title={next?.why || ""}>
        <em>下一刀</em>
        <b>{next ? String(next.task || "none") : "—"}</b>
        {next?.why ? <span className="hint ell">{" " + next.why}</span> : null}
      </span>

      {ops?.error ? <span className="pill bad">{ops.error}</span> : null}
    </div>
  );
}
