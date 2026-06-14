import { useEffect, useMemo, useState } from "react";
import { ChevronDown, RefreshCw, ExternalLink, AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import {
  dataSchema,
  isReservable,
  isStale,
  FALLBACK_RESERVE_URL,
  type GymData,
  type DayData,
} from "@/lib/schema";

type StatusMeta = {
  sym: string;
  label: string;
  variant: "available" | "partial" | "open" | "occupied" | "muted";
};

const STATUS_META: Record<string, StatusMeta> = {
  available: { sym: "○", label: "空き", variant: "available" },
  partial: { sym: "△", label: "一部空き", variant: "partial" },
  open: { sym: "◎", label: "一般開放", variant: "open" },
  occupied: { sym: "×", label: "予約あり", variant: "occupied" },
  closed: { sym: "保", label: "休館・保守", variant: "muted" },
  rain: { sym: "雨", label: "雨天", variant: "muted" },
  cancelling: { sym: "取", label: "取消中", variant: "muted" },
  planned: { sym: "予", label: "開放予定", variant: "muted" },
  out_of_period: { sym: "−", label: "受付期間外", variant: "muted" },
  unknown: { sym: "−", label: "−", variant: "muted" },
};

function meta(status: string): StatusMeta {
  return STATUS_META[status] ?? STATUS_META.unknown;
}

function shortArea(name: string): string {
  return name
    .replace("大体育室半面", "半面")
    .replace("大体育室", "大")
    .replace("小体育室", "小");
}

function dayHasReservable(day: DayData): boolean {
  return Object.values(day.byArea).some((a) => isReservable(a.status));
}

function fmtUpdated(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function App() {
  const [data, setData] = useState<GymData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [onlyReservable, setOnlyReservable] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/data.json?t=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`データ取得に失敗しました (${res.status})`);
      const json = await res.json();
      setData(dataSchema.parse(json));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const days = useMemo(() => {
    if (!data) return [];
    return onlyReservable ? data.days.filter(dayHasReservable) : data.days;
  }, [data, onlyReservable]);

  const reservableCount = useMemo(
    () => (data ? data.days.filter(dayHasReservable).length : 0),
    [data]
  );

  function toggle(date: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(date) ? next.delete(date) : next.add(date);
      return next;
    });
  }

  const areas = data?.areas ?? [];
  const periodOrder = data?.periodOrder ?? ["午前", "午後1", "午後2", "夜間"];
  const reserveUrl = data?.reserveUrl ?? FALLBACK_RESERVE_URL;
  const stale = data ? isStale(data.updatedAt) : false;

  return (
    <div className="mx-auto max-w-xl px-2 pb-12">
      {/* ヘッダー */}
      <header className="sticky top-0 z-10 -mx-2 mb-2 border-b border-slate-200 bg-slate-50/95 px-2 py-2 backdrop-blur">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h1 className="text-sm font-bold leading-tight">
              {data?.facility ?? "高津スポーツセンター"}
            </h1>
            <p className="text-[11px] text-slate-500">
              予約空き状況
              {data && (
                <>
                  ・予約可能{reservableCount}日・更新 {fmtUpdated(data.updatedAt)}
                </>
              )}
            </p>
            {stale && data && (
              <p className="mt-0.5 flex items-center gap-1 text-[10px] font-medium text-amber-600">
                <AlertTriangle className="h-3 w-3" />
                データが古い可能性があります（自動更新が滞っているかもしれません）
              </p>
            )}
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <a
              href={reserveUrl}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 rounded bg-emerald-600 px-2 py-1 text-[11px] font-medium text-white active:bg-emerald-700"
            >
              <ExternalLink className="h-3 w-3" />
              予約する
            </a>
            <button
              onClick={load}
              className="flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-1 text-[11px] text-slate-600 active:bg-slate-100"
            >
              <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
              更新
            </button>
          </div>
        </div>
        <label className="mt-2 flex items-center gap-1.5 text-[11px] text-slate-600">
          <Switch checked={onlyReservable} onCheckedChange={setOnlyReservable} />
          予約できる日だけ表示
        </label>
      </header>

      {/* 凡例 */}
      <div className="mb-2 flex flex-wrap gap-1.5 px-0.5 text-[10px] text-slate-500">
        <Badge variant="available">○ 空き</Badge>
        <Badge variant="partial">△ 一部空き</Badge>
        <Badge variant="open">◎ 一般開放(予約不要)</Badge>
        <Badge variant="occupied">× 予約あり</Badge>
        <Badge variant="muted">保 休館/保守 − 期間外</Badge>
      </div>

      {/* 状態表示 */}
      {loading && !data && (
        <p className="py-8 text-center text-xs text-slate-400">読み込み中…</p>
      )}
      {error && (
        <p className="rounded bg-red-50 px-3 py-2 text-xs text-red-600">{error}</p>
      )}

      {data && (
        <>
          {/* エリア見出し */}
          <div className="grid grid-cols-[3.2rem_repeat(3,1fr)] gap-px rounded-t bg-slate-200 text-center text-[10px] font-medium text-slate-500">
            <div className="bg-slate-100 px-1 py-1">日付</div>
            {areas.map((a) => (
              <div key={a} className="bg-slate-100 px-1 py-1" title={a}>
                {shortArea(a)}
              </div>
            ))}
          </div>

          {/* 日別行 */}
          <div className="overflow-hidden rounded-b border border-t-0 border-slate-200">
            {days.length === 0 && (
              <p className="py-6 text-center text-xs text-slate-400">
                該当する日がありません
              </p>
            )}
            {days.map((day) => {
              const hasPeriods = areas.some(
                (a) => Object.keys(day.byArea[a]?.periods ?? {}).length > 0
              );
              const isOpen = expanded.has(day.date);
              const wd = day.weekday;
              const dateColor =
                wd === "日"
                  ? "text-red-500"
                  : wd === "土"
                    ? "text-sky-600"
                    : "text-slate-700";
              const dn = day.date.slice(5).replace("-", "/");
              return (
                <div
                  key={day.date}
                  className={cn(
                    "border-b border-slate-100 last:border-0",
                    isOpen &&
                      "my-1 overflow-hidden rounded-md ring-2 ring-emerald-400 last:border-b"
                  )}
                >
                  <button
                    disabled={!hasPeriods}
                    onClick={() => hasPeriods && toggle(day.date)}
                    className={cn(
                      "grid w-full grid-cols-[3.2rem_repeat(3,1fr)] items-center gap-px text-center transition-colors",
                      hasPeriods && "cursor-pointer",
                      isOpen
                        ? "bg-emerald-500 text-white"
                        : hasPeriods && "hover:bg-emerald-50 active:bg-emerald-50"
                    )}
                  >
                    <div
                      className={cn(
                        "flex items-center justify-center gap-0.5 px-1 py-1.5 text-[11px] font-bold",
                        isOpen ? "text-white" : dateColor
                      )}
                    >
                      <span>
                        {dn}
                        <span className="ml-0.5 text-[9px] font-normal">({wd})</span>
                      </span>
                      {hasPeriods && (
                        <ChevronDown
                          className={cn(
                            "h-3.5 w-3.5 shrink-0 transition-transform",
                            isOpen ? "rotate-180 text-white" : "text-emerald-500"
                          )}
                        />
                      )}
                    </div>
                    {areas.map((a) => {
                      const m = meta(day.byArea[a]?.status ?? "unknown");
                      return (
                        <div key={a} className="px-0.5 py-1.5">
                          <Badge variant={m.variant} className="w-full justify-center">
                            {m.sym}
                          </Badge>
                        </div>
                      );
                    })}
                  </button>

                  {/* 時間帯グリッド展開（施設を列に統一。上の列と揃う） */}
                  {isOpen && (
                    <div className="bg-white px-1 py-1.5">
                      <div className="mb-1 flex items-center gap-1 px-0.5 text-[10px] font-medium text-emerald-600">
                        <ChevronDown className="h-3 w-3 rotate-180" />
                        時間帯の詳細（タップで閉じる）
                      </div>
                      <div className="grid grid-cols-[3.2rem_repeat(3,1fr)] gap-px text-center text-[10px]">
                        {/* 施設ヘッダー（再掲・控えめ） */}
                        <div className="px-1 py-0.5 text-slate-400">時間帯</div>
                        {areas.map((a) => (
                          <div key={a} className="px-1 py-0.5 font-medium text-slate-400">
                            {shortArea(a)}
                          </div>
                        ))}
                        {/* 時間帯ごとの行 */}
                        {periodOrder.map((p) => (
                          <div key={p} className="contents">
                            <div className="px-1 py-1 text-left font-medium text-slate-500">
                              {p}
                            </div>
                            {areas.map((a) => {
                              const as = day.byArea[a];
                              const ps = as?.periods ?? {};
                              // 時間帯データがあれば実データ、なければ日単位の状態を表示
                              const st =
                                Object.keys(ps).length > 0
                                  ? ps[p] ?? "unknown"
                                  : as?.status ?? "unknown";
                              const m = meta(st);
                              return (
                                <div key={a} className="px-0.5 py-1">
                                  <Badge
                                    variant={m.variant}
                                    className="w-full justify-center font-normal"
                                  >
                                    {m.sym}
                                  </Badge>
                                </div>
                              );
                            })}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <p className="mt-3 px-1 text-[10px] leading-relaxed text-slate-400">
            ふれあいネットの予約空き状況を自動取得。○△が予約で取れる枠です。実際の予約は
            <a
              href="https://www.fureai-net.city.kawasaki.jp/web/"
              target="_blank"
              rel="noreferrer"
              className="text-sky-500 underline"
            >
              ふれあいネット
            </a>
            で行ってください。
          </p>
        </>
      )}
    </div>
  );
}
