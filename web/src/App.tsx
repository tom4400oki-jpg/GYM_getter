import { useEffect, useMemo, useState } from "react";
import { RefreshCw, ExternalLink, AlertTriangle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import {
  dataSchema,
  isStale,
  isWeekendOrHoliday,
  dayHasReservable,
  dayHasEveningReservable,
  FALLBACK_RESERVE_URL,
  type GymData,
} from "@/lib/schema";
import {
  loadFacilities,
  shortArea,
  type Category,
  type FacilitiesIndex,
  type FacilityMeta,
} from "@/lib/facilities";
import DayRow from "@/components/DayRow";
import AllSummary from "@/components/AllSummary";

import { z } from "zod";

// 「すべて」（カテゴリ横断サマリ）の擬似施設ID
const ALL_ID = "all";
const STORAGE_KEY = "gym-getter:view";

const savedViewSchema = z.object({
  facilityId: z.string().optional(),
  category: z.enum(["hall", "tennis"]).optional(),
  onlyReservable: z.boolean().optional(),
  onlyWeekend: z.boolean().optional(),
  onlyEvening: z.boolean().optional(),
});
type SavedView = z.infer<typeof savedViewSchema>;

function loadSavedView(): SavedView {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = savedViewSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : {};
  } catch {
    return {};
  }
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
  // 施設定義（facilities.json）。読み込めるまでタブ等は描画しない。
  const [index, setIndex] = useState<FacilitiesIndex | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);

  const saved = useMemo(loadSavedView, []);
  const params = useMemo(() => new URLSearchParams(window.location.search), []);

  const [category, setCategory] = useState<Category>(saved.category ?? "hall");
  const [facilityId, setFacilityId] = useState<string>("");
  const [data, setData] = useState<GymData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [onlyReservable, setOnlyReservable] = useState(saved.onlyReservable ?? true);
  const [onlyWeekend, setOnlyWeekend] = useState(
    params.get("weekend") === "1" || (saved.onlyWeekend ?? false)
  );
  const [onlyEvening, setOnlyEvening] = useState(
    params.get("evening") === "1" || (saved.onlyEvening ?? false)
  );
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    loadFacilities()
      .then((idx) => {
        setIndex(idx);
        // 初期表示施設: URL ?f= → 前回の選択（localStorage）→ 体育館の先頭
        const wanted = params.get("f") ?? saved.facilityId;
        const found =
          wanted === ALL_ID ? null : idx.facilities.find((f) => f.id === wanted);
        if (wanted === ALL_ID) {
          setFacilityId(ALL_ID);
        } else if (found) {
          setFacilityId(found.id);
          setCategory(found.cat);
        } else {
          const first =
            idx.facilities.find((f) => f.cat === "hall") ?? idx.facilities[0];
          setFacilityId(first.id);
          setCategory(first.cat);
        }
      })
      .catch((e) => setIndexError(e instanceof Error ? e.message : String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const facilities = index?.facilities ?? [];
  const isAll = facilityId === ALL_ID;
  const fac: FacilityMeta | undefined = facilities.find((f) => f.id === facilityId);

  // 選択状態の保存（再訪時復元）＋ URL 共有パラメータの反映
  useEffect(() => {
    if (!facilityId) return;
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        facilityId,
        category,
        onlyReservable,
        onlyWeekend,
        onlyEvening,
      } satisfies SavedView)
    );
    const q = new URLSearchParams();
    q.set("f", facilityId);
    if (onlyWeekend) q.set("weekend", "1");
    if (onlyEvening) q.set("evening", "1");
    window.history.replaceState(null, "", `?${q.toString()}`);
  }, [facilityId, category, onlyReservable, onlyWeekend, onlyEvening]);

  async function loadData(target: FacilityMeta, signal?: AbortSignal) {
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const res = await fetch(`/${target.file}?t=${Date.now()}`, {
        cache: "no-store",
        signal,
      });
      if (!res.ok) throw new Error(`データ取得に失敗しました (${res.status})`);
      const json = await res.json();
      setData(dataSchema.parse(json));
    } catch (e) {
      if (signal?.aborted) return;
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!signal?.aborted) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    if (!facilityId || isAll) return;
    const target = facilities.find((f) => f.id === facilityId);
    if (!target) return;
    const controller = new AbortController();
    loadData(target, controller.signal);
    setExpanded(new Set());
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facilityId, isAll, facilities]);

  const days = useMemo(() => {
    if (!data) return [];
    let ds = data.days;
    if (onlyReservable) ds = ds.filter(dayHasReservable);
    if (onlyWeekend) ds = ds.filter(isWeekendOrHoliday);
    if (onlyEvening) ds = ds.filter(dayHasEveningReservable);
    return ds;
  }, [data, onlyReservable, onlyWeekend, onlyEvening]);

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

  // カテゴリ切替：「すべて」選択中はそのまま、通常はそのカテゴリの先頭施設へ
  function selectCategory(cat: Category) {
    if (cat === category) return;
    setCategory(cat);
    if (isAll) return;
    const first = facilities.find((f) => f.cat === cat);
    if (first) setFacilityId(first.id);
  }

  const visibleFacilities = facilities.filter((f) => f.cat === category);
  const catLabel = index?.categories.find((c) => c.id === category)?.label ?? "";

  const areas = data?.areas ?? [];
  const periodOrder = data?.periodOrder ?? fac?.periodOrder ?? [];
  const reserveUrl = data?.reserveUrl ?? FALLBACK_RESERVE_URL;
  const stale = data ? isStale(data.updatedAt) : false;

  // テニス系は「コート=行 × スロット=列」の転置レイアウト。コート数が多いため
  // 一覧の列にはせず、日別行は空き枠数のサマリ、展開時に転置グリッドを出す。
  const isTennis = fac?.cat === "tennis";
  const listCols = isTennis
    ? "3.2rem 1fr"
    : `3.2rem repeat(${Math.max(areas.length, 1)}, minmax(0,1fr))`;

  if (indexError) {
    return (
      <div className="mx-auto max-w-xl px-2 py-8">
        <p className="rounded bg-red-50 px-3 py-2 text-xs text-red-600">
          {indexError}（再読み込みしてください）
        </p>
      </div>
    );
  }
  const currentFac = !isAll ? fac : undefined;
  if (!index || (!isAll && !currentFac)) {
    return <p className="py-8 text-center text-xs text-slate-400">読み込み中…</p>;
  }

  return (
    <div className="mx-auto max-w-xl px-2 pb-12">
      {/* ヘッダー */}
      <header className="sticky top-0 z-10 -mx-2 mb-2 border-b border-slate-200 bg-slate-50/95 px-2 py-2 backdrop-blur">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h1 className="text-sm font-bold leading-tight">
              {isAll ? `${catLabel} 全施設まとめ` : data?.facility ?? currentFac?.name ?? ""}
            </h1>
            <p className="text-[11px] text-slate-500">
              予約空き状況
              {!isAll && data && (
                <>
                  ・予約可能{reservableCount}日・更新 {fmtUpdated(data.updatedAt)}
                </>
              )}
            </p>
            {!isAll && stale && data && (
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
            {currentFac && (
              <button
                onClick={() => loadData(currentFac)}
                className="flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-1 text-[11px] text-slate-600 active:bg-slate-100"
              >
                <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
                更新
              </button>
            )}
          </div>
        </div>
        {/* カテゴリ（体育館 / テニス） */}
        <div className="mt-2 flex gap-1" role="tablist" aria-label="カテゴリ">
          {index.categories.map((c) => (
            <button
              key={c.id}
              role="tab"
              aria-selected={category === c.id}
              onClick={() => selectCategory(c.id)}
              className={cn(
                "flex-1 rounded px-2 py-1 text-[12px] font-bold transition-colors",
                category === c.id
                  ? "bg-slate-700 text-white"
                  : "border border-slate-300 bg-white text-slate-500 active:bg-slate-100"
              )}
            >
              {c.label}
            </button>
          ))}
        </div>
        {/* 施設タブ（選択中カテゴリ。「すべて」=横断サマリ） */}
        <div className="mt-1 flex flex-wrap gap-1" role="tablist" aria-label="施設">
          <button
            role="tab"
            aria-selected={isAll}
            onClick={() => setFacilityId(ALL_ID)}
            className={cn(
              "rounded px-2.5 py-1 text-[11px] font-medium transition-colors",
              isAll
                ? "bg-emerald-600 text-white"
                : "border border-slate-300 bg-white text-slate-600 active:bg-slate-100"
            )}
          >
            すべて
          </button>
          {visibleFacilities.map((f) => (
            <button
              key={f.id}
              role="tab"
              aria-selected={facilityId === f.id}
              onClick={() => setFacilityId(f.id)}
              className={cn(
                "rounded px-2.5 py-1 text-[11px] font-medium transition-colors",
                facilityId === f.id
                  ? "bg-emerald-600 text-white"
                  : "border border-slate-300 bg-white text-slate-600 active:bg-slate-100"
              )}
            >
              {f.short}
            </button>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
          {!isAll && (
            <label className="flex items-center gap-1.5 text-[11px] text-slate-600">
              <Switch checked={onlyReservable} onCheckedChange={setOnlyReservable} />
              予約できる日だけ表示
            </label>
          )}
          <label className="flex items-center gap-1.5 text-[11px] text-slate-600">
            <Switch checked={onlyWeekend} onCheckedChange={setOnlyWeekend} />
            土日祝だけ表示
          </label>
          <label className="flex items-center gap-1.5 text-[11px] text-slate-600">
            <Switch checked={onlyEvening} onCheckedChange={setOnlyEvening} />
            18時以降だけ表示
          </label>
        </div>
      </header>

      {/* 凡例 */}
      <div className="mb-2 flex flex-wrap gap-1.5 px-0.5 text-[10px] text-slate-500">
        <Badge variant="available">○ 空き</Badge>
        <Badge variant="partial">△ 一部空き</Badge>
        <Badge variant="open">◎ 一般開放(予約不要)</Badge>
        <Badge variant="occupied">× 予約あり</Badge>
        <Badge variant="muted">保 休館/保守 − 期間外</Badge>
      </div>

      {/* 横断サマリ（すべて） */}
      {isAll && (
        <AllSummary
          facilities={visibleFacilities}
          onlyWeekend={onlyWeekend}
          onlyEvening={onlyEvening}
          onSelect={setFacilityId}
        />
      )}

      {/* 状態表示 */}
      {!isAll && loading && !data && (
        <p className="py-8 text-center text-xs text-slate-400">読み込み中…</p>
      )}
      {!isAll && error && (
        <p className="rounded bg-red-50 px-3 py-2 text-xs text-red-600">{error}</p>
      )}

      {!isAll && currentFac && data && (
        <>
          {/* エリア見出し */}
          <div
            className="grid gap-px rounded-t bg-slate-200 text-center text-[10px] font-medium text-slate-500"
            style={{ gridTemplateColumns: listCols }}
          >
            <div className="bg-slate-100 px-1 py-1">日付</div>
            {isTennis ? (
              <div className="bg-slate-100 px-1 py-1">空き状況（タップで詳細）</div>
            ) : (
              areas.map((a) => (
                <div key={a} className="bg-slate-100 px-1 py-1" title={a}>
                  {shortArea(currentFac, a)}
                </div>
              ))
            )}
          </div>

          {/* 日別行 */}
          <div className="overflow-hidden rounded-b border border-t-0 border-slate-200">
            {days.length === 0 && (
              <p className="py-6 text-center text-xs text-slate-400">
                該当する日がありません
              </p>
            )}
            {days.map((day) => (
              <DayRow
                key={day.date}
                day={day}
                fac={currentFac}
                areas={areas}
                periodOrder={periodOrder}
                isOpen={expanded.has(day.date)}
                onToggle={toggle}
                listCols={listCols}
              />
            ))}
          </div>
        </>
      )}

      <p className="mt-3 px-1 text-[10px] leading-relaxed text-slate-400">
        ふれあいネットの予約空き状況を自動取得。○△が予約で取れる枠です。実際の予約は
        <a
          href={FALLBACK_RESERVE_URL}
          target="_blank"
          rel="noreferrer"
          className="text-sky-500 underline"
        >
          ふれあいネット
        </a>
        で行ってください。
        <br />
        料金は令和7年度・入場料を徴収しない場合の使用料です（土日祝は休日料金。入場料を徴収する場合は別料金）。
      </p>
    </div>
  );
}
