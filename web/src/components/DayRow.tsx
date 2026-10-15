import { ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn, formatMonthDay } from "@/lib/utils";
import { meta } from "@/lib/status";
import { isReservable, isWeekendOrHoliday, type DayData } from "@/lib/schema";
import {
  periodTime,
  priceFor,
  shortArea,
  type FacilityMeta,
} from "@/lib/facilities";

type Props = {
  day: DayData;
  fac: FacilityMeta;
  areas: string[];
  periodOrder: string[];
  isOpen: boolean;
  onToggle: (date: string) => void;
  /** 一覧行のグリッド列定義（App のヘッダー行と揃える） */
  listCols: string;
};

export default function DayRow({
  day,
  fac,
  areas,
  periodOrder,
  isOpen,
  onToggle,
  listCols,
}: Props) {
  const isTennis = fac.cat === "tennis";

  // 時間帯内訳がある日に加え、日レベルの状態が分かる日（×/◎など）も
  // 開けるようにして全施設で挙動を統一。内訳が無い日は展開時に
  // 日レベル状態を時間帯グリッドへフォールバック表示する（cell 参照）。
  const openable = areas.some(
    (a) =>
      Object.keys(day.byArea[a]?.periods ?? {}).length > 0 ||
      (day.byArea[a]?.status ?? "unknown") !== "unknown"
  );

  const wd = day.weekday;
  // 日曜・祝日=赤、土曜=青
  const dateColor =
    wd === "日" || day.isHoliday
      ? "text-red-500"
      : wd === "土"
        ? "text-sky-600"
        : "text-slate-700";
  const dn = formatMonthDay(day.date);
  const holidayRate = isWeekendOrHoliday(day);

  // その日の予約可能スロット数（テニスのサマリ表示用）
  const reservableSlots = () => {
    let n = 0;
    for (const a of areas) {
      const ps = day.byArea[a]?.periods ?? {};
      for (const p of periodOrder) if (isReservable(ps[p] ?? "")) n++;
    }
    return n;
  };

  // セル（空き状況＋料金）を描く共通関数
  const cell = (a: string, p: string) => {
    const as = day.byArea[a];
    const ps = as?.periods ?? {};
    const st =
      Object.keys(ps).length > 0 ? ps[p] ?? "unknown" : as?.status ?? "unknown";
    const m = meta(st);
    const yen = priceFor(fac, a, p, holidayRate, day.date);
    return (
      <div className="px-0.5 py-1">
        <Badge variant={m.variant} className="w-full justify-center font-normal">
          {m.sym}
        </Badge>
        {yen != null && (
          <div className="mt-0.5 text-[9px] text-slate-400">
            {yen.toLocaleString()}円
          </div>
        )}
      </div>
    );
  };

  // 時刻ラベル（午前 等 / スロット名の下に時刻）
  const timeLabel = (p: string) => {
    const t = periodTime(fac, p);
    return (
      <div>
        <div>{p}</div>
        {t && <div className="text-[9px] font-normal text-slate-400">{t}</div>}
      </div>
    );
  };

  return (
    <div
      className={cn(
        "border-b border-slate-100 last:border-0",
        isOpen && "my-1 overflow-hidden rounded-md ring-2 ring-emerald-400 last:border-b"
      )}
    >
      <button
        disabled={!openable}
        onClick={() => openable && onToggle(day.date)}
        style={{ gridTemplateColumns: listCols }}
        className={cn(
          "grid w-full items-center gap-px text-center transition-colors",
          openable && "cursor-pointer",
          isOpen
            ? "bg-emerald-500 text-white"
            : openable && "hover:bg-emerald-50 active:bg-emerald-50"
        )}
      >
        <div
          className={cn(
            "flex items-center justify-center gap-0.5 whitespace-nowrap px-0.5 py-1.5 text-[11px] font-bold",
            isOpen ? "text-white" : dateColor
          )}
        >
          <span>
            {dn}
            <span className="ml-0.5 text-[9px] font-normal">
              ({wd}
              {day.isHoliday ? "・祝" : ""})
            </span>
          </span>
          {openable && (
            <ChevronDown
              className={cn(
                "h-3.5 w-3.5 shrink-0 transition-transform",
                isOpen ? "rotate-180 text-white" : "text-emerald-500"
              )}
            />
          )}
        </div>
        {isTennis ? (
          <div className="px-1 py-1.5 text-[11px]">
            {(() => {
              const n = reservableSlots();
              return n > 0 ? (
                <span className={isOpen ? "text-white" : "text-emerald-600"}>
                  空き{n}枠
                </span>
              ) : (
                <span className={isOpen ? "text-white/80" : "text-slate-400"}>
                  空きなし
                </span>
              );
            })()}
          </div>
        ) : (
          areas.map((a) => {
            const m = meta(day.byArea[a]?.status ?? "unknown");
            return (
              <div key={a} className="px-0.5 py-1.5">
                <Badge variant={m.variant} className="w-full justify-center">
                  {m.sym}
                </Badge>
              </div>
            );
          })
        )}
      </button>

      {/* 時間帯グリッド展開 */}
      {isOpen && (
        <div className="bg-white px-1 py-1.5">
          <div className="mb-1 flex items-center gap-1 px-0.5 text-[10px] font-medium text-emerald-600">
            <ChevronDown className="h-3 w-3 rotate-180" />
            時間帯の詳細（タップで閉じる）
          </div>
          {isTennis ? (
            // テニス: 行=コート, 列=スロット（転置）
            <div
              className="grid gap-px text-center text-[10px]"
              style={{
                gridTemplateColumns: `2.5rem repeat(${periodOrder.length}, minmax(0,1fr))`,
              }}
            >
              <div className="px-1 py-0.5 text-slate-400">面</div>
              {periodOrder.map((p) => (
                <div key={p} className="px-0.5 py-0.5 font-medium text-slate-500">
                  {timeLabel(p)}
                </div>
              ))}
              {areas.map((a) => (
                <div key={a} className="contents">
                  <div className="flex items-center justify-center px-1 py-1 font-medium text-slate-500">
                    {shortArea(fac, a)}
                  </div>
                  {periodOrder.map((p) => (
                    <div key={p}>{cell(a, p)}</div>
                  ))}
                </div>
              ))}
            </div>
          ) : (
            // 体育館系: 行=時間帯, 列=エリア
            <div
              className="grid gap-px text-center text-[10px]"
              style={{
                gridTemplateColumns: `5rem repeat(${areas.length}, minmax(0,1fr))`,
              }}
            >
              <div className="px-1 py-0.5 text-slate-400">時間帯</div>
              {areas.map((a) => (
                <div key={a} className="px-1 py-0.5 font-medium text-slate-400">
                  {shortArea(fac, a)}
                </div>
              ))}
              {periodOrder.map((p) => (
                <div key={p} className="contents">
                  <div className="px-1 py-1 text-left font-medium text-slate-500">
                    {timeLabel(p)}
                  </div>
                  {areas.map((a) => (
                    <div key={a}>{cell(a, p)}</div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
