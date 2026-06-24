"""
テニスコート（時間貸し型）ページの HTML を吸い出す調査用スクリプト。

目的: 等々力テニス / 富士見テニス / 多摩スポーツセンター（テニス）の
ふれあいネット上のページ構造（コート選択→日付→時間スロットのリンク/フォーム）を
把握し、専用パーサを実装するための材料を集める。予約操作は一切しない（閲覧のみ）。

出力: tennis_html/ 配下に各ステップの HTML と、リンク/ボタンの一覧（*.links.txt）を保存。
      GitHub Actions ではこのフォルダをアーティファクトとしてアップロードする。

環境変数: FUREAI_USER_ID / FUREAI_PASSWORD / FUREAI_SECURITY_NUMBER / HEADLESS
"""
import os
import sys
import asyncio
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from backend.scrapers.fureai_net import FureaiNetScraper  # noqa: E402

OUT_DIR = ROOT / "tennis_html"

# 調査対象（region / building は FACILITY_NAV と同じ表記）
TARGETS = [
    {"key": "todoroki", "region": "中原区", "building": "等々力テニスコート"},
    {"key": "fujimi",   "region": "川崎区", "building": "富士見テニスコート"},
    {"key": "tama",     "region": "多摩区", "building": "川崎市多摩スポーツセンター"},
]


def _scraper_kwargs() -> dict:
    return {
        "headless": os.getenv("HEADLESS", "true").lower() == "true",
        "user_id": os.getenv("FUREAI_USER_ID", ""),
        "password": os.getenv("FUREAI_PASSWORD", ""),
        "security_no": os.getenv("FUREAI_SECURITY_NUMBER", ""),
    }


def _run_async(coro):
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return asyncio.run(coro)


async def _dump(page, name: str) -> None:
    """現在のページの HTML と、リンク/ボタンの一覧を保存する。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        html = await page.content()
    except Exception as e:
        html = f"<!-- content取得失敗: {e} -->"
    (OUT_DIR / f"{name}.html").write_text(html, encoding="utf-8")

    # リンク・ボタン・見出しの一覧（構造把握用の要約）
    try:
        summary = await page.evaluate(r"""() => {
            const out = [];
            out.push('URL: ' + location.href);
            out.push('TITLE: ' + document.title);
            out.push('--- A links (text | href) ---');
            for (const a of document.querySelectorAll('a')) {
                const t = (a.textContent || '').trim().replace(/\s+/g, ' ');
                out.push(`${t}\t| ${a.getAttribute('href') || ''}`);
            }
            out.push('--- inputs (type | value | name) ---');
            for (const i of document.querySelectorAll('input,button')) {
                out.push(`${i.type || i.tagName}\t| ${i.value || (i.textContent||'').trim()}\t| ${i.name || ''}`);
            }
            out.push('--- img alt (空き状況アイコン候補) ---');
            const alts = new Set();
            for (const im of document.querySelectorAll('img')) {
                if (im.alt) alts.add(im.alt);
            }
            out.push([...alts].join('\n'));
            return out.join('\n');
        }""")
    except Exception as e:
        summary = f"summary取得失敗: {e}"
    (OUT_DIR / f"{name}.links.txt").write_text(summary, encoding="utf-8")
    print(f"[dump] {name} -> {page.url}", flush=True)


async def _safe(coro, label: str) -> bool:
    try:
        await coro
        return True
    except Exception as e:
        print(f"[skip] {label}: {e}", flush=True)
        return False


async def dump_all() -> int:
    async with FureaiNetScraper(**_scraper_kwargs()) as scraper:
        if not await scraper.login():
            print("[エラー] ログイン失敗。Secrets を確認してください。", flush=True)
            return 1
        page = scraper.page

        for t in TARGETS:
            key = t["key"]
            print(f"\n=== {t['building']} ({key}) ===", flush=True)
            # 予約メニュー → 地域から → 地域 → 館 と辿り、各段でダンプ
            await _safe(scraper._click_menu_link("reservation"), f"{key}:menu")
            await _dump(page, f"{key}_1_menu")

            await _safe(scraper._click_button("地域から"), f"{key}:地域から")
            await _dump(page, f"{key}_2_region_list")

            await _safe(scraper._click_link_text(t["region"]), f"{key}:region")
            await _dump(page, f"{key}_3_buildings")

            await _safe(scraper._click_link_text(t["building"]), f"{key}:building")
            await _dump(page, f"{key}_4_after_building")

            # 館選択後のページから、施設(コート)/日付らしきリンクを1つ辿ってさらにダンプ
            try:
                href = await page.evaluate(r"""() => {
                    const a = [...document.querySelectorAll('a')].find(a =>
                        /sendInstNo|selectDay|テニス|コート|次へ|表示/.test(
                            (a.textContent||'') + (a.getAttribute('href')||'')));
                    return a ? (a.textContent||'').trim() : null;
                }""")
            except Exception:
                href = None
            if href:
                print(f"[drill] '{href}' を辿ってみる", flush=True)
                ok = await _safe(scraper._click_link_text(href), f"{key}:drill")
                if ok:
                    await _dump(page, f"{key}_5_drill")
        return 0


if __name__ == "__main__":
    sys.exit(_run_async(dump_all()))
