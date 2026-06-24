# GYM Getter 改善計画 (plan.md)

高津スポーツセンターの**予約空き状況**をふれあいネットから自動取得し、身内に Web ＋ LINE で共有するアプリ。
「シンプル・無料（クレジットカード不要）」を最優先にした最小構成。

---

## 現行アーキテクチャ（本番稼働中）
```
GitHub Actions (cron 90分ごと=1日16回 + 手動 workflow_dispatch)
  ├─ scripts/scrape_takatsu.py  (Playwright / 複数施設を1ログインで順次取得)
  │     ├─ web/public/data.json（高津）・data-plaza.json（市民プラザ）を生成
  │     └─ 施設ごとに公開中 data と差分を取り、空き/埋まりの変化時に「現在の全空き枠を日付順」でLINE通知（施設名付き・ブロードキャスト）
  └─ web を build → Firebase Hosting (live) へデプロイ
                          │
                          ▼
                 Firebase Hosting (静的SPA, 無料Spark)
                 Vite+React+TS+Tailwind が施設タブで data(-*).json を表示（認証なし・時間帯/時刻/料金まで）
```
- 定期実行 = **GitHub Actions**（無料・カード不要）。Cloud Run/Scheduler は Blaze=カード必須のため不採用。
- 保存 = 静的 `data.json` / `data-plaza.json`（施設=1ファイル, DBなし）
- 対象（10タブ）= 体育館系（時間帯貸し・取得可）: 高津（大体育室半面Ａ・Ｂ・小）／宮前（半面Ａ・Ｂ）／幸（半面Ａ・Ｂ・小）／麻生（半面Ａ・Ｂ）／多摩体育館（半面Ａ・Ｂ）／カルッツかわさき（半面Ａ・Ｂ・小）／川崎市民プラザ 体育館／とどろきアリーナ（メイン・サブ・体育室1/2）。テニス系（時間貸し・※スクレイプ未対応）: 等々力テニス（10面）／富士見テニス（12面）。/ `reservation`（予約）
- 表示 = 施設タブ切替。日×エリアの空き○/一部△/予約あり×、空きの日は時間帯（午前/午後1/午後2/夜間）＋各時間帯の時刻と使用料まで展開。
- 通知 = `LINE_CHANNEL_ACCESS_TOKEN`（GitHub Secrets）。前回比で**空き枠が増えた／埋まった変化があった時のみ**通知し、本文には**現在予約できる全枠を日付順**に列挙（先頭に「新たに○枠/埋まり○枠」サマリ）。変化なしの実行では送信しない。
- メンテ時間等で取得0件のときは `data.json` を上書きせず deploy/通知をスキップ（ライブ保持）。

## 主要ファイル
- `scripts/scrape_takatsu.py` … スタンドアロン・スクレイパー（DB非依存）。`FACILITIES`(FacilityConfig のリスト)で複数施設を定義し、1ログインで順次取得→施設ごとに `data(-*).json` 生成＋LINE通知。施設追加はここに1エントリ＋`FACILITY_NAV` 追加＋Webの `FACILITIES`/`PRICES` 追加
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
- 2026-06-22: 時間帯の時刻と使用料をUIに表示（高津）。スクレイパーを複数施設対応に refactor（`FacilityConfig`/`FACILITIES`、1ログインで順次取得、施設別 data ファイル）。**川崎市民プラザ 体育館を2施設目として追加**（Web=施設タブ、`PRICES`に施設別料金、`data-plaza.json`、sw.js を `data-*.json` 素通り対応）。
- 2026-06-22: **とどろきアリーナを3施設目として追加**（メイン/サブアリーナ・体育室1/2、`data-todoroki.json`）。料金は令和5年度改定「アマチュアスポーツ・入場料なし」区分（サブは全面料金）。⚠ プラザ・とどろきのエリア名/index は仮。初回 Actions 実行で取得失敗の管理者通知が出たら `FACILITY_NAV` の name/index を実際のリンク文字列に調整。
- 2026-06-22: **宮前スポーツセンター（大体育室・半面Ａ/Ｂのみ）を追加**。高津と同じ時間帯貸し形式なので既存スクレイパーで取得可（`scrape_enabled=True`、`data-miyamae.json`）。料金=大体育室 半面（入場料徴収しない・高津の半面と同額）。
- 2026-06-22: **幸スポーツセンター（大体育室半面Ａ/Ｂ・小体育室）を追加**。高津と同形式・**同額**（Web `PRICES` は `SPORTS_CENTER_PRICE` を高津と共用）。`data-saiwai.json`・`scrape_enabled=True`。タブが6つになったため施設タブを `flex-wrap` に変更。
- 2026-06-22: **麻生スポーツセンター（大体育室半面Ａ/Ｂ）を追加**。高津と同形式・同額（`PRICES.asao` も `SPORTS_CENTER_PRICE` 共用）。`data-asao.json`・`scrape_enabled=True`。小体育室は料金未確認のため対象外。計7施設。
- 2026-06-22: **多摩スポーツセンターの体育館（大体育室半面Ａ/Ｂ）を追加**。多摩は体育館（時間帯貸し）＋テニス（時間貸し）の混在施設だが、データモデルは1タブ=1時間帯構成のため**体育館のみ先行**（`FACILITY_NAV.tama_sports_center.areas` を大体育室半面Ａ/Ｂ＝index2,3 に絞った）。料金=高津と同額（`PRICES.tama`=`SPORTS_CENTER_PRICE`）。`data-tama.json`・`scrape_enabled=True`。計8施設タブ。
  - **多摩テニス（テニス場Ａ/Ｂ）は等々力テニスのパーサ実装とまとめて対応予定**。料金は等々力と別（個人利用: 2時間枠1,800円/3時間枠2,700円/18:30枠は照明込3,720円）でスロット時間長も要確認。
