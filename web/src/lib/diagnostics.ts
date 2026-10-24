import { z } from "zod";
import { dataSchema, dayHasReservable, isStale } from "@/lib/schema";
import type { FacilitiesIndex } from "@/lib/facilities";

const GITHUB_REPO = "tom4400oki-jpg/GYM_getter";

// ── scrape-log.json のスキーマ ──────────────────────
const lineQuotaSchema = z.object({
  usage: z.number().nullish(),
  limit: z.number().nullish(),
  type: z.string().nullish(),
});

const facilityDiagSchema = z.object({
  id: z.string(),
  name: z.string(),
  status: z.string(),
  days: z.number().default(0),
  reservableDays: z.number().default(0),
  okAreas: z.array(z.string()).default([]),
  failedAreas: z.array(z.string()).default([]),
  unknownRatio: z.number().default(0),
  unknownSamples: z.array(z.string()).default([]),
  notifyAdded: z.number().default(0),
  notifyRemoved: z.number().default(0),
});

const groupLogSchema = z.object({
  group: z.string(),
  startedAt: z.string(),
  finishedAt: z.string(),
  durationSec: z.number(),
  status: z.string(),
  maintenance: z.boolean().default(false),
  runId: z.string().nullish(),
  lineQuota: lineQuotaSchema.nullish(),
  facilities: z.array(facilityDiagSchema).default([]),
  warnings: z.array(z.string()).default([]),
  flowLogTail: z.array(z.string()).default([]),
});

export const scrapeLogSchema = z.object({
  updatedAt: z.string().nullish(),
  groups: z.record(z.string(), groupLogSchema).default({}),
});

export type ScrapeLog = z.infer<typeof scrapeLogSchema>;

// ── GitHub Actions API のスキーマ ───────────────────
const ghRunSchema = z.object({
  id: z.number(),
  name: z.string().nullish(),
  status: z.string().nullish(),
  conclusion: z.string().nullish(),
  created_at: z.string(),
  updated_at: z.string().nullish(),
  html_url: z.string().nullish(),
});

const ghRunsResponseSchema = z.object({
  workflow_runs: z.array(ghRunSchema).default([]),
});

const ghStepSchema = z.object({
  name: z.string(),
  status: z.string().nullish(),
  conclusion: z.string().nullish(),
  number: z.number(),
});

const ghJobSchema = z.object({
  id: z.number(),
  name: z.string(),
  status: z.string().nullish(),
  conclusion: z.string().nullish(),
  steps: z.array(ghStepSchema).default([]),
});

const ghJobsResponseSchema = z.object({
  jobs: z.array(ghJobSchema).default([]),
});

const ghAnnotationSchema = z.object({
  annotation_level: z.string().nullish(),
  title: z.string().nullish(),
  message: z.string(),
});

const ghAnnotationsListSchema = z.array(ghAnnotationSchema);

