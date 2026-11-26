/**
 * #41 工具卡定位回跳的通道（跨页跳转用）。
 *
 * 为什么不用 URL hash：要定位的是「一段 flag 正文」，hash 里带长文本会被浏览器编码、
 * 也会污染历史栈；而且刷新页面时不该重放一次跳转。
 * 为什么需要 pending：从「审证」页点过来时，对话页是**刚挂载**的 —— 事件已经发完了，
 * 所以意图必须先存下，由后来挂载的页面 take 走（一次性，取完即清）。
 */
let pending = "";
const subs = new Set<() => void>();

export const LOCATE_EVENT = "pictf:locate";

/** 请求「跳到对话页里那条含这段文本的工具调用」。text 一般是 flag 值或输出去重后的片段。 */
export function requestLocate(text: string): void {
  const t = (text || "").trim();
  if (!t) return;
  pending = t;
  for (const f of subs) {
    try { f(); } catch { /* 单个订阅者出错不该拖垮整条通道 */ }
  }
  try { window.dispatchEvent(new Event(LOCATE_EVENT)); } catch { /* 非浏览器环境 */ }
}

/** 取走待处理的定位请求（一次性）：没有就返回空串。 */
export function takeLocate(): string {
  const t = pending;
  pending = "";
  return t;
}

/** 订阅「有新的定位请求」；返回取消函数。 */
export function onLocate(f: () => void): () => void {
  subs.add(f);
  return () => { subs.delete(f); };
}