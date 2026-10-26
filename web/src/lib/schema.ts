import { z } from "zod";

export const areaStatusSchema = z.object({
  status: z.string(),
  statusRaw: z.string(),
  // 時間帯 -> ステータス（例 { "午後2": "available" }）。空きの日のみ埋まる
  periods: z.record(z.string(), z.string()).default({}),
});

export const daySchema = z.object({
  date: z.string(),
  weekday: z.string(),
  // 日本の祝日（土日は weekday で判定）。旧データには無いので既定 false
  isHoliday: z.boolean().default(false),
  byArea: z.record(z.string(), areaStatusSchema),
});

export const dataSchema = z.object({
  facility: z.string(),
  bookingType: z.string(),
  // ふれあいネットの予約導線URL（旧データには無いので任意、nullも許容）
  reserveUrl: z.string().url().nullish(),
  updatedAt: z.string(),
  areas: z.array(z.string()),
  periodOrder: z.array(z.string()).default(["午前", "午後1", "午後2", "夜間"]),
  days: z.array(daySchema),
});

/** ふれあいネット予約ページ（高機能版）のフォールバックURL */
export const FALLBACK_RESERVE_URL =
  "https://www.fureai-net.city.kawasaki.jp/user/view/user/homeIndex.html";

/** updatedAt が古いとみなす閾値（時間）。2時間周期の cron が1回遅延・スキップされても誤警告にならないよう4時間に設定 */
export const STALE_HOURS = 4;

/** updatedAt が古い（既定4時間超）かどうか。サイレント故障の利用者向けセーフティネット */
export function isStale(iso: string, maxAgeMs = STALE_HOURS * 60 * 60 * 1000): boolean {
  const t = new Date(iso).getTime();
  if (isNaN(t)) return false;
  return Date.now() - t > maxAgeMs;
}

export type GymData = z.infer<typeof dataSchema>;
export type DayData = z.infer<typeof daySchema>;
export type AreaStatus = z.infer<typeof areaStatusSchema>;

/** 予約で取れる（予約可能）とみなすステータス */
export function isReservable(status: string): boolean {
  return status === "available" || status === "partial";
}

/** 土日または祝日か（休日料金・土日祝フィルタの判定） */
export function isWeekendOrHoliday(day: DayData): boolean {
  return day.weekday === "土" || day.weekday === "日" || day.isHoliday;
}

export function dayHasReservable(day: DayData): boolean {
  return Object.values(day.byArea).some((a) => isReservable(a.status));
}

/** 18時以降に始まる枠か（体育館『夜間』18:30〜／テニスは時刻表記'18:30'等） */
export function isEveningPeriod(label: string): boolean {
  if (label === "夜間") return true;
  const m = /^(\d{1,2}):/.exec(label);
  return m ? Number(m[1]) >= 18 : false;
}

export function dayHasEveningReservable(day: DayData): boolean {
  return Object.values(day.byArea).some((a) =>
    Object.entries(a.periods ?? {}).some(
      ([p, st]) => isEveningPeriod(p) && isReservable(st)
    )
  );
}
