# GYM Getter 改善計画 (plan.md)

高津スポーツセンターの**予約空き状況**をふれあいネットから自動取得し、身内に Web ＋ LINE で共有するアプリ。
「シンプル・無料（クレジットカード不要）」を最優先にした最小構成。

---

## 現行アーキテクチャ（本番稼働中）
```
GitHub Actions (cron 90分ごと=1日16回 + 手動 workflow_dispatch)
  ├─ scripts/scrape_takatsu.py  (Playwright, FureaiNetScraper.check_area_days を利用 / 3エリア)
  │     ├─ web/public/data.json を生成
  │     └─ 公開中 data.json と差分を取り、空き/埋まりの変化時に「現在の全空き枠を日付順」でLINE通知（Messaging API ブロードキャスト）
  └─ web を build → Firebase Hosting (live) へデプロイ
                          │
                          ▼
                 Firebase Hosting (静的SPA, 無料Spark)
                 Vite+React+TS+Tailwind が data.json を表示（認証なし・時間帯まで）
```
- 定期実行 = **GitHub Actions**（無料・カード不要）。Cloud Run/Scheduler は Blaze=カード必須のため不採用。
- 保存 = 静的 `data.json`（DBなし）
- 対象 = 高津スポーツセンターのみ / `reservation`（予約）/ 3エリア（大体育室半面Ａ・Ｂ・小体育室）
- 表示 = 日×エリアの空き○/一部△/予約あり×、空きの日は時間帯（午前/午後1/午後2/夜間）まで展開。
- 通知 = `LINE_CHANNEL_ACCESS_TOKEN`（GitHub Secrets）。前回比で**空き枠が増えた／埋まった変化があった時のみ**通知し、本文には**現在予約できる全枠を日付順**に列挙（先頭に「新たに○枠/埋まり○枠」サマリ）。変化なしの実行では送信しない。
- メンテ時間等で取得0件のときは `data.json` を上書きせず deploy/通知をスキップ（ライブ保持）。

## 主要ファイル
- `scripts/scrape_takatsu.py` … スタンドアロン・スクレイパー（DB非依存）。`data.json` 生成＋LINE差分通知
- `backend/scrapers/fureai_net.py` … `FureaiNetScraper`（`check_area_days` / `FACILITY_NAV` / `get_target_dates` / `map_status`）
- `backend/scrapers/base.py` … Playwright ライフサイクル管理のみの基底クラス
- `requirements-scrape.txt` … playwright / python-dotenv のみ（現役の唯一の依存定義）
- `web/` … Vite+React+TS+Tailwind の静的SPA（`src/App.tsx`, `src/lib/schema.ts`(Zod), `src/components/ui/*`）
- `firebase.json` / `.firebaserc` … Hosting設定（public=web/dist, SPA rewrite, data.jsonはno-cache）
- `.github/workflows/scrape.yml` … スクレイプ→build→Hostingデプロイ

## data.json スキーマ（periods 形式・現行）
```json
{ "facility":"高津スポーツセンター","bookingType":"reservation","updatedAt":"…(ISO/UTC)",
  "areas":["大体育室半面Ａ","大体育室半面Ｂ","小体育室"],
  "periodOrder":["午前","午後1","午後2","夜間"],
  "days":[{"date":"2026-06-14","weekday":"土",
    "byArea":{"大体育室半面Ａ":{
      "status":"partial","statusRaw":"一部空き",
      "periods":{"午前":"occupied","午後1":"available","午後2":"available","夜間":"open"}
    }}}]}
```
- `periods` は「空き(○/△)の日」のみ埋まる。それ以外は `{}`（UIは日単位 status で表示）。

---

## 進捗（初期構築フェーズ）
- [x] **Step 1: スクレイパー単体** … `scripts/scrape_takatsu.py`。ログイン・3エリア取得・`data.json` 生成OK。
- [x] **Step 2: 表示SPA** … `web/`(Vite)。日×エリア表示／空きのみトグル／時間帯展開／凡例／更新ボタン。
- [x] **Step 3: Firebase Hosting** … `gym-getter` にデプロイ。公開URL **https://gym-getter.web.app**。
- [x] **Step 4: GitHub Actions 自動化** … Secrets 登録済み・cron 90分で本番稼働中。LINE新着通知も稼働。

## 改善フェーズ（メンテ性・安定性・拡張性・満足度）
> 詳細計画: `~/.claude/plans/logical-humming-milner.md`
- [x] **Phase 0: 旧スタック削除＋ドキュメント同期**
      - 旧 FastAPI/SQLite/vanilla frontend 一式・`run.py`・`test_*.py`・探索用スクリプト・旧 `requirements.txt` を削除。
      - `fureai_net.py` の未使用メソッド（`check_availability`/`check_single_area`/`discover_*`/`detect_booking_flow`/
        `_extract_weekly_slots`/`_navigate_to_top`/`STATUS_MAP`/`_parse_date_header`）を削除（953→470行）。
      - `base.py` を Playwright ライフサイクルのみの最小構成に。`.env.example`・`plan.md` を現状同期。
      - 検証: 構文OK・import OK・`check_area_days` の依存メソッド全存在を確認。
