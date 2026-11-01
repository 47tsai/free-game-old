const REPO = "47tsai/free-game";
const ACTIVE_PATH = "data/active-games.json";
const MANUAL_PATH = "data/manual-claims.json";
const AUTO_PATH = "data/claims.json";

function json(data, status = 200, origin = "*") {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "Access-Control-Allow-Origin": origin,
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Authorization, Content-Type",
      "Vary": "Origin",
    },
  });
}

function allowedOrigin(request, env) {
  const origin = request.headers.get("Origin") || "";
  return origin === env.APP_ORIGIN ? origin : env.APP_ORIGIN;
}

function encodeBase64(value) {
  const bytes = new TextEncoder().encode(value);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

function decodeBase64(value) {
  const binary = atob(String(value || "").replace(/\s/g, ""));
  const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

async function githubRequest(path, env, options = {}) {
  return fetch("https://api.github.com/repos/" + REPO + "/contents/" + path, {
    ...options,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: "Bearer " + env.FREE_GAME_GH_TOKEN,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "chi-free-game-tracker",
      ...(options.headers || {}),
    },
  });
}

async function readJsonFile(path, env, fallback) {
  const response = await githubRequest(path, env);
  if (response.status === 404) return { sha: null, document: fallback };
  if (!response.ok) throw new Error("GitHub 讀取 " + path + " 失敗：" + response.status);
  const file = await response.json();
  return { sha: file.sha, document: JSON.parse(decodeBase64(file.content)) };
}

async function readPublicJsonFile(path, fallback) {
  const response = await fetch(
    "https://raw.githubusercontent.com/" + REPO + "/main/" + path,
    { headers: { "Cache-Control": "no-cache" } },
  );
  if (response.status === 404) return fallback;
  if (!response.ok) throw new Error("GitHub 讀取 " + path + " 失敗：" + response.status);
  return response.json();
}

async function readManualClaims(env) {
  const result = await readJsonFile(MANUAL_PATH, env, { claims: {} });
  if (!result.document || typeof result.document !== "object"
      || !result.document.claims || typeof result.document.claims !== "object") {
    throw new Error("手動領取資料格式錯誤");
  }
  return result;
}

async function readPublicManualClaims() {
  const document = await readPublicJsonFile(MANUAL_PATH, { claims: {} });
  if (!document || typeof document !== "object"
      || !document.claims || typeof document.claims !== "object") {
    throw new Error("手動領取資料格式錯誤");
  }
  return document.claims;
}

async function readAutoClaims() {
  const document = await readPublicJsonFile(AUTO_PATH, {});
  return document && typeof document === "object" ? document : {};
}

async function readActiveGames() {
  const response = await fetch(
    "https://raw.githubusercontent.com/" + REPO + "/main/" + ACTIVE_PATH,
    { headers: { "Cache-Control": "no-cache" } },
  );
  if (response.status === 404) return { updated_at: null, games: [] };
  if (!response.ok) throw new Error("GitHub 讀取限免清單失敗：" + response.status);
  const data = await response.json();
  if (!data || !Array.isArray(data.games)) throw new Error("限免清單格式錯誤");
  const now = Date.now();
  const games = data.games.filter((game) => {
    const deadline = Date.parse(game.deadline || "");
    return Number.isFinite(deadline) && deadline > now;
  });
  return { updated_at: data.updated_at || null, games };
}

function normalizeTitle(value) {
  return String(value || "").normalize("NFKC").toLocaleLowerCase().trim().replace(/\s+/g, " ");
}

function platformKey(value) {
  const platform = String(value || "").toLocaleLowerCase();
  if (platform.includes("steam")) return "steam";
  if (platform.includes("epic")) return "epic";
  return "";
}

function autoStatus(record) {
  if (!record) return "";
  const value = String(record.status || "").trim().toLocaleLowerCase();
  if (value === "claimed") return "已領取";
  if (value.includes("already") || value.includes("exist")) return "已擁有";
  if (value.includes("available")) return "尚未領取";
  if (value.includes("fail")) return "領取失敗";
  if (value.includes("skip")) return "未完成";
  return "尚無回報";
}

async function verifyOwner(request, env) {
  const authorization = request.headers.get("Authorization") || "";
  if (!authorization.startsWith("Bearer ")) return false;
  const response = await fetch(env.CHI_AUTH_URL.replace(/\/$/, "") + "/api/userinfo", {
    headers: { Authorization: authorization },
  });
  if (!response.ok) return false;
  const user = await response.json();
  return user.sub === "chi-owner" && user.aud === env.CHI_CLIENT_ID;
}

async function writeManualClaim(eventId, claimed, activeIds, env) {
  for (let attempt = 0; attempt < 2; attempt++) {
    const current = await readManualClaims(env);
    const claims = Object.fromEntries(Object.entries(current.document.claims).filter(([id]) => activeIds.has(id)));
    claims[eventId] = {
      claimed,
      updated_at: new Date().toISOString(),
    };
    const document = JSON.stringify({
      updated_at: new Date().toISOString(),
      claims,
    }, null, 2) + "\n";

    const response = await githubRequest(MANUAL_PATH, env, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: "chore: update manual game claim status",
        content: encodeBase64(document),
        ...(current.sha ? { sha: current.sha } : {}),
      }),
    });

    if (response.ok) return claims[eventId];
    if (response.status !== 409 && response.status !== 422) {
      const detail = await response.text();
      throw new Error("GitHub 寫入失敗：" + response.status + " " + detail.slice(0, 300));
    }
  }
  throw new Error("資料剛剛有其他更新，請重新操作");
}

