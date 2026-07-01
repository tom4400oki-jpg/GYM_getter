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

// 体育館系（高津・宮前・幸・麻生・多摩・市民プラザ・とどろき）の時間帯時刻
const BASE_TIMES: Record<string, string> = {
  午前: "9:00〜12:00",
  午後1: "12:10〜15:10",
  午後2: "15:20〜18:20",
  夜間: "18:30〜21:30",
};

// テニス系は施設でスロット時間が異なるため施設別に保持（同じ "9:00" でも時間が違う）。
const PERIOD_TIMES_BY_FACILITY: Record<string, Record<string, string>> = {
  // 等々力テニス
  tennis: {
    "9:00": "9:00〜12:00",
    "12:00": "12:00〜14:00",
    "14:00": "14:00〜16:00",
    "16:00": "16:00〜18:30",
    "18:30": "18:30〜20:30",
  },
  // 富士見テニス（6枠）
  fujimi: {
    "9:00": "9:00〜11:00",
    "11:00": "11:00〜13:00",
    "13:00": "13:00〜15:00",
    "15:00": "15:00〜17:00",
    "17:00": "17:00〜18:30",
    "18:30": "18:30〜20:30",
  },
  // 大師テニス（4枠・各2時間）
  daishi: {
    "9:00": "9:00〜11:00",
    "12:00": "12:00〜14:00",
    "14:00": "14:00〜16:00",
    "16:00": "16:00〜18:00",
  },
  // 多摩テニス（5枠）
  tamatennis: {
    "9:00": "9:00〜12:00",
    "12:00": "12:00〜14:00",
    "14:00": "14:00〜16:00",
    "16:00": "16:00〜18:00",
    "18:30": "18:30〜20:30",
  },
};

/** 施設×時間帯ラベルから利用時刻文字列を返す。 */
function periodTime(facilityId: string, p: string): string {
  return PERIOD_TIMES_BY_FACILITY[facilityId]?.[p] ?? BASE_TIMES[p] ?? "";
}

// 施設使用料（令和7年度・入場料を徴収しない場合/貸切, 単位:円）
type PeriodYen = Record<string, number>;
type PriceSet = { weekday: PeriodYen; holiday: PeriodYen };
type FacilityPrice = {
  // エリア名から料金区分キーを決める
  match: (area: string) => string;
  sets: Record<string, PriceSet>;
};

// 高津・幸スポーツセンター共通（大体育室半面/小体育室・入場料を徴収しない場合）。料金は同額。
const SPORTS_CENTER_PRICE: FacilityPrice = {
  match: (a) => (a.includes("小") ? "小" : "半面"),
  sets: {
    半面: {
      weekday: { 午前: 3800, 午後1: 4530, 午後2: 5080, 夜間: 5800 },
      holiday: { 午前: 4560, 午後1: 5430, 午後2: 6090, 夜間: 6960 },
    },
    小: {
      weekday: { 午前: 4230, 午後1: 4960, 午後2: 5560, 夜間: 6290 },
      holiday: { 午前: 5070, 午後1: 5950, 午後2: 6670, 夜間: 7540 },
    },
  },
};

