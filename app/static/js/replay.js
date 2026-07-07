import { clamp } from "./api.js";

export function replayFrames(workbench) {
  const frames = workbench?.replay?.frames || [];
  return Array.isArray(frames) ? frames : [];
}

export function replayEvents(workbench) {
  const events = workbench?.events || workbench?.replay?.events || [];
  return Array.isArray(events) ? events : [];
}

export function timelineEnd(workbench) {
  const summaryEnd = Number(workbench?.replay?.summary?.timeline?.end || 0);
  const simEnd = Number(workbench?.simulation_time?.end || 0);
  const eventEnd = Math.max(0, ...replayEvents(workbench).map((event) => Number(event.time || 0)).filter(Number.isFinite));
  return Math.max(1, summaryEnd, simEnd, eventEnd, 600);
}

export function bearingDeg(a, b) {
  const lat1 = Number(a.lat) * Math.PI / 180;
  const lat2 = Number(b.lat) * Math.PI / 180;
  const dLon = (Number(b.lon) - Number(a.lon)) * Math.PI / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return ((Math.atan2(y, x) * 180 / Math.PI) + 360) % 360;
}

export function blendEntity(before, after, ratio, normalizeEntity) {
  if (!before) return normalizeEntity(after);
  if (!after) return normalizeEntity(before);
  const lat = Number(before.lat) + (Number(after.lat) - Number(before.lat)) * ratio;
  const lon = Number(before.lon) + (Number(after.lon) - Number(before.lon)) * ratio;
  const alt = Number(before.alt_m || 0) + (Number(after.alt_m || 0) - Number(before.alt_m || 0)) * ratio;
  return normalizeEntity({
    ...before,
    ...after,
    lat,
    lon,
    alt_m: alt,
    heading_deg: Math.abs(lat - Number(before.lat)) > 1e-8 || Math.abs(lon - Number(before.lon)) > 1e-8
      ? bearingDeg(before, { lat, lon })
      : Number(after.heading_deg ?? before.heading_deg ?? 0),
  });
}

export function replayFrameAt(workbench, timeValue, normalizeEntity) {
  const frames = replayFrames(workbench).slice().sort((a, b) => Number(a.sim_time || 0) - Number(b.sim_time || 0));
  if (!frames.length) return null;
  const time = Number(timeValue);
  if (!Number.isFinite(time) || time <= Number(frames[0].sim_time || 0)) return frames[0];
  const last = frames[frames.length - 1];
  if (time >= Number(last.sim_time || 0)) return last;
  for (let index = 1; index < frames.length; index += 1) {
    const before = frames[index - 1];
    const after = frames[index];
    const t0 = Number(before.sim_time || 0);
    const t1 = Number(after.sim_time || 0);
    if (time < t0 || time > t1) continue;
    const ratio = t1 > t0 ? clamp((time - t0) / (t1 - t0), 0, 1) : 0;
    const beforeById = new Map((before.entities || []).map((entity) => [entity.id, entity]));
    const afterById = new Map((after.entities || []).map((entity) => [entity.id, entity]));
    const ids = new Set([...beforeById.keys(), ...afterById.keys()]);
    const entities = [...ids].map((id) => blendEntity(beforeById.get(id), afterById.get(id), ratio, normalizeEntity)).filter(Boolean);
    return {
      ...after,
      source: `${after.source || "afsim-run-replay"}/interpolated`,
      sim_time: time,
      entity_count: entities.length,
      entities,
      events: [...new Set([...(before.events || []), ...(after.events || [])])].slice(-60),
    };
  }
  return last;
}
