"""
GYM Getter 起動スクリプト
使い方: python run.py
"""
import subprocess
import sys
import os
from pathlib import Path


def check_playwright():
    """Playwright のブラウザがインストール済みか確認"""
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def main():
    # .env 読み込み
    env_file = Path(".env")
    if not env_file.exists() and Path(".env.example").exists():
        import shutil
        shutil.copy(".env.example", ".env")
        print("✓ .env を作成しました（必要に応じて編集してください）")

    # Playwright インストール確認
    try:
        import playwright
    except ImportError:
        print("✗ playwright がインストールされていません。requirements.txt を確認してください。")
        sys.exit(1)

    if not check_playwright():
        print("Playwright ブラウザをインストールします...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        print("✓ Playwright chromium インストール完了")

    print("=" * 50)
    print("GYM Getter 起動中...")
    print("  URL: http://localhost:8000")
    print("  API: http://localhost:8000/docs")
    print("  Ctrl+C で停止")
    print("=" * 50)

    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["backend", "frontend"],
    )


if __name__ == "__main__":
    main()