const PRICES: Record<string, FacilityPrice> = {
  // 高津スポーツセンター：半面Ａ/半面Ｂ＝大体育館 半面、小＝小体育館
  takatsu: SPORTS_CENTER_PRICE,
  // 幸スポーツセンター：大体育室半面/小体育室（高津と同額）
  saiwai: SPORTS_CENTER_PRICE,
  // 麻生スポーツセンター：大体育室半面のみ（高津の半面と同額）
  asao: SPORTS_CENTER_PRICE,
  // 多摩スポーツセンター 体育館：大体育室半面（高津と同額）
  tama: SPORTS_CENTER_PRICE,
  // カルッツかわさき：大体育室半面/小体育室（入場料を徴収しない場合・平日土日同額）
  culttz: {
    match: (a) => (a.includes("小") ? "小" : "半面"),
    sets: {
      半面: {
        weekday: { 午前: 4500, 午後1: 5400, 午後2: 6100, 夜間: 6900 },
        holiday: { 午前: 4500, 午後1: 5400, 午後2: 6100, 夜間: 6900 },
      },
      小: {
        weekday: { 午前: 5600, 午後1: 6500, 午後2: 7300, 夜間: 8300 },
        holiday: { 午前: 5600, 午後1: 6500, 午後2: 7300, 夜間: 8300 },
      },
    },
  },
  // 宮前スポーツセンター 大体育室（半面Ａ/Ｂ・入場料を徴収しない場合）
  miyamae: {
    match: () => "半面",
    sets: {
      半面: {
        weekday: { 午前: 3800, 午後1: 4530, 午後2: 5080, 夜間: 5800 },
        holiday: { 午前: 4560, 午後1: 5430, 午後2: 6090, 夜間: 6960 },
      },
    },
  },
  // 川崎市民プラザ 体育館（貸切）：平日は午前/夜間のみ設定
  plaza: {
    match: () => "体育館",
    sets: {
      体育館: {
        weekday: { 午前: 5700, 夜間: 6500 },
        holiday: { 午前: 6800, 午後1: 6200, 午後2: 6200, 夜間: 7800 },
      },
    },
  },
  // 多摩テニス：個人利用。9:00枠(3時間)2,700円、12/14/16枠(2時間)各1,800円、18:30枠(照明込)3,720円。
  tamatennis: {
    match: () => "コート",
    sets: {
      コート: {
        weekday: { "9:00": 2700, "12:00": 1800, "14:00": 1800, "16:00": 1800, "18:30": 3720 },
        holiday: { "9:00": 2700, "12:00": 1800, "14:00": 1800, "16:00": 1800, "18:30": 3720 },
      },
    },
  },
  // 大師テニスコート：市区内・2時間1,520円のフラット（全スロット同額）
  daishi: {
    match: () => "コート",
    sets: {
      コート: {
        weekday: { "9:00": 1520, "12:00": 1520, "14:00": 1520, "16:00": 1520 },
        holiday: { "9:00": 1520, "12:00": 1520, "14:00": 1520, "16:00": 1520 },
      },
    },
  },
  // とどろきアリーナ：アマチュアスポーツ・入場料なし（サブは全面料金）
  todoroki: {
    match: (a) =>
      a.includes("メイン") ? "メイン" : a.includes("サブ") ? "サブ" : "体育室",
    sets: {
      メイン: {
        weekday: { 午前: 20160, 午後1: 20160, 午後2: 20160, 夜間: 20160 },
        holiday: { 午前: 24190, 午後1: 24190, 午後2: 24190, 夜間: 24190 },
      },
      サブ: {
        weekday: { 午前: 13440, 午後1: 13440, 午後2: 13440, 夜間: 13440 },
        holiday: { 午前: 16120, 午後1: 16120, 午後2: 16120, 夜間: 16120 },
      },
      体育室: {
        weekday: { 午前: 3360, 午後1: 3360, 午後2: 3360, 夜間: 3360 },
        holiday: { 午前: 4030, 午後1: 4030, 午後2: 4030, 夜間: 4030 },
      },
    },
  },
  // 等々力テニスコート：専用使用料（1面1枠）。平日/土日祝とも同額、スロット長で変動。
  tennis: {
    match: () => "コート",
    sets: {
      コート: {
        weekday: { "9:00": 2280, "12:00": 1520, "14:00": 1520, "16:00": 1900, "18:30": 1520 },
        holiday: { "9:00": 2280, "12:00": 1520, "14:00": 1520, "16:00": 1900, "18:30": 1520 },
      },
    },
  },
};

// 富士見テニスコート：季節別料金（月で区切り。1面1回・スロット別）。
// 9:00/11:00/13:00=2,200・18:30=3,400 は通年。15:00は11〜12月のみ2,800、17:00は3〜9月のみ1,650。
function fujimiPrice(period: string, date: string): number | null {
  const base: Record<string, number> = {
    "9:00": 2200,
    "11:00": 2200,
    "13:00": 2200,
    "15:00": 2200,
    "17:00": 2550,
    "18:30": 3400,
  };
  const m = Number(date.slice(5, 7));
  if (m >= 3 && m <= 9) base["17:00"] = 1650; // 3/1〜9/30
  if (m === 11 || m === 12) base["15:00"] = 2800; // 11/1〜12/31
  return base[period] ?? null;
}

/** 施設×エリア×時間帯×曜日×日付から使用料（円）を返す。土日は休日料金（祝日は別途確認）。 */
function priceFor(
  facilityId: string,
  area: string,
  period: string,
  weekday: string,
  date: string
): number | null {
  if (facilityId === "fujimi") return fujimiPrice(period, date);
  const f = PRICES[facilityId];
  if (!f) return null;
  const set = f.sets[f.match(area)];
  if (!set) return null;
  const isHoliday = weekday === "土" || weekday === "日";
  return (isHoliday ? set.holiday : set.weekday)[period] ?? null;
}

