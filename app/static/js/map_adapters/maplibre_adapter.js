const mapInstances = new WeakMap();

function library() {
  return globalThis.maplibregl || null;
}

function clear(container) {
  const instance = mapInstances.get(container);
  if (instance?.remove) instance.remove();
  mapInstances.delete(container);
  container.innerHTML = "";
}

function summary(input) {
  return [
    `entities=${input.entities.length}`,
    `tracks=${input.tracks.length}`,
    `detections=${input.detections.length}`,
    `events=${input.events.length}`,
  ].join(" | ");
}

function renderPlaceholder(container, input, status, title, message) {
  clear(container);
  container.innerHTML = `
    <div class="map-adapter-placeholder">
      <strong>${title}</strong>
      <span>${message}</span>
      <code>${summary(input)}</code>
    </div>
  `;
  return {
    engine: "maplibre",
    status,
    authoritative: false,
  };
}

export const maplibreAdapter = {
  id: "maplibre",
  label: "MapLibre 2D",

  dispose(container) {
    clear(container);
  },

  render(container, input) {
    const maplibregl = library();
    if (!maplibregl) {
      return renderPlaceholder(
        container,
        input,
        "unavailable",
        "MapLibre 2D unavailable",
        "未检测到本地 MapLibre GL JS 静态资源，当前不会影响 Canvas Tactical 地图。"
      );
    }
    return renderPlaceholder(
      container,
      input,
      "placeholder",
      "MapLibre 2D adapter ready",
      "MapLibre 库已加载；战术图层投影和符号绘制将在后续阶段接入。"
    );
  },
};
