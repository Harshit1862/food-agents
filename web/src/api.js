// Everything the browser says to the gateway goes through here.
// Every request carries the customer's Keycloak token; the gateway refuses requests without one.

import { freshToken } from "./auth.js";

export const GATEWAY = import.meta.env.VITE_GATEWAY_URL || "http://localhost:8000";

async function authHeaders(extra = {}) {
  return { ...extra, Authorization: `Bearer ${await freshToken()}` };
}

// The gateway explains refusals as {"error": "..."}: show that text instead of a bare status number.
async function fail(res, path) {
  let message = `${path} answered ${res.status}`;
  try {
    const body = await res.json();
    if (body.error) message = body.error;
  } catch {
    /* not JSON: keep the status message */
  }
  throw new Error(message);
}

export async function getJSON(path) {
  const res = await fetch(GATEWAY + path, { headers: await authHeaders() });
  if (!res.ok) await fail(res, path);
  return res.json();
}

export async function postJSON(path, body) {
  const res = await fetch(GATEWAY + path, {
    method: "POST",
    headers: await authHeaders({ "content-type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) await fail(res, path);
  return res.json();
}

// POST a request and read the Server-Sent-Events stream that comes back.
// (The browser's EventSource only does GET, so we read the stream by hand.)
// onEvent(name, data) is called once per event, as it arrives.
export async function streamSSE(path, body, onEvent) {
  const res = await fetch(GATEWAY + path, {
    method: "POST",
    headers: await authHeaders({ "content-type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) await fail(res, path);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let name = "message";
      const data = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) name = line.slice(7);
        else if (line.startsWith("data: ")) data.push(line.slice(6));
        // lines starting with ":" are keep-alive comments: ignored
      }
      if (data.length) onEvent(name, JSON.parse(data.join("\n")));
    }
  }
}