// 施設タブ定義。cat=カテゴリ（体育館/テニス）、file は web/public 配下の data ファイル名。
const FACILITIES = [
  { id: "takatsu", name: "高津スポーツセンター", short: "高津", file: "data.json", cat: "hall" },
  { id: "miyamae", name: "宮前スポーツセンター", short: "宮前", file: "data-miyamae.json", cat: "hall" },
  { id: "saiwai", name: "幸スポーツセンター", short: "幸", file: "data-saiwai.json", cat: "hall" },
  { id: "asao", name: "麻生スポーツセンター", short: "麻生", file: "data-asao.json", cat: "hall" },
  { id: "tama", name: "多摩スポーツセンター（体育館）", short: "多摩", file: "data-tama.json", cat: "hall" },
  { id: "culttz", name: "カルッツかわさき", short: "カルッツ", file: "data-culttz.json", cat: "hall" },
  { id: "plaza", name: "川崎市民プラザ", short: "プラザ", file: "data-plaza.json", cat: "hall" },
  { id: "todoroki", name: "とどろきアリーナ", short: "とどろき", file: "data-todoroki.json", cat: "hall" },
  { id: "tennis", name: "等々力テニスコート", short: "等々力", file: "data-tennis.json", cat: "tennis" },
  { id: "fujimi", name: "富士見テニスコート", short: "富士見", file: "data-fujimi.json", cat: "tennis" },
  { id: "daishi", name: "大師テニスコート", short: "大師", file: "data-daishi.json", cat: "tennis" },
  { id: "tamatennis", name: "多摩スポーツセンター（テニス）", short: "多摩", file: "data-tama-tennis.json", cat: "tennis" },
] as const;
type FacilityId = (typeof FACILITIES)[number]["id"];
type Category = (typeof FACILITIES)[number]["cat"];

const CATEGORIES: { id: Category; label: string }[] = [
  { id: "hall", label: "体育館" },
  { id: "tennis", label: "テニス" },
];

function shortArea(name: string): string {
  return name
    .replace("大体育室半面", "半面")
    .replace("大体育室", "大")
    .replace("小体育室", "小")
    .replace("メインアリーナ", "メイン")
    .replace("サブアリーナ", "サブ")
    .replace("体育室", "体育")
    .replace("等々力テニス場", "")
    .replace("富士見テニス場", "")
    .replace("大師テニス場", "")
    .replace("テニスコート", "")
    .replace("テニス場", "");
}

function dayHasReservable(day: DayData): boolean {
  return Object.values(day.byArea).some((a) => isReservable(a.status));
}

// 18時以降に始まる枠か（体育館『夜間』18:30〜／テニスは時刻表記'18:30'等）
function isEveningPeriod(label: string): boolean {
  if (label === "夜間") return true;
  const m = /^(\d{1,2}):/.exec(label);
  return m ? Number(m[1]) >= 18 : false;
}

