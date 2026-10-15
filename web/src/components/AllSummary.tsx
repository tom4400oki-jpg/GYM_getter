import { useEffect, useMemo, useState } from "react";
import { cn, formatMonthDay } from "@/lib/utils";
import {
  dataSchema,
  dayHasReservable,
  dayHasEveningReservable,
  isReservable,
  type DayData,
} from "@/lib/schema";
import type { FacilityMeta } from "@/lib/facilities";

type Props = {
  facilities: FacilityMeta[];
  onlyWeekend: boolean;
  onlyEvening: boolean;
  /** 施設チップをタップしたらその施設タブへ移動 */
  onSelect: (facilityId: string) => void;
};

// 施設×日の予約可能ヒット（day は施設ごとに保持し、夜間フィルタも施設単位で判定する）
type Hit = { fac: FacilityMeta; day: DayData; slots: number };
type Row = { date: string; weekday: string; isHoliday: boolean; hits: Hit[] };

/** その日の予約可能スロット数（periods 不明の日は日単位で1と数える） */
function reservableSlots(day: DayData): number {
  let n = 0;
  for (const a of Object.values(day.byArea)) {
    const ps = Object.entries(a.periods ?? {});
    if (ps.length > 0) {
      for (const [, st] of ps) if (isReservable(st)) n++;
    } else if (isReservable(a.status)) {
      n++;
    }
  }
  return n;
}

/**
 * カテゴリ内の全施設を横断して「予約できる日 → 空きのある施設」を一覧する俯瞰ビュー。
 * 「今週末どこか空いてる？」に1タップで答えるためのタブ。
 */
export default function AllSummary({
  facilities,
  onlyWeekend,
  onlyEvening,
  onSelect,
}: Props) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [failed, setFailed] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setFailed([]);
    (async () => {
      const results = await Promise.all(
        facilities.map(async (fac) => {
          try {
            const res = await fetch(`/${fac.file}?t=${Date.now()}`, {
              cache: "no-store",
            });
            if (!res.ok) throw new Error(String(res.status));
            return { fac, data: dataSchema.parse(await res.json()) };
          } catch {
            return { fac, data: null };
          }
        })
      );
      if (cancelled) return;
      setFailed(results.filter((r) => !r.data).map((r) => r.fac.short));

      const byDate = new Map<string, Row>();
      for (const { fac, data } of results) {
        if (!data) continue;
        for (const day of data.days) {
          if (!dayHasReservable(day)) continue;
          const existing = byDate.get(day.date);
          const row: Row = existing ?? {
            date: day.date,
            weekday: day.weekday,
            isHoliday: day.isHoliday,
            hits: [],
          };
          row.hits.push({ fac, day, slots: reservableSlots(day) });
          if (!existing) {
            byDate.set(day.date, row);
          }
        }
      }
      setRows([...byDate.values()].sort((a, b) => a.date.localeCompare(b.date)));
    })();
    return () => {
      cancelled = true;
    };
  }, [facilities]);

  const visible = useMemo(() => {
    if (!rows) return [];
    let vs = rows;
    if (onlyWeekend)
      vs = vs.filter((r) => r.weekday === "土" || r.weekday === "日" || r.isHoliday);
    if (onlyEvening)
      vs = vs
        .map((r) => ({ ...r, hits: r.hits.filter((h) => dayHasEveningReservable(h.day)) }))
        .filter((r) => r.hits.length > 0);
    return vs;
  }, [rows, onlyWeekend, onlyEvening]);

  if (rows === null) {
    return <p className="py-8 text-center text-xs text-slate-400">読み込み中…</p>;
  }

  return (
    <div className="overflow-hidden rounded border border-slate-200">
      {failed.length > 0 && (
        <p className="border-b border-slate-100 bg-amber-50 px-2 py-1 text-[10px] text-amber-600">
          取得できなかった施設: {failed.join("・")}
        </p>
      )}
      {visible.length === 0 && (
        <p className="py-6 text-center text-xs text-slate-400">該当する日がありません</p>
      )}
      {visible.map((row) => {
        const dateColor =
          row.weekday === "日" || row.isHoliday
            ? "text-red-500"
            : row.weekday === "土"
              ? "text-sky-600"
              : "text-slate-700";
        return (
          <div
            key={row.date}
            className="flex items-start gap-2 border-b border-slate-100 px-2 py-1.5 last:border-0"
          >
            <div
              className={cn(
                "w-16 shrink-0 pt-0.5 text-[11px] font-bold whitespace-nowrap",
                dateColor
              )}
            >
              {formatMonthDay(row.date)}
              <span className="ml-0.5 text-[9px] font-normal">
                ({row.weekday}
                {row.isHoliday ? "・祝" : ""})
              </span>
            </div>
            <div className="flex flex-wrap gap-1">
              {row.hits.map(({ fac, slots }) => (
                <button
                  key={fac.id}
                  onClick={() => onSelect(fac.id)}
                  className="rounded border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 active:bg-emerald-100"
                >
                  {fac.short}
                  <span className="ml-0.5 text-[9px] font-normal text-emerald-500">
                    {slots}枠
                  </span>
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
