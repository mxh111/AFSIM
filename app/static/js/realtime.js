import { applyAuthToken } from "./auth.js";

export function previewWebSocketUrl(params) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const query = applyAuthToken(new URLSearchParams(params));
  return `${protocol}://${window.location.host}/ws/afsim/preview?${query.toString()}`;
}

export function afsimRealtimeWebSocketUrl(runId, params = new URLSearchParams()) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const query = applyAuthToken(new URLSearchParams(params));
  return `${protocol}://${window.location.host}/ws/afsim/realtime/${encodeURIComponent(runId)}?${query.toString()}`;
}
