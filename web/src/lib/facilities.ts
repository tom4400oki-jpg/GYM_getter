import { z } from "zod";

// facilities.json（scripts/scrape_fureai.py の FACILITIES から生成）のスキーマ。
// 施設タブ・料金・利用時刻の単一の真実は Python 側にあり、フロントはこれを解釈するだけ。

const yenByPeriodSchema = z.record(z.string(), z.number());

const priceSetSchema = z.object({
  weekday: yenByPeriodSchema,
  holiday: yenByPeriodSchema,
});

const priceSchema = z.object({
  // エリア名の部分一致 → 料金区分キー（先勝ち）。当たらなければ default。
  match: z.array(z.object({ contains: z.string(), key: z.string() })).default([]),
  default: z.string(),
  sets: z.record(z.string(), priceSetSchema),
  // 季節別料金（該当月は上書き）。富士見テニスのみ。
  seasonal: z
    .array(z.object({ period: z.string(), months: z.array(z.number()), yen: z.number() }))
    .default([]),
});

export const facilityMetaSchema = z.object({
  id: z.string(),
  name: z.string(),
  short: z.string(),
  file: z.string(),
  cat: z.enum(["hall", "tennis"]),
  periodOrder: z.array(z.string()),
  periodTimes: z.record(z.string(), z.string()).default({}),
  areaShort: z.record(z.string(), z.string()).default({}),
  prices: priceSchema.nullish(),
});

export const facilitiesIndexSchema = z.object({
  updatedAt: z.string(),
  categories: z.array(z.object({ id: z.enum(["hall", "tennis"]), label: z.string() })),
  facilities: z.array(facilityMetaSchema).min(1),
});

export type FacilityMeta = z.infer<typeof facilityMetaSchema>;
export type FacilitiesIndex = z.infer<typeof facilitiesIndexSchema>;
export type Category = FacilityMeta["cat"];

export async function loadFacilities(): Promise<FacilitiesIndex> {
  const res = await fetch(`/facilities.json?t=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`施設定義の取得に失敗しました (${res.status})`);
  return facilitiesIndexSchema.parse(await res.json());
}

/** 時間帯ラベルから利用時刻文字列を返す。 */
export function periodTime(fac: FacilityMeta, p: string): string {
  return fac.periodTimes[p] ?? "";
}

/** エリア名の短縮表示名。 */
export function shortArea(fac: FacilityMeta, area: string): string {
  return fac.areaShort[area] ?? area;
}

/** 施設×エリア×時間帯×休日区分×日付から使用料（円）を返す。
 * holiday=true（土日祝）は休日料金。 */
export function priceFor(
  fac: FacilityMeta,
  area: string,
  period: string,
  holiday: boolean,
  date: string
): number | null {
  const pr = fac.prices;
  if (!pr) return null;
  // 季節別料金（該当月は区分に関係なく上書き）
  const month = Number(date.slice(5, 7));
  const seasonal = pr.seasonal.find(
    (s) => s.period === period && s.months.includes(month)
  );
  if (seasonal) return seasonal.yen;
  const rule = pr.match.find((r) => area.includes(r.contains));
  const set = pr.sets[rule ? rule.key : pr.default];
  if (!set) return null;
  return (holiday ? set.holiday : set.weekday)[period] ?? null;
}
