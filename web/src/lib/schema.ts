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
  byArea: z.record(z.string(), areaStatusSchema),
});

export const dataSchema = z.object({
  facility: z.string(),
  bookingType: z.string(),
  updatedAt: z.string(),
  areas: z.array(z.string()),
  periodOrder: z.array(z.string()).default(["午前", "午後1", "午後2", "夜間"]),
  days: z.array(daySchema),
});

export type GymData = z.infer<typeof dataSchema>;
export type DayData = z.infer<typeof daySchema>;
export type AreaStatus = z.infer<typeof areaStatusSchema>;

/** 予約で取れる（予約可能）とみなすステータス */
export function isReservable(status: string): boolean {
  return status === "available" || status === "partial";
}
