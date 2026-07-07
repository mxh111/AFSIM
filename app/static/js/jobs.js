import { api } from "./api.js";

export function jobWebSocketUrl(jobId) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/ws/afsim/jobs/${encodeURIComponent(jobId)}`;
}

export async function submitDemoJob({ demoName, inputFile, timeoutSeconds }) {
  return api("/api/afsim/run/jobs", {
    method: "POST",
    body: JSON.stringify({
      demo_name: demoName,
      input_file: inputFile || null,
      timeout_seconds: timeoutSeconds,
    }),
  });
}

export async function submitGeneratedJob({ scenarioId, timeoutSeconds }) {
  return api(`/api/afsim/designs/${encodeURIComponent(scenarioId)}/run/jobs`, {
    method: "POST",
    body: JSON.stringify({ timeout_seconds: timeoutSeconds }),
  });
}

export async function getJob(jobId) {
  return api(`/api/afsim/jobs/${encodeURIComponent(jobId)}`);
}

export async function cancelJob(jobId) {
  return api(`/api/afsim/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
}

export async function getJobReplay(jobId) {
  return api(`/api/afsim/jobs/${encodeURIComponent(jobId)}/replay`);
}
