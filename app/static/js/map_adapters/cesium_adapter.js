const viewerInstances = new WeakMap();

function library() {
  return globalThis.Cesium || null;
}

function clear(container) {
  const instance = viewerInstances.get(container);
  if (instance?.destroy && !instance.isDestroyed?.()) instance.destroy();
  viewerInstances.delete(container);
  container.innerHTML = "";
}

function summary(input) {
  return [
    `entities=${input.entities.length}`,
    `tracks=${input.tracks.length}`,
    `communications=${input.communications.length}`,
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
    engine: "cesium",
    status,
    authoritative: false,
  };
}

export const cesiumAdapter = {
  id: "cesium",
  label: "Cesium 3D",

  dispose(container) {
    clear(container);
  },

  render(container, input) {
    const cesium = library();
    if (!cesium) {
      return renderPlaceholder(
        container,
        input,
        "unavailable",
        "Cesium 3D unavailable",
        "未检测到本地 Cesium 静态资源，当前不会影响 Canvas Tactical 地图。"
      );
    }
    return renderPlaceholder(
      container,
      input,
      "placeholder",
      "Cesium 3D adapter ready",
      "Cesium 库已加载；三维地球、地形和军标实体将在后续阶段接入。"
    );
  },
};
