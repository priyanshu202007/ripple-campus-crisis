const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";
export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL
).replace(/\/+$/, "");

async function request(path, { method = "GET", body, signal } = {}) {
  const hasBody = body !== undefined;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: hasBody ? { "Content-Type": "application/json" } : undefined,
    body: hasBody ? JSON.stringify(body) : undefined,
    signal,
  });
  const responseText = await response.text();

  if (!response.ok) {
    let detail = responseText;

    try {
      const errorBody = JSON.parse(responseText);
      detail = errorBody.detail ?? responseText;
    } catch {
      // Keep the response text when the server's error body is not JSON.
    }

    const suffix = detail ? `: ${detail}` : "";
    throw new Error(`${method} ${path} failed (${response.status})${suffix}`);
  }

  try {
    return JSON.parse(responseText);
  } catch (error) {
    const reason = error instanceof Error ? ` ${error.message}` : "";
    throw new Error(`${method} ${path} returned invalid JSON.${reason}`);
  }
}

function get(path, options) {
  return request(path, options);
}

function post(path, body, { optionalBody = false } = {}) {
  if (body === undefined && !optionalBody) {
    throw new TypeError(`POST ${path} requires a JSON body.`);
  }

  return request(path, { method: "POST", body });
}

export function getHealth(options) {
  return get("/api/health", options);
}

export function getDatasetSummary() {
  return get("/api/dataset/summary");
}

export function getIncident() {
  return get("/api/incident");
}

export function getImpacts() {
  return get("/api/impacts");
}

export function scheduleRepairs(body) {
  return post("/api/repairs/schedule", body);
}

export function scheduleActivities(body) {
  return post("/api/activities/schedule", body);
}

export function validate(body) {
  return post("/api/validate", body, { optionalBody: true });
}

export function compareStrategies(body) {
  return post("/api/strategies/compare", body);
}

export function getBuildings() {
  return get("/api/buildings");
}

export function getRooms() {
  return get("/api/rooms");
}

export function getServices() {
  return get("/api/services");
}

export function getActivities() {
  return get("/api/activities");
}

export function getWorkRequests() {
  return get("/api/work-requests");
}
