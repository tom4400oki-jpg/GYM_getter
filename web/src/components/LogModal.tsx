import { useEffect, useRef, useState } from "react";
import { Copy, Check, RefreshCw, X, Terminal } from "lucide-react";
import { cn } from "@/lib/utils";
import { buildDiagnosticReport, type DiagnosticContext } from "@/lib/diagnostics";

type Props = {
  open: boolean;
  onClose: () => void;
  context: DiagnosticContext;
};

export default function LogModal({ open, onClose, context }: Props) {
  const [report, setReport] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);
  const [copied, setCopied] = useState<boolean>(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  async function fetchReport(signal?: AbortSignal) {
    setLoading(true);
    setCopied(false);
    try {
      const text = await buildDiagnosticReport(context, signal);
      if (!signal?.aborted) {
        setReport(text);
      }
    } catch (e) {
      if (!signal?.aborted) {
        setReport(
          `=== GYM Getter 診断ログ取得エラー ===\n${e instanceof Error ? e.message : String(e)}`
        );
      }
    } finally {
      if (!signal?.aborted) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    fetchReport(controller.signal);
    return () => {
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function handleCopy() {
    if (!report) return;
    try {
      await navigator.clipboard.writeText(report);
      setCopied(true);
      setTimeout(() => setCopied(false), 3000);
    } catch {
      // iOS WebView 等のフォールバック：テキストエリアを選択状態にする
      if (textareaRef.current) {
        textareaRef.current.focus();
        textareaRef.current.select();
        document.execCommand("copy");
        setCopied(true);
        setTimeout(() => setCopied(false), 3000);
      }
    }
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/60 p-2 sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-label="稼働・診断ログ"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90dvh] w-full max-w-lg flex-col rounded-xl bg-white shadow-xl"
        style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* モーダルヘッダー */}
        <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2.5">
          <div className="flex items-center gap-1.5">
            <Terminal className="h-4 w-4 text-emerald-600" />
            <h2 className="text-sm font-bold text-slate-800">
              稼働・診断ログ（チャット貼付用）
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="閉じる"
            className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 active:bg-slate-200"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* 説明＋アクションボタン */}
        <div className="flex flex-col gap-2 px-3 pt-2.5">
          <p className="text-xs text-slate-600">
            GitHub Actions の直近実行結果・スクレイプ稼働状況・各施設データの鮮度をまとめて取得します。下のボタンでコピーしてそのままチャットに貼り付けてください。
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={loading || !report}
              onClick={handleCopy}
              className={cn(
                "flex min-h-[44px] flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-bold text-white transition-colors",
                copied
                  ? "bg-sky-600"
                  : "bg-emerald-600 hover:bg-emerald-700 active:bg-emerald-800 disabled:opacity-50"
              )}
            >
              {copied ? (
                <>
                  <Check className="h-4 w-4" />
                  コピーしました（チャットに貼付可）
                </>
              ) : (
                <>
                  <Copy className="h-4 w-4" />
                  ログをコピーする
                </>
              )}
            </button>
            <button
              type="button"
              disabled={loading}
              onClick={() => fetchReport()}
              aria-label="ログを再取得"
              className="flex min-h-[44px] items-center justify-center gap-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 active:bg-slate-100 disabled:opacity-50"
            >
              <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
              再取得
            </button>
          </div>
        </div>

        {/* ログ本文（iOS Safari の自動ズーム防止のため text-base 16px を確保） */}
        <div className="mt-2 flex-1 overflow-hidden px-3">
          <textarea
            ref={textareaRef}
            readOnly
            value={loading && !report ? "診断ログを収集中…" : report}
            aria-label="診断ログ本文"
            className="h-64 w-full resize-none rounded-lg border border-slate-300 bg-slate-950 p-2.5 font-mono text-base leading-snug text-slate-100 focus:outline-none focus:ring-2 focus:ring-emerald-500"
          />
        </div>
      </div>
    </div>
  );
}