- 2026-06-22: **カルッツかわさき（体育館）を追加**。高津と同形式だが料金は別（半面4,500〜6,900円/小5,600〜8,300円・平日土日同額フラット）。`PRICES.culttz`・`data-culttz.json`・`scrape_enabled=True`。
- 2026-06-22: **富士見テニスコート（12面）を追加（UIのみ・スクレイプ未対応）**。等々力とスロットが違う（9:00/11:00/13:00/15:00/17:00/18:30の6枠）。**季節別料金**（`fujimiPrice(period,date)`: 3〜9月は17:00枠1,650円・11〜12月は15:00枠2,800円・他は2,200/2,550/3,400）。
  - 付随リファクタ: スロット時刻が施設で異なる（等々力9:00=9-12, 富士見9:00=9-11）ため、`PERIOD_TIMES` を `BASE_TIMES`＋`PERIOD_TIMES_BY_FACILITY`＋`periodTime(facilityId,p)` に分離。`priceFor` に `date` 引数追加。`isTennis` は tennis/fujimi 両対応。テニス系タブは `等々力庭球`/`富士見庭球`。
  - 残テニス3施設（等々力・多摩・富士見）の**スクレイパーは引き続きHTML待ち**。UI/料金/レイアウトは実装済み。
- 2026-06-22: **施設タブを2カテゴリ（体育館／テニス）に分割**。`FACILITIES` に `cat:"hall"|"tennis"`、`CATEGORIES` 定義。上段=カテゴリタブ、下段=選択カテゴリの施設タブ（`visibleFacilities`）。カテゴリ切替で先頭施設へ自動移動（`selectCategory`）。タブ短縮名は等々力/富士見に簡略化。
- 2026-06-24: **テニス（時間貸し）のスクレイパー実装・有効化**。調査の結果テニスも体育館と同じ「施設選択→月カレンダー→日クリック」構造、違いは日クリック後の時間スロット画面のみ（img alt 例「6月25日木曜日０９００予約あり」）と判明。`_extract_tennis_slots` を追加し、体育館抽出が空ならフォールバック。施設選択 index 代替で先頭「すべて」を除外（テニスの index ずれ修正）。`FacilityConfig.period_order` 追加。
  - **性能対策**: テニスは22面で重く90分×16回だと無料枠超過＋20分タイムアウト。`FacilityConfig.group`(hall/tennis)＋環境変数 `SCRAPE_GROUP` で分離。体育館=`scrape.yml`(group=hall)、テニス=`scrape-tennis.yml`(1日1回 JST5:00, group=tennis, timeout35分)。各ワークフローは担当外施設の公開中JSONを web/public に書き戻してデプロイ退行を防止。concurrency group は共通(scrape-deploy)で同時デプロイ回避。
  - **無料枠の実測調整(2026-06-24)**: 実測で体育館1回≈8分・テニス1回≈12.5分。8館に増えたため当初の3時間ごと(8回/日)では約2,295分/月で無料枠2,000超過。**体育館を4時間ごと(6回/日)に変更** → 体育館1,440＋テニス375≈1,815分/月で枠内。さらに頻度を上げたい場合はスクレイパーの日ドリル再ナビ最適化(もどる/次の週ボタン活用)が必要。
  - 調査用 `scripts/dump_tennis_html.py`＋`dump-tennis.yml` は役目を終えたら削除可。
- 2026-06-22: **等々力テニスコートを4施設目として追加（UI完成・スクレイパーのみ未）**。ふれあいネットが「時間貸し型（開始時刻スロット 9:00/12:00/14:00/16:00/18:30・コート10面・1日単位の全コート一覧ビュー）」で既存の時間帯貸しパーサと別形式。
  - 料金=専用使用料1面1枠（9:00→2,280円/12:00・14:00・18:30→1,520円/16:00→1,900円、平日土日同額）。
  - **UI完成**: テニスは `isTennis` 分岐で「コート=行 × スロット=列」の転置レイアウト（コート10面でもモバイルに収まる）。日別一覧行はコート列にせず「空き○枠」サマリ表示。あわせて一覧ヘッダー/日別行のグリッド列数を `areas.length` 連動に修正（従来 `repeat(3)` 固定でプラザ1面/とどろき4面が崩れていたバグも解消）。
  - **スクレイパー未対応**: `FacilityConfig.scrape_enabled=False` で取得対象から除外（失敗アラート防止）。`data-tennis.json` は空プレースホルダ。
  - **TODO（要HTML）**: 実ページ（コート選択→日付→スロットグリッド）のHTMLを入手後、(1) テニス用ナビ＋スロットグリッドのパーサ実装、(2) `scrape_enabled=True` に。UI/料金/転置レイアウトは実装済みなのでパーサが `periods={"9:00":..,"12:00":..}` を埋めれば表示される。
    任意で Secret `LINE_ADMIN_USER_ID` を登録すると管理者へ LINE push 通知が届く。
