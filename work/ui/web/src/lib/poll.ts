import { useEffect, useRef } from "react";

/**
 * 轮询去重（#22）
 *
 * 为什么要有：7 处 setInterval 原来都是「拿到就 setState」。payload 绝大多数轮次
 * 一模一样，但 setState 每次都触发重渲染 —— 展开态被顶掉、列表闪、CPU 白烧。
 * ARTEX 的做法是存上次 payload 的序列化结果，相同就直接 return
 * （web/src/app/(main)/function/tasks/page.tsx 的 if (sig === lastRef.current) return;），
 * 这里抽成 hook，7 处共用。
 *
 * 判据是**签名**（JSON 序列化）而不是引用：服务端每次都新造对象，引用必然不等。
 * 代价是每轮序列化一次；对几 KB 的 payload 可以忽略，换来的是「无变化不重渲染」。
 */

export function sigOf(v: unknown): string {
  try {
    return JSON.stringify(v) ?? "null";
  } catch {
    return String(v);
  }
}

export type PollOpts = { onError?: (e: unknown) => void };

/**
 * 每 ms 跑一次 fn；只有 payload 签名变化时才把结果交给 apply。
 * 先 tick 再定时（与各页原行为一致），卸载/切页安全。
 */
export function usePoll<T>(
  fn: () => Promise<T>,
  ms: number,
  apply: (v: T) => void,
  opts?: PollOpts,
): void {
  // 用 ref 兜住最新闭包：ms 不变就不重建定时器（重建会漏一轮/闪一下）
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const applyRef = useRef(apply);
  applyRef.current = apply;
  const errRef = useRef(opts?.onError);
  errRef.current = opts?.onError;

  useEffect(() => {
    let alive = true;
    let last = "";
    const tick = async () => {
      try {
        const v = await fnRef.current();
        if (!alive) return;
        const s = sigOf(v);
        if (s === last) return; // 一模一样：不 setState
        last = s;
        applyRef.current(v);
      } catch (e) {
        if (alive && errRef.current) errRef.current(e);
      }
    };
    void tick();
    const t = setInterval(() => void tick(), ms);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [ms]);
}