export default {
  async scheduled(_controller, env) {
    const active = await readActiveGames();
    const activeIds = new Set(active.games.map((game) => game.id));
    for (let attempt = 0; attempt < 2; attempt++) {
      const current = await readManualClaims(env);
      const claims = Object.fromEntries(
        Object.entries(current.document.claims).filter(([id]) => activeIds.has(id)),
      );
      if (Object.keys(claims).length === Object.keys(current.document.claims).length) return;
      const document = JSON.stringify({
        updated_at: new Date().toISOString(),
        claims,
      }, null, 2) + "\n";
      const response = await githubRequest(MANUAL_PATH, env, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: "chore: prune expired manual game claims",
          content: encodeBase64(document),
          ...(current.sha ? { sha: current.sha } : {}),
        }),
      });
      if (response.ok) return;
      if (response.status !== 409 && response.status !== 422) {
        throw new Error("GitHub 清理過期領取紀錄失敗：" + response.status);
      }
    }
    throw new Error("清理時資料持續變動，將等待下次排程");
  },

  async fetch(request, env) {
    const origin = allowedOrigin(request, env);
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": origin,
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "Authorization, Content-Type",
          "Access-Control-Max-Age": "86400",
          "Vary": "Origin",
        },
      });
    }

    const url = new URL(request.url);
    try {
      if (request.method === "GET" && url.pathname === "/api/games") {
        const [active, manual, automatic] = await Promise.all([
          readActiveGames(),
          readPublicManualClaims(),
          readAutoClaims(),
        ]);
        const games = active.games.map((game) => {
          const key = platformKey(game.platform) + "|" + normalizeTitle(game.name);
          return {
            ...game,
            claim_status: autoStatus(automatic[key]) || game.claim_status || "尚無回報",
          };
        });
        return json({
          updated_at: active.updated_at,
          games,
          manual_claims: manual,
        }, 200, origin);
      }

      if (request.method === "POST" && url.pathname === "/api/claim") {
        if (!(await verifyOwner(request, env))) {
          return json({ error: "請先使用 CHIAuth 登入" }, 401, origin);
        }
        const body = await request.json();
        const eventId = String(body.event_id || "");
        if (!/^[a-f0-9]{20}$/.test(eventId) || typeof body.claimed !== "boolean") {
          return json({ error: "領取狀態資料格式錯誤" }, 400, origin);
        }
        const active = await readActiveGames();
        if (!active.games.some((game) => game.id === eventId)) {
          return json({ error: "這個限免活動已經過期或不存在" }, 404, origin);
        }
        const activeIds = new Set(active.games.map((game) => game.id));
        const claim = await writeManualClaim(eventId, body.claimed, activeIds, env);
        return json({ claim }, 200, origin);
      }

      return json({ error: "Not Found" }, 404, origin);
    } catch (error) {
      return json({ error: error.message || "Internal Server Error" }, 500, origin);
    }
  },
};
