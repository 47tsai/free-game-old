# Free Game Tracker

網站前端部署於 Cloudflare Pages，API 以獨立 Cloudflare Worker 提供。遊戲資料由既有 notifier pipeline 產生，手動領取狀態獨立寫入 GitHub。

## Repository layout

- `web/`: Cloudflare Pages 靜態前端，Pages output directory 設為 `web`。
- `worker/`: API Worker，綁定 `fg-api.chi.qzz.io`。
- `src/free_game/active.py`: 目前有效限免活動的持久化。
- `data/active-games.json`: notifier 自動更新，僅保留截止時間尚未到期的活動。
- `data/manual-claims.json`: Worker 透過 GitHub Contents API 更新的手動領取狀態。

## OAuth 設定

在 CHIAuth 建立 OAuth Client：

- Name: `Free Game Tracker`
- Redirect URI: `https://fg.chi.qzz.io/`
- Grant: Authorization Code + PKCE (S256)

將取得的 Client ID 同時填入：

- `web/config.js` 的 `clientId`
- `worker/wrangler.toml` 的 `CHI_CLIENT_ID`

Client ID 是公開識別值，不是密碼。不要將 CHIAuth access token 或 GitHub Token 寫進前端檔案。

## Cloudflare Worker

GitHub Token 使用 Fine-grained PAT，Repository access 僅選 `47tsai/free-game`，Repository permissions 設定 Contents: Read and write。

在 GitHub Repository Settings → Secrets and variables → Actions 建立以下 Repository Secrets：

- `FREE_GAME_GH_TOKEN`: 上述 GitHub Fine-grained PAT
- `CLOUDFLARE_API_TOKEN`: Cloudflare API Token，需有目標帳號的 Workers Scripts 編輯權限，以及設定自訂網域所需的 Zone/DNS 權限
- `CLOUDFLARE_ACCOUNT_ID`: Cloudflare Account ID

部署 Worker 時，GitHub Actions 會將 `FREE_GAME_GH_TOKEN` 寫入 Worker Secret（同名），再執行 Wrangler 部署。請勿將 Token 寫進程式碼或提交到 Repository。

部署工作流程位於 `.github/workflows/deploy-free-game-api.yml`，可從 GitHub Actions 手動執行。

Worker custom domain 為 `fg-api.chi.qzz.io`。確認該網域已由 Cloudflare DNS 管理，並且 Worker 部署成功。

## Cloudflare Pages

建立 Pages 專案並連接 `47tsai/free-game`：

- Production branch: `main`
- Build command: 留空
- Build output directory: `web`
- Custom domain: `fg.chi.qzz.io`

合併程式分支後，Pages 會從 `web/` 部署靜態網站。

## 資料與狀態

- 活動識別碼由平台、遊戲名稱、商店連結及截止時間產生。同一款遊戲再次限免會得到新的活動 ID。
- 前端只呈現截止時間仍在未來的活動。
- FGC 狀態從既有 `data/claims.json` 讀取；手動狀態優先，且可手動取消。
- 手動紀錄在活動過期後清除，不作為永久收藏或歷史紀錄。
- 前端只在記憶體保存 access token；OAuth state 與 PKCE verifier 僅暫存在 sessionStorage，交換完成即清除。
