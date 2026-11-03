# Architecture

## Overview

ntfy claim feed → claim-state sync → existing-game claim sync → 4Gamers RSS → RSS parser → Article parser → Gemini extractor → Game validation → initial Discord embed → 24-hour reminder check

## Modules

- `main.py`: 串接流程，以遊戲為單位處理成功與失敗。同篇文章可產生多款遊戲，單款失敗不會丟掉其他成功結果。
- `rss.py`: 解析 4Gamers RSS，依標題與摘要篩選限免文章。
- `article.py`: 解析文章文字、OG Image、Steam / Epic 商店連結與連結 context；Steam Widget URL 會正規化。
- `ai.py`: 使用 Gemini 結構化遊戲資料。AI 回傳 `store_index`，程式再從已驗證的候選 URL 取值，不直接信任 AI URL。
- `stores.py`: 先以 Steam App ID 查價，無法取得時才搜尋 Steam；Epic 不會呼叫 Steam 查價。
- `discord.py`: 發送簡潔狀態 Embed；尚未領取通知提供 4Gamers 與網站手動標記連結。
- `claims.py`: 透過 ntfy JSON poll 增量讀取 FGC 摘要，以平台與正規化標題精確配對 Steam / Epic / GOG 遊戲；領取紀錄與訊息游標分開保存。
- `notifications.py`: 讀取 Worker 管理的手動領取紀錄，追蹤首次通知、自動領取通知及一次性 24 小時提醒；追蹤檔案與會過期的網站活動索引分開保存。\n- `state.py`: 保存文章與遊戲狀態。遊戲 key 不綁定 4Gamers 文章 URL，因此跨文章也能去重，並保留舊版 key 的相容判斷。
- `http.py`: 統一 HTTP GET / POST JSON，對暫時性錯誤與 Rate Limit 有限重試。

## GitHub Actions

- Python 3.12
- 僅支援 GitHub Actions 手動觸發（`workflow_dispatch`）；目前此 Repository 的 Workflow 未設定 `schedule`，因此不能將定期執行視為由 GitHub Actions 排程。定期執行由 cron-job.org 外部觸發此 workflow。
- 測試通過後才執行 notifier
- `data/processed.json`、`data/claims.json`、`data/ntfy_cursor.json`、`data/active-games.json`、`data/notification-state.json` 由 Actions 自動寫回 repository；`data/manual-claims.json` 由 Worker 管理，Actions 只讀取
- concurrency 避免多個執行同時修改 state

## 設計取向

優先順序：**不漏遊戲 > 不重複通知 > API 使用量**。
因此去重可以保守，但遊戲辨識與發送流程不應過度過濾。
