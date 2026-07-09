const TOKEN_STORAGE_KEY = "afsimApiToken";
const DEFAULT_LOCAL_TOKEN = "operator-token";

export function apiToken() {
  try {
    const existing = window.localStorage.getItem(TOKEN_STORAGE_KEY);
    if (existing) return existing;
    window.localStorage.setItem(TOKEN_STORAGE_KEY, DEFAULT_LOCAL_TOKEN);
    return DEFAULT_LOCAL_TOKEN;
  } catch {
    return DEFAULT_LOCAL_TOKEN;
  }
}

export function authHeaders() {
  const token = apiToken();
  return token ? { "X-AFSIM-Token": token } : {};
}

export function applyAuthToken(params) {
  const token = apiToken();
  if (token) params.set("token", token);
  return params;
}
