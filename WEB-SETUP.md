# Free Game Tracker

網站前端部署於 GitHub Pages，API 以獨立 Cloudflare Worker 提供。遊戲資料由既有 notifier pipeline 產生，手動領取狀態獨立寫入 GitHub。

## Repository layout

- `web/`: GitHub Pages 靜態前端，透過 GitHub Actions 部署，網域為 `fg.chi.qzz.io`。
- `worker/`: API Worker，綁定 `fg-api.chi.qzz.io`。
- `src/free_game/active.py`: 目前有效限免活動的持久化。
- `data/active-games.json`: notifier 自動更新，僅保留截止時間尚未到期的活動。
- `data/manual-claims.json`: Worker 透過 GitHub Contents API 更新的手動領取狀態。

## OAuth 設定

CHIAuth OAuth Client：

- Name: `Free Game Tracker`
- Redirect URI: `https://fg.chi.qzz.io/`
- Grant: Authorization Code + PKCE (S256)

Client ID 設定於：

- `web/config.js`
- `worker/wrangler.toml`

Client ID 是公開識別值，不是密碼。不要將 CHIAuth access token 或 GitHub Token 寫進前端檔案。

## GitHub Pages

前端由 `.github/workflows/deploy-free-game-pages.yml` 部署：

- Repository: `47tsai/free-game`
- Branch: `main`
- Artifact directory: `web`
- Custom domain: `fg.chi.qzz.io`

`web/CNAME` 設定自訂網域。GitHub Pages 需要在 DNS 將 `fg.chi.qzz.io` 指向 `47tsai.github.io`（CNAME），並在 GitHub Pages 完成網域驗證及 HTTPS 憑證簽發。

## Cloudflare Worker

GitHub Token 使用 Fine-grained PAT，Repository access 僅選 `47tsai/free-game`，Repository permissions 設定 Contents: Read and write。

GitHub Repository Settings → Secrets and variables → Actions：

- `FREE_GAME_GH_TOKEN`: 上述 GitHub Fine-grained PAT
- `CLOUDFLARE_API_TOKEN`: Cloudflare API Token
- `CLOUDFLARE_ACCOUNT_ID`: Cloudflare Account ID

部署工作流程位於 `.github/workflows/deploy-free-game-api.yml`。Worker custom domain 為 `fg-api.chi.qzz.io`，由 Cloudflare 管理。

## 資料與狀態

- 活動識別碼由平台、遊戲名稱、商店連結及截止時間產生。同一款遊戲再次限免會得到新的活動 ID。
- 前端只呈現截止時間仍在未來的活動。
- FGC 狀態從既有 `data/claims.json` 讀取；手動狀態優先，且可手動取消。
- 手動紀錄在活動過期後清除，不作為永久收藏或歷史紀錄。
- 前端只在記憶體保存 access token；OAuth state 與 PKCE verifier 僅暫存在 sessionStorage，交換完成即清除。

### 首次初始化活動清單

新增活動索引功能後，需在 GitHub Actions 手動執行一次 **Free Game Notifier**，讓 notifier 重新掃描 RSS 文章並建立 `data/active-games.json`。完成後，確認該檔案已提交至 `main`；若仍不存在，請檢查該次執行的 `Run notifier` 與 `Save state` logs。

此初始化會執行完整通知流程，若期間發現尚未通知的新活動，仍可能發送 Discord 通知；也會同步處理 ntfy 領取狀態。它不會部署 Cloudflare Worker。
