import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** YYYY-MM-DD から M/D 表記を返す（例: 2026-10-15 -> 10/15, 2026-06-03 -> 6/3） */
export function formatMonthDay(dateStr: string): string {
  const parts = dateStr.split("-");
  if (parts.length < 3) return dateStr;
  const m = parseInt(parts[1], 10);
  const d = parseInt(parts[2], 10);
  if (isNaN(m) || isNaN(d)) return dateStr.slice(5).replace("-", "/");
  return `${m}/${d}`;
}
