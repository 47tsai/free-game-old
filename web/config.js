// Static frontend hosted on GitHub Pages; API remains on Cloudflare Workers.
// Keep API and OAuth origins independent from the frontend hosting provider.
window.FG_CONFIG = Object.freeze({
  apiBase: "https://fg-api.chi.qzz.io",
  authOrigin: "https://auth.chi.qzz.io",
  clientId: "MN7nps4p8bc9oFVHwR1TdZejZyaUmXLt"
});
