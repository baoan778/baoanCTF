import { useSyncExternalStore } from "react";
import { sigOf } from "./poll";

/**
 * 共享的 /api/status（#22 里的「真·重复」那一半）
 *
 * 原状：App 顶部绿点每 5s 拉一次 /api/status，SystemPage 也每 5s 拉同一个接口 ——
 * 同一份进程状态被问两遍。现在只由 App 轮询，结果发到这个 store；
 * SystemPage 订阅它，需要立刻刷新时仍可自己 api.status() 再 publish。
 *
 * 为什么用 store 而不是 React context：App 的 status 只用到 ok，
 * 而 context 会把整棵树包起来改渲染结构；这里要的只是「同一份数据两个读者」。
 */
let value: unknown = null;
let lastSig = "";
const subs = new Set<() => void>();

export function publishStatus(v: unknown): void {
  const s = sigOf(v);
  if (s === lastSig) return; // 没变：不惊动任何订阅者
  lastSig = s;
  value = v;
  for (const f of Array.from(subs)) f();
}

function getSnapshot(): unknown {
  return value;
}

function subscribe(f: () => void): () => void {
  subs.add(f);
  return () => {
    subs.delete(f);
  };
}

export function useSharedStatus<T>(): T | null {
  return useSyncExternalStore(subscribe, getSnapshot) as T | null;
}
