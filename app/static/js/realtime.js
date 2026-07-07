export function previewWebSocketUrl(params) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/ws/afsim/preview?${params.toString()}`;
}
