import { disposeOperationalMap, renderOperationalMap } from "../map_renderer.js";

export const canvasAdapter = {
  id: "canvas",
  label: "Canvas Tactical",
  available: true,

  dispose(container) {
    disposeOperationalMap(container);
  },

  render(container, input, options = {}) {
    const scene = {
      ...input.scene,
      platforms: input.platforms,
      tracks: input.tracks,
      detections: input.detections,
      communications: input.communications,
      events: input.events,
    };
    const frame = input.frame
      ? {
          ...input.frame,
          entities: input.entities,
          events: input.frame_event_ids,
        }
      : null;
    renderOperationalMap(container, scene, {
      ...options,
      frame,
    });
    return {
      engine: this.id,
      status: "available",
      authoritative: Boolean(frame?.authoritative),
    };
  },
};
