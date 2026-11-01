(() => {
  const config = window.FG_CONFIG || {};
  const apiBase = String(config.apiBase || "").replace(/\/$/, "");
  const authOrigin = String(config.authOrigin || "").replace(/\/$/, "");
  const clientId = String(config.clientId || "");
  const redirectUri = window.location.origin + window.location.pathname;
  let accessToken = "";
  let games = [];
  let manualClaims = {};
  let activeFilter = "all";

  const $ = (id) => document.getElementById(id);
  const elements = {
    list: $("game-list"),
    empty: $("empty-state"),
    error: $("error-message"),
    count: $("result-count"),
    updated: $("updated-at"),
    total: $("total-count"),
    unclaimed: $("unclaimed-count"),
    search: $("search-input"),
    platform: $("platform-filter"),
    login: $("login-button"),
    logout: $("logout-button"),
    refresh: $("refresh-button"),
  };

  function showError(message) {
    elements.error.textContent = message;
    elements.error.hidden = false;
  }

  function clearError() {
    elements.error.hidden = true;
    elements.error.textContent = "";
  }

  function randomString(length = 64) {
    const bytes = new Uint8Array(length);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  async function challengeFor(verifier) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
    const bytes = new Uint8Array(digest);
    let binary = "";
    bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  async function login() {
    if (!clientId) {
      showError("CHIAuth OAuth Client 尚未設定，請先完成網站授權設定。");
      return;
    }
    const state = randomString(24);
    const verifier = randomString(48);
    const challenge = await challengeFor(verifier);
    sessionStorage.setItem("fg_oauth_state", state);
    sessionStorage.setItem("fg_oauth_verifier", verifier);
    const url = new URL("/api/authorize", authOrigin);
    url.search = new URLSearchParams({
      response_type: "code",
      client_id: clientId,
      redirect_uri: redirectUri,
      state,
      code_challenge_method: "S256",
      code_challenge: challenge,
    }).toString();
    window.location.assign(url.toString());
  }

  async function handleCallback() {
    const url = new URL(window.location.href);
    const code = url.searchParams.get("code");
    const returnedState = url.searchParams.get("state");
    const oauthError = url.searchParams.get("error");
    if (oauthError) {
      showError("CHIAuth 登入未完成：" + (url.searchParams.get("error_description") || oauthError));
      history.replaceState({}, "", redirectUri);
      return;
    }
    if (!code) return;

    const expectedState = sessionStorage.getItem("fg_oauth_state");
    const verifier = sessionStorage.getItem("fg_oauth_verifier");
    sessionStorage.removeItem("fg_oauth_state");
    sessionStorage.removeItem("fg_oauth_verifier");
    history.replaceState({}, "", redirectUri);

    if (!expectedState || returnedState !== expectedState || !verifier) {
      showError("登入驗證狀態不符，請重新登入。");
      return;
    }

    try {
      const response = await fetch(new URL("/api/token", authOrigin), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: clientId,
          code,
          redirect_uri: redirectUri,
          code_verifier: verifier,
        }),
      });
      const payload = await response.json();
      if (!response.ok || !payload.access_token) {
        throw new Error(payload.error_description || payload.error || "Token 交換失敗");
      }
      accessToken = payload.access_token;
      updateAuthControls();
    } catch (error) {
      showError("CHIAuth 登入失敗：" + error.message);
    }
  }

  function updateAuthControls() {
    elements.login.hidden = Boolean(accessToken);
    elements.logout.hidden = !accessToken;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (accessToken) headers.set("Authorization", "Bearer " + accessToken);
    if (options.body) headers.set("Content-Type", "application/json");
    const response = await fetch(apiBase + path, { ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) {
        accessToken = "";
        updateAuthControls();
      }
      throw new Error(payload.error || "API 請求失敗（" + response.status + "）");
    }
    return payload;
  }

  function isClaimed(game) {
    if (Object.prototype.hasOwnProperty.call(manualClaims, game.id)) {
      return Boolean(manualClaims[game.id].claimed);
    }
    return ["已領取", "已擁有"].includes(game.claim_status);
  }

  function formatDeadline(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "期限未確認";
    return new Intl.DateTimeFormat("zh-TW", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
      hour12: false, timeZone: "Asia/Taipei",
    }).format(date);
  }

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function renderCard(game) {
    const claimed = isClaimed(game);
    const card = node("article", "game-card");
    const image = node("img", "game-image");
    image.alt = "";
    image.loading = "lazy";
    image.src = game.image || "";
    image.onerror = () => { image.hidden = true; };
    card.append(image);

    const content = node("div", "game-content");
    const top = node("div", "game-topline");
    top.append(
      node("span", "platform-label", game.platform || "其他平台"),
      node("span", "status-label" + (claimed ? " claimed" : ""), claimed ? "已領取" : "尚未領取")
    );
    content.append(top);
    content.append(node("h2", "game-title", game.name || "未命名遊戲"));
    if (game.brief) content.append(node("p", "game-brief", game.brief));

    const bottom = node("div", "game-bottom");
    bottom.append(node("span", "deadline", "領取至 " + formatDeadline(game.deadline)));
    const actions = node("div", "card-actions");
    const link = node("a", "store-link", "前往商店");
    link.href = game.link;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    actions.append(link);

    const toggle = node("button", "claim-toggle", claimed ? "取消已領取" : "標記已領取");
    toggle.type = "button";
    toggle.disabled = !accessToken;
    toggle.title = accessToken ? "" : "登入後才能修改領取狀態";
    toggle.addEventListener("click", async () => {
      toggle.disabled = true;
      try {
        const result = await api("/api/claim", {
          method: "POST",
          body: JSON.stringify({ event_id: game.id, claimed: !claimed }),
        });
        manualClaims[game.id] = result.claim;
        render();
      } catch (error) {
        showError("更新領取狀態失敗：" + error.message);
        toggle.disabled = false;
      }
    });
    actions.append(toggle);
    bottom.append(actions);
    content.append(bottom);
    card.append(content);
    return card;
  }

  function render() {
    const query = elements.search.value.trim().toLocaleLowerCase("zh-TW");
    const platform = elements.platform.value;
    const filtered = games.filter((game) => {
      const claimed = isClaimed(game);
      const matchesStatus = activeFilter === "all"
        || (activeFilter === "claimed" && claimed)
        || (activeFilter === "unclaimed" && !claimed);
      const matchesPlatform = platform === "all" || game.platform === platform;
      const matchesQuery = !query || String(game.name || "").toLocaleLowerCase("zh-TW").includes(query);
      return matchesStatus && matchesPlatform && matchesQuery;
    });

    elements.list.replaceChildren(...filtered.map(renderCard));
    elements.empty.hidden = filtered.length > 0;
    elements.count.textContent = "顯示 " + filtered.length + " 款遊戲";
    elements.total.textContent = String(games.length);
    elements.unclaimed.textContent = String(games.filter((game) => !isClaimed(game)).length);
  }

  async function loadGames() {
    clearError();
    elements.refresh.disabled = true;
    elements.list.replaceChildren(node("div", "loading-state", "正在載入限免清單…"));
    try {
      const payload = await api("/api/games");
      games = Array.isArray(payload.games) ? payload.games : [];
      manualClaims = payload.manual_claims && typeof payload.manual_claims === "object"
        ? payload.manual_claims : {};
      elements.updated.textContent = payload.updated_at
        ? "資料更新時間：" + new Intl.DateTimeFormat("zh-TW", {
            dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Taipei",
          }).format(new Date(payload.updated_at))
        : "";
      render();
    } catch (error) {
      elements.list.replaceChildren();
      showError("無法取得限免清單：" + error.message);
      elements.count.textContent = "載入失敗";
    } finally {
      elements.refresh.disabled = false;
    }
  }

  function setupTheme() {
    const root = document.documentElement;
    const button = $("theme-toggle");
    const icon = $("theme-icon");
    const saved = (() => { try { return localStorage.getItem("chi-theme"); } catch { return null; } })();
    const systemDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const apply = (theme) => {
      if (theme === "dark") root.setAttribute("data-chi-theme", "dark");
      else root.removeAttribute("data-chi-theme");
      icon.setAttribute("href", "https://cdn.jsdelivr.net/gh/47tsai/chi-design-system@main/icons.svg#" + (theme === "dark" ? "sun" : "moon"));
      button.setAttribute("aria-label", theme === "dark" ? "切換為淺色主題" : "切換為深色主題");
    };
    apply(saved || (systemDark ? "dark" : "light"));
    button.addEventListener("click", () => {
      const next = root.getAttribute("data-chi-theme") === "dark" ? "light" : "dark";
      apply(next);
      try { localStorage.setItem("chi-theme", next); } catch {}
    });
  }

  elements.login.addEventListener("click", login);
  elements.logout.addEventListener("click", () => {
    accessToken = "";
    updateAuthControls();
    render();
  });
  elements.refresh.addEventListener("click", loadGames);
  elements.search.addEventListener("input", render);
  elements.platform.addEventListener("change", render);
  document.querySelectorAll("[data-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      activeFilter = button.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach((item) => {
        item.classList.toggle("is-active", item === button);
      });
      render();
    });
  });

  setupTheme();
  updateAuthControls();
  handleCallback().finally(loadGames);
})();
