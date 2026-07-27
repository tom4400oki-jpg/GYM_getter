import React from "react";
import ReactDOM from "react-dom/client";
import App from "@/App";
import "@/index.css";

// LINEアプリ内ブラウザは認証などが動かないため、外部ブラウザで開き直させる。
// openExternalBrowser=1 付与済みなら何もしない（無限ループ防止）。
if (/ Line\//i.test(navigator.userAgent) && !location.search.includes("openExternalBrowser=1")) {
  const sep = location.search ? "&" : "?";
  location.replace(`${location.href}${sep}openExternalBrowser=1`);
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// PWA: Service Worker 登録（ホーム追加・オフライン表示用）
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
