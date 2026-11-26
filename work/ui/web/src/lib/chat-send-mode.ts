import { useSyncExternalStore } from "react";

/**
 * 发送键位（#26）
 *
 * 为什么可配：Enter 一键发送最快，但中文输入法组字时回车是「选词」——
 * 不判 isComposing 就发送，会把半截指令发出去。所以留两档：
 * 默认 Ctrl+Enter（安全档），用户显式切到 Enter 才享受一键发送，
 * 两条路径都先放行输入法组字（ARTEX lib/chat-send-mode.ts:56-65 的顺序：
 * 先判 nativeEvent.isComposing || keyCode === 229，再判模式）。
 */
export type SendMode = "ctrl-enter" | "enter";

const KEY = "pictf.sendMode";
const subs = new Set<() => void>();

function readMode(): SendMode {
  try {
    return localStorage.getItem(KEY) === "enter" ? "enter" : "ctrl-enter";
  } catch {
    return "ctrl-enter"; // 无痕模式
  }
}

let mode: SendMode = readMode();

export function subscribeSendMode(f: () => void): () => void {
  subs.add(f);
  return () => {
    subs.delete(f);
  };
}

export function getSendMode(): SendMode {
  return mode;
}

export function setSendMode(m: SendMode): void {
  mode = m;
  try {
    localStorage.setItem(KEY, m);
  } catch {
    /* 存不下就只在本次生效 */
  }
  for (const f of Array.from(subs)) f();
}

export function useSendMode(): SendMode {
  return useSyncExternalStore(subscribeSendMode, getSendMode);
}

/** 键位说明：placeholder 与设置项共用一句，避免两处文案漂移 */
export function sendModeHint(m: SendMode): string {
  return m === "enter" ? "Enter 发送（Shift+Enter 换行）" : "Ctrl+Enter 发送";
}

/** 按键事件的最小形状：不 import React 类型，键盘事件与合成事件都能喂进来 */
type KeyLike = {
  key: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  isComposing?: boolean;
  nativeEvent?: { isComposing?: boolean; keyCode?: number };
};

/** 该不该把这一下 Enter 当成「发送」——输入法组字一律放行 */
export function shouldSubmitOnKey(e: KeyLike, m: SendMode): boolean {
  // ① 输入法正在组字：回车是选词，不是发送（否则发出去的是半截指令）
  if (e.nativeEvent?.isComposing || e.isComposing) return false;
  // ② 老浏览器/部分输入法在组字时 keyCode 是 229，isComposing 可能来不及置位
  if (e.nativeEvent?.keyCode === 229) return false;
  if (e.key !== "Enter" || e.altKey) return false;
  if (m === "enter") return !e.shiftKey; // Shift+Enter 仍换行
  // 显式归一成 boolean：调用方拿到的永远是 true/false，不是 undefined
  return !!(e.ctrlKey || e.metaKey);
}