function fmtJst(iso: string | null | undefined): string {
  if (!iso) return "不明";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function fetchScrapeLog(signal?: AbortSignal): Promise<{
  log: ScrapeLog | null;
  error: string | null;
}> {
  try {
    const res = await fetch(`/scrape-log.json?t=${Date.now()}`, {
      cache: "no-store",
      signal,
    });
    if (!res.ok) {
      return { log: null, error: `HTTP ${res.status}` };
    }
    const json: unknown = await res.json();
    const parsed = scrapeLogSchema.safeParse(json);
    if (!parsed.success) {
      return { log: null, error: `スキーマ不一致: ${parsed.error.message}` };
    }
    return { log: parsed.data, error: null };
  } catch (e) {
    return {
      log: null,
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

type GhRunSummary = {
  id: number;
  name: string;
  status: string;
  conclusion: string;
  createdAt: string;
  failedSteps: string[];
  annotations: string[];
};

async function fetchGitHubRuns(signal?: AbortSignal): Promise<{
  runs: GhRunSummary[];
  error: string | null;
}> {
  try {
    const res = await fetch(
      `https://api.github.com/repos/${GITHUB_REPO}/actions/runs?per_page=6`,
      {
        headers: { Accept: "application/vnd.github+json" },
        cache: "no-store",
        signal,
      }
    );
    if (!res.ok) {
      return { runs: [], error: `GitHub API HTTP ${res.status}` };
    }
    const parsed = ghRunsResponseSchema.safeParse(await res.json());
    if (!parsed.success) {
      return { runs: [], error: "GitHub API レスポンス解析失敗" };
    }

    const rawRuns = parsed.data.workflow_runs;
    // 直近で失敗した run（最大2件）のみジョブ詳細と注釈を追加取得する
    let failChecked = 0;
    const summaries: GhRunSummary[] = [];

    for (const r of rawRuns) {
      const failedSteps: string[] = [];
      const annotations: string[] = [];

      if (r.conclusion === "failure" && failChecked < 2) {
        failChecked++;
        try {
          const jRes = await fetch(
            `https://api.github.com/repos/${GITHUB_REPO}/actions/runs/${r.id}/jobs`,
            {
              headers: { Accept: "application/vnd.github+json" },
              cache: "no-store",
              signal,
            }
          );
          if (jRes.ok) {
            const jParsed = ghJobsResponseSchema.safeParse(await jRes.json());
            if (jParsed.success) {
              for (const job of jParsed.data.jobs) {
                for (const s of job.steps) {
                  if (s.conclusion === "failure") {
                    failedSteps.push(s.name);
                  }
                }
                const aRes = await fetch(
                  `https://api.github.com/repos/${GITHUB_REPO}/check-runs/${job.id}/annotations`,
                  {
                    headers: { Accept: "application/vnd.github+json" },
                    cache: "no-store",
                    signal,
                  }
                );
                if (aRes.ok) {
                  const aParsed = ghAnnotationsListSchema.safeParse(await aRes.json());
                  if (aParsed.success) {
                    for (const a of aParsed.data) {
                      if (a.annotation_level === "failure" || a.title === "GYM Getter") {
                        const firstLine = a.message.split("\n")[0] ?? a.message;
                        annotations.push(
                          `[${a.annotation_level ?? "info"}] ${firstLine.slice(0, 160)}`
                        );
                      }
                    }
                  }
                }
              }
            }
          }
        } catch {
          // 個別ジョブ詳細の取得失敗は無視して一覧は返す
        }
      }

      summaries.push({
        id: r.id,
        name: r.name ?? "workflow",
        status: r.status ?? "unknown",
        conclusion: r.conclusion ?? "running",
        createdAt: fmtJst(r.created_at),
        failedSteps,
        annotations: [...new Set(annotations)],
      });
    }

    return { runs: summaries, error: null };
  } catch (e) {
    return {
      runs: [],
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

type FacilityHealth = {
  shortName: string;
  file: string;
  updatedAt: string;
  stale: boolean;
  daysCount: number;
  reservableCount: number;
  error: string | null;
};

async function checkAllFacilitiesHealth(
  index: FacilitiesIndex | null,
  signal?: AbortSignal
): Promise<FacilityHealth[]> {
  if (!index) return [];
  return Promise.all(
    index.facilities.map(async (f) => {
      try {
        const res = await fetch(`/${f.file}?t=${Date.now()}`, {
          cache: "no-store",
          signal,
        });
        if (!res.ok) {
          return {
            shortName: f.short,
            file: f.file,
            updatedAt: "-",
            stale: true,
            daysCount: 0,
            reservableCount: 0,
            error: `HTTP ${res.status}`,
          };
        }
        const parsed = dataSchema.safeParse(await res.json());
        if (!parsed.success) {
          return {
            shortName: f.short,
            file: f.file,
            updatedAt: "-",
            stale: true,
            daysCount: 0,
            reservableCount: 0,
            error: "スキーマエラー",
          };
        }
        const d = parsed.data;
        return {
          shortName: f.short,
          file: f.file,
          updatedAt: fmtJst(d.updatedAt),
          stale: isStale(d.updatedAt),
          daysCount: d.days.length,
          reservableCount: d.days.filter(dayHasReservable).length,
          error: null,
        };
      } catch (e) {
        return {
          shortName: f.short,
          file: f.file,
          updatedAt: "-",
          stale: true,
          daysCount: 0,
          reservableCount: 0,
          error: e instanceof Error ? e.message : String(e),
        };
      }
    })
  );
}

export type DiagnosticContext = {
  index: FacilitiesIndex | null;
  facilityId: string;
  category: string;
  uiError: string | null;
};

export async function buildDiagnosticReport(
  ctx: DiagnosticContext,
  signal?: AbortSignal
): Promise<string> {
  const nowStr = fmtJst(new Date().toISOString());
  const [scrapeRes, ghRes, healthList] = await Promise.all([
    fetchScrapeLog(signal),
    fetchGitHubRuns(signal),
    checkAllFacilitiesHealth(ctx.index, signal),
  ]);

  const lines: string[] = [];
  lines.push("=== GYM Getter 診断ログ ===");
  lines.push(`生成時刻(JST): ${nowStr}`);
  lines.push(
    `画面状態: category=${ctx.category}, facility=${ctx.facilityId}, uiError=${ctx.uiError ?? "なし"}`
  );
  lines.push(`URL: ${window.location.href}`);
  lines.push("");

  // [1] GitHub Actions の直近実行状況
  lines.push("[1] GitHub Actions 直近の実行状況");
  if (ghRes.error) {
    lines.push(`  (取得失敗: ${ghRes.error})`);
  } else if (ghRes.runs.length === 0) {
    lines.push("  (実行履歴なし)");
  } else {
    for (const r of ghRes.runs) {
      const mark =
        r.conclusion === "success"
          ? "OK"
          : r.conclusion === "failure"
            ? "NG"
            : r.status;
      lines.push(`  - [${mark}] #${r.id} ${r.name} (${r.createdAt} JST)`);
      if (r.failedSteps.length > 0) {
        lines.push(`      失敗ステップ: ${r.failedSteps.join(", ")}`);
      }
      for (const ann of r.annotations) {
        lines.push(`      注釈: ${ann}`);
      }
    }
  }
  lines.push("");

  // [2] スクレイプ稼働ログ (scrape-log.json)
  lines.push("[2] スクレイプ稼働ログ (scrape-log.json)");
  if (scrapeRes.error || !scrapeRes.log) {
    lines.push(`  (未生成または取得失敗: ${scrapeRes.error ?? "不明"})`);
  } else {
    const groups = Object.values(scrapeRes.log.groups);
    if (groups.length === 0) {
      lines.push("  (ログエントリ待機中：次回スクレイプ実行時に記録されます)");
    }
    for (const g of groups) {
      const q = g.lineQuota;
      const quotaStr = q
        ? ` / LINE送信数: ${q.usage ?? 0}/${q.limit ?? "unlimited"}`
        : "";
      lines.push(
        `  ■ group=${g.group}: status=${g.status} / ${fmtJst(g.startedAt)} (${g.durationSec}s)${quotaStr}`
      );
      if (g.warnings.length > 0) {
        for (const w of g.warnings) {
          lines.push(`    ⚠ 警告: ${w}`);
        }
      }
      for (const f of g.facilities) {
        const failStr =
          f.failedAreas.length > 0 ? ` [失敗:${f.failedAreas.join(",")}]` : "";
        const unkStr =
          f.unknownRatio > 0 ? ` [unk:${Math.round(f.unknownRatio * 100)}%]` : "";
        lines.push(
          `    - ${f.name}: ${f.status} (${f.days}日, 予約可${f.reservableDays}日)${failStr}${unkStr}`
        );
      }
      if (g.flowLogTail.length > 0) {
        lines.push("    [スクレイパーログ末尾]");
        for (const fl of g.flowLogTail.slice(-6)) {
          lines.push(`      ${fl}`);
        }
      }
    }
  }
  lines.push("");

  // [3] 公開中データ鮮度
  lines.push(
    `[3] 公開中データ鮮度 (facilities.json: ${fmtJst(ctx.index?.updatedAt)})`
  );
  for (const h of healthList) {
    if (h.error) {
      lines.push(`  - ${h.shortName} (${h.file}): [エラー: ${h.error}]`);
    } else {
      const state = h.stale ? "⚠古い(>3h)" : "正常";
      lines.push(
        `  - ${h.shortName} (${h.file}): ${state} / 更新 ${h.updatedAt} (${h.daysCount}日, 予約可${h.reservableCount}日)`
      );
    }
  }

  return lines.join("\n");
}
