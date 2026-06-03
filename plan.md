# GYM Getter 改善計画 (plan.md)

高津スポーツセンターの**予約空き状況**をふれあいネットから自動取得し、身内でWeb共有するアプリ。
「シンプル・無料（クレジットカード不要）」を最優先にした最小構成。

> 旧「Firebase全面移行＋Cloud Run」案は将来の拡張案として末尾に保持。

---

## 現行アーキテクチャ（最シンプル・無料版）
```
GitHub Actions (cron 1日3回 + 手動)
  ├─ scripts/scrape_takatsu.py  (Playwright, 既存FureaiNetScraperを再利用 / 3エリア)
  │     └─ web/public/data.json を生成
  └─ web を build → Firebase Hosting へデプロイ
                          │
                          ▼
                 Firebase Hosting (静的SPA, 無料Spark)
                 Vite+React+TS+Tailwind+shadcn が data.json を表示（認証なし・時間帯まで）
```
- 定期実行 = **GitHub Actions**（無料・カード不要）。Cloud Run/Schedulerは Blaze=カード必須のため不採用。
- 保存 = 静的 `data.json`（DBなし）
- 対象 = 高津スポーツセンターのみ / `reservation`（予約）/ 3エリア（大体育室半面Ａ・Ｂ・小体育室）
- 表示 = 日×エリアの空き○/一部△/予約あり×、空きの日は時間帯まで展開。小さめ密表示。
- 既存 FastAPI / SQLite / APScheduler 一式は触らず残置。

## 主要ファイル
- `scripts/scrape_takatsu.py` … スタンドアロン・スクレイパー（DB非依存）。`data.json`生成
- `requirements-scrape.txt` … playwright / python-dotenv のみ
- `web/` … Vite+React+TS+Tailwind+shadcn の静的SPA（`src/App.tsx`, `src/lib/schema.ts`(Zod), `src/components/ui/*`）
- `firebase.json` / `.firebaserc` … Hosting設定（public=web/dist, SPA rewrite, data.jsonはno-cache）
- `.github/workflows/scrape.yml` … スクレイプ→build→Hostingデプロイ

## data.json スキーマ
```json
{ "facility":"高津スポーツセンター","bookingType":"reservation","updatedAt":"…",
  "areas":["大体育室半面Ａ","大体育室半面Ｂ","小体育室"],
  "days":[{"date":"2026-06-04","weekday":"木",
    "byArea":{"大体育室半面Ａ":{"status":"partial","statusRaw":"一部空き",
      "slots":[{"start":"09:00","end":"11:00","status":"available"}]}}}]}
```

---

## 進捗（フェーズ）
- [x] **Step 1: スクレイパー単体** … `scripts/scrape_takatsu.py` 作成・ローカル実行で`data.json`生成を確認。
      Windows cp932 の print クラッシュを UTF-8 固定で解消済み。ログイン成功・3エリア取得OK。
- [x] **Step 2: 表示SPA** … `web/`(Vite+shadcn) 作成。`npm run build`成功、devでレンダリング・
      コンソールエラーなしを確認。日×エリア表示／空きのみトグル／時間帯展開／凡例／更新ボタン。
- [x] **Step 3: Firebase Hosting** … プロジェクト `gym-getter` に手動デプロイ完了。
      公開URL **https://gym-getter.web.app** で30日分・空き状況を確認済み。
      `.firebaserc` / `scrape.yml` の projectId を `gym-getter` に設定済み。
- [ ] **Step 4: GitHub Actions 自動化**（※ユーザー操作が必要）
      - [ ] コードを GitHub(main) に push（workflow/web/scripts を反映）
      - [ ] GitHub Secrets 登録: `FUREAI_USER_ID` / `FUREAI_PASSWORD` / `FUREAI_SECURITY_NUMBER` /
            `FIREBASE_SERVICE_ACCOUNT`(サービスアカウントJSON)
      - [ ] `workflow_dispatch` で手動実行し、データ更新＋自動デプロイを確認 → cron 有効化

## ローカル実行メモ
- スクレイプ: `py scripts/scrape_takatsu.py`（`python`はストアスタブ不可。`py`ランチャ使用）
  - 環境変数: `SCRAPE_DAYS_AHEAD`(既定30) / `HEADLESS`(既定true) / `PYTHONUTF8=1`推奨
  - 認証情報は `.env`（`FUREAI_USER_ID`等）
- 表示: `cd web && npm run dev`（http://localhost:5173）

## 留意点 / 将来
- ふれあいネットがログイン無しで空き閲覧できるなら認証Secrets省略で更に簡素化可能（要確認）
- 抽選枠・他施設・空き出現時の通知(Discord/メール)・履歴は data.json 拡張＋Actions追記で対応可
- 認証付き/リアルタイムが欲しくなったら保存先を data.json → Firestore に差し替えて段階移行

---

## （将来案）フル構成
Cloud Run Jobs(Playwright)+Cloud Scheduler+Firestore+Firebase Auth(許可リスト)+Next.js。
PC電源非依存・リアルタイム・認証保護が必要になった段階で移行。要 Blaze(カード)。

## 進捗ログ
- 2026-06-03: 最シンプル・無料版に方針確定。Step1(スクレイパー)・Step2(SPA)実装＆ローカル動作確認完了。
  Firebase設定・Actionsワークフロー雛形を作成（Step3/4はユーザーのFirebase作成・Secrets登録待ち）。
- 2026-06-03: Firebase(gym-getter)へデプロイ。https://gym-getter.web.app 公開。
- 2026-06-03: 【精度修正】実物と表示が不一致だった問題を解消。
  - 原因1: 空き日のalt属性が日付前置（例「2026年6月8日一部空き」）で、STATUS_MAP完全一致が外れ
    空き日が軒並み「−(不明)」化 → `map_status()` の部分一致に変更。
  - 原因2: 時間帯解析が「9:00～11:00」形式前提で、実際の「午前/午後1/午後2/夜間」非対応＋
    日リンクが `javascript:selectDay()` のためテキストクリック失敗 →
    `_click_day()`/`_extract_day_periods()`/`check_area_days()` を新設し時間帯（週表示）を正確に取得。
  - UIを時間帯グリッド（午前/午後1/午後2/夜間）に刷新。「予約可能」=○△のみカウント、◎一般開放は別表示。
  - 検証: 6/8 半面Ｂ＝午前×/午後1×/午後2○/夜間◎ が実物と一致。30日/予約可能3日で再デプロイ。