- [x] **Phase 1: 安定性ハードニング**
      - `login()` にメンテ判定フラグ `maintenance` を追加。メンテ時間（正常）と真のログイン失敗を区別。
      - `scrape_takatsu.py` をエリア単位の成否判定に再構成（`_collect_records`→by_area, `_area_ok`）。
        一部失敗エリアは前回値を保持（`_overlay_previous`）＋差分通知から除外（`_reservable_keys(only_areas=)`）。
      - 異常時の管理者通知 `_notify_admin`（Actions `::warning::` 注釈＋任意で LINE push）。
        真のログイン失敗は exit 1 でジョブ失敗化。メンテ/全失敗は data.json 維持で exit 0。
      - `keepalive.yml`（毎月1日空コミット）で Actions 休眠停止を防止。`scrape.yml` に `LINE_ADMIN_USER_ID` 追加。
      - 検証: 構文OK＋純粋関数の単体テスト（area_ok/build_payload/overlay/reservable）全通過。
- [x] **Phase 3: ユーザー体験**
      - data.json に `reserveUrl` を追加し、ヘッダーに「予約する」ボタン（ふれあいネット予約導線）。
      - `updatedAt` が3時間超で「データが古い可能性」警告バッジ（`isStale`）。サイレント故障の利用者側保険。
      - PWA化: `manifest.webmanifest` / `sw.js`（data.json は素通り＝鮮度優先）/ `icon.svg` 追加、`index.html`・
        `main.tsx` に配線。`firebase.json` に `/sw.js` の no-cache を追加。
      - 検証: `npm run build` 成功＋dev プレビューで予約ボタン/鮮度バッジ表示・コンソールエラー0を確認。
- [x] **Phase 2: 拡張土台（機能は足さない）**
      - スクレイプ設定を `scrape_takatsu.py` 冒頭に集約（`FACILITY_ID`/`FACILITY_NAME`/`BOOKING_TYPE`/
        `NOTIFY_EMOJI`/`SHORT_AREA`）。`FACILITY_NAV`(`fureai_net.py`)をエリア定義の単一の真実に。
      - 通知見出しを `FACILITY_NAME`/`NOTIFY_EMOJI` 参照に（施設追加で関数編集不要）。施設追加手順をコメント明文化。
      - 通知は `_notify_line`（利用者）/`_notify_admin`（管理者）に分離済み。

## ローカル実行メモ
- スクレイプ: `py scripts/scrape_takatsu.py`（`python` はストアスタブ不可。`py` ランチャ使用）
  - 依存: `py -m pip install -r requirements-scrape.txt` ＋ `py -m playwright install chromium`
  - 環境変数: `.env`（`FUREAI_USER_ID`/`FUREAI_PASSWORD`/`FUREAI_SECURITY_NUMBER`）。`SCRAPE_DAYS_AHEAD`(既定30) / `HEADLESS`(既定true) / `PYTHONUTF8=1` 推奨
- 表示: `cd web && npm run dev`（http://localhost:5173）／ build: `npm run build`

## 留意点 / 将来
- スクレイパーはふれあいネットの HTML 構造依存で本質的に脆い。**壊れたら気づける**仕組み（Phase 1）が最重要。
- GitHub Actions のスケジュールは「リポジトリに60日コミットが無い」と自動停止する点に注意。
- 抽選枠・多摩/等々力・通知フィルタ等の機能拡張は Phase 2 の設定駆動土台の上に後日追加する方針。

## 進捗ログ
- 2026-06-03: 最シンプル・無料版に方針確定。Step1〜3（スクレイパー・SPA・Firebase）完了。
- 2026-06-04: GitHub Actions 自動化稼働。LINE通知を「新しく空いた枠だけ」に変更。cron を90分ごと(1日16回)に。
- 2026-06-14: 改善計画策定（メンテ/安定/拡張/満足度）。**Phase 0〜3 すべて完了**。
  - Phase 0: 旧スタック削除・scraper デッドコード除去（953→470行）・ドキュメント同期。
  - Phase 1: メンテ判定／エリア単位の部分失敗ガード／管理者異常アラート／keepalive／差分堅牢化。
  - Phase 3: 予約導線ボタン／鮮度バッジ／PWA化。
  - Phase 2: スクレイプ設定の集約・通知見出しの設定参照化・施設追加手順の明文化。
  - 残課題（push後）: GitHub Actions で `workflow_dispatch` 実行し、実データ更新＋デプロイ＋異常時通知を確認。
- 2026-06-22: LINE通知を「新着差分だけ」→「変化（空き増/埋まり）時に現在の全空き枠を日付順で通知」に変更。埋まった時も通知が飛ぶように。
    任意で Secret `LINE_ADMIN_USER_ID` を登録すると管理者へ LINE push 通知が届く。
