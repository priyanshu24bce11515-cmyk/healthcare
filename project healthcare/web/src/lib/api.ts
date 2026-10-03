const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

export interface ApiErrorBody {
  code: string;
  message: string;
}

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

function isEnvelope(body: unknown): body is { data: unknown; meta: unknown } {
  return typeof body === "object" && body !== null && !Array.isArray(body) && "data" in body && "meta" in body;
}

// The header that establishes identity varies by auth mode — demo mode sends
// `x-demo-principal`, real mode sends `Authorization: Bearer <token>` (see
// lib/auth.tsx). request() stays agnostic to which: callers (via useAuth())
// hand it whatever headers apply, already built.
async function request<T>(path: string, authHeaders: Record<string, string>, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders,
      ...init?.headers,
    },
  });

  const raw = await res.text();
  let body: unknown = undefined;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      // not JSON — leave body undefined, fall back to raw text below
    }
  }

  if (!res.ok) {
    // Enveloped error shape: {"error": {"code","message"}, "meta": {...}}
    if (typeof body === "object" && body !== null && "error" in body) {
      const err = (body as { error: ApiErrorBody | string }).error;
      if (typeof err === "object" && err !== null && "message" in err) {
        throw new ApiError(err.code ?? "UNKNOWN", err.message, res.status);
      }
      if (typeof err === "string") {
        throw new ApiError("UNKNOWN", err, res.status);
      }
    }
    // body is undefined here whenever raw wasn't valid JSON — e.g. the API
    // host doesn't actually exist behind this origin (a static-only deploy)
    // and the request landed on a fallback/error HTML page instead of a real
    // API response. Never surface that raw markup to the user.
    const looksLikeMarkup = /^\s*<(!doctype|html)/i.test(raw);
    const message = body === undefined && (looksLikeMarkup || !raw) ? `No response from the API (HTTP ${res.status})` : raw;
    throw new ApiError("UNKNOWN", message || res.statusText, res.status);
  }

  if (res.status === 204) return undefined as T;
  // Enveloped success shape: {"data": ..., "meta": {...}} — unwrap to `data`.
  // Falls back to the raw body for any route not yet migrated to the envelope.
  return (isEnvelope(body) ? body.data : body) as T;
}

export const api = {
  get: <T,>(path: string, authHeaders: Record<string, string>) => request<T>(path, authHeaders),
  post: <T,>(path: string, authHeaders: Record<string, string>, body?: unknown) =>
    request<T>(path, authHeaders, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T,>(path: string, authHeaders: Record<string, string>, body?: unknown) =>
    request<T>(path, authHeaders, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
};