function dayHasEveningReservable(day: DayData): boolean {
  return Object.values(day.byArea).some((a) =>
    Object.entries(a.periods ?? {}).some(
      ([p, st]) => isEveningPeriod(p) && isReservable(st)
    )
  );
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
  const [category, setCategory] = useState<Category>("hall");
  const [facilityId, setFacilityId] = useState<FacilityId>(FACILITIES[0].id);
  const [data, setData] = useState<GymData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [onlyReservable, setOnlyReservable] = useState(true);
  const [onlyWeekend, setOnlyWeekend] = useState(false);
  const [onlyEvening, setOnlyEvening] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  async function load(fid: FacilityId = facilityId) {
    const facility = FACILITIES.find((f) => f.id === fid) ?? FACILITIES[0];
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const res = await fetch(`/${facility.file}?t=${Date.now()}`, { cache: "no-store" });
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
    load(facilityId);
    setExpanded(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [facilityId]);

  const days = useMemo(() => {
    if (!data) return [];
    let ds = data.days;
    if (onlyReservable) ds = ds.filter(dayHasReservable);
    if (onlyWeekend) ds = ds.filter((d) => d.weekday === "土" || d.weekday === "日");
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

  // カテゴリ切替：そのカテゴリの先頭施設へ移動
  function selectCategory(cat: Category) {
    if (cat === category) return;
    setCategory(cat);
    const first = FACILITIES.find((f) => f.cat === cat);
    if (first) setFacilityId(first.id);
  }

  const visibleFacilities = FACILITIES.filter((f) => f.cat === category);

  const areas = data?.areas ?? [];
  const periodOrder = data?.periodOrder ?? ["午前", "午後1", "午後2", "夜間"];
  const reserveUrl = data?.reserveUrl ?? FALLBACK_RESERVE_URL;
  const stale = data ? isStale(data.updatedAt) : false;

  // テニス系は「コート=行 × スロット=列」の転置レイアウト。コート数が多いため
  // 一覧の列にはせず、日別行は空き枠数のサマリ、展開時に転置グリッドを出す。
  const isTennis = ["tennis", "fujimi", "daishi", "tamatennis"].includes(facilityId);
  const listCols = isTennis
    ? "3.2rem 1fr"
    : `3.2rem repeat(${Math.max(areas.length, 1)}, minmax(0,1fr))`;

  // その日の予約可能スロット数（テニスのサマリ表示用）
  function reservableSlots(day: DayData): number {
    let n = 0;
    for (const a of areas) {
      const ps = day.byArea[a]?.periods ?? {};
      for (const p of periodOrder) if (isReservable(ps[p] ?? "")) n++;
    }
    return n;
  }

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
              onClick={() => load()}
              className="flex items-center gap-1 rounded border border-slate-300 bg-white px-2 py-1 text-[11px] text-slate-600 active:bg-slate-100"
            >
              <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
              更新
            </button>
          </div>
        </div>
        {/* カテゴリ（体育館 / テニス） */}
        <div className="mt-2 flex gap-1" role="tablist" aria-label="カテゴリ">
          {CATEGORIES.map((c) => (
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
        {/* 施設タブ（選択中カテゴリ） */}
        <div className="mt-1 flex flex-wrap gap-1" role="tablist" aria-label="施設">
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
          <label className="flex items-center gap-1.5 text-[11px] text-slate-600">
            <Switch checked={onlyReservable} onCheckedChange={setOnlyReservable} />
            予約できる日だけ表示
          </label>
          <label className="flex items-center gap-1.5 text-[11px] text-slate-600">
            <Switch checked={onlyWeekend} onCheckedChange={setOnlyWeekend} />
            土日だけ表示
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
                  {shortArea(a)}
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
            {days.map((day) => {
              // 時間帯内訳がある日に加え、日レベルの状態が分かる日（×/◎など）も
              // 開けるようにして全施設で挙動を統一。内訳が無い日は展開時に
              // 日レベル状態を時間帯グリッドへフォールバック表示する（cell 参照）。
              const openable = areas.some(
                (a) =>
                  Object.keys(day.byArea[a]?.periods ?? {}).length > 0 ||
                  (day.byArea[a]?.status ?? "unknown") !== "unknown"
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
                    disabled={!openable}
                    onClick={() => openable && toggle(day.date)}
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
                        "flex items-center justify-center gap-0.5 px-1 py-1.5 text-[11px] font-bold",
                        isOpen ? "text-white" : dateColor
                      )}
                    >
                      <span>
                        {dn}
                        <span className="ml-0.5 text-[9px] font-normal">({wd})</span>
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
                          const n = reservableSlots(day);
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

                  {/* 時間帯グリッド展開（施設を列に統一。上の列と揃う） */}
                  {isOpen && (
                    <div className="bg-white px-1 py-1.5">
                      <div className="mb-1 flex items-center gap-1 px-0.5 text-[10px] font-medium text-emerald-600">
                        <ChevronDown className="h-3 w-3 rotate-180" />
                        時間帯の詳細（タップで閉じる）
                      </div>
                      {(() => {
                        // セル（空き状況＋料金）を描く共通関数
                        const cell = (a: string, p: string) => {
                          const as = day.byArea[a];
                          const ps = as?.periods ?? {};
                          const st =
                            Object.keys(ps).length > 0
                              ? ps[p] ?? "unknown"
                              : as?.status ?? "unknown";
                          const m = meta(st);
                          const yen = priceFor(facilityId, a, p, day.weekday, day.date);
                          return (
                            <div className="px-0.5 py-1">
                              <Badge
                                variant={m.variant}
                                className="w-full justify-center font-normal"
                              >
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
                          const t = periodTime(facilityId, p);
                          return (
                            <div>
                              <div>{p}</div>
                              {t && (
                                <div className="text-[9px] font-normal text-slate-400">
                                  {t}
                                </div>
                              )}
                            </div>
                          );
                        };

                        // テニス: 行=コート, 列=スロット（転置）
                        if (isTennis) {
                          return (
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
                                    {shortArea(a)}
                                  </div>
                                  {periodOrder.map((p) => (
                                    <div key={p}>{cell(a, p)}</div>
                                  ))}
                                </div>
                              ))}
                            </div>
                          );
                        }

                        // 体育館系: 行=時間帯, 列=エリア
                        return (
                          <div
                            className="grid gap-px text-center text-[10px]"
                            style={{
                              gridTemplateColumns: `5rem repeat(${areas.length}, minmax(0,1fr))`,
                            }}
                          >
                            <div className="px-1 py-0.5 text-slate-400">時間帯</div>
                            {areas.map((a) => (
                              <div key={a} className="px-1 py-0.5 font-medium text-slate-400">
                                {shortArea(a)}
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
                        );
                      })()}
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
            <br />
            料金は令和7年度・入場料を徴収しない場合の使用料です（祝日は土日料金。入場料を徴収する場合は別料金）。
          </p>
        </>
      )}
    </div>
  );
}
