export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000";
// Optional: mirror of the backend's ADMIN_API_KEY (protects "Clear data").
const ADMIN_KEY = process.env.NEXT_PUBLIC_ADMIN_KEY || "";
const TIMEOUT_MS = 90000;

async function request(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let res;
  try {
    res = await fetch(path, {
      ...options,
      cache: "no-store",
      credentials: "same-origin",
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...(ADMIN_KEY ? { "x-api-key": ADMIN_KEY } : {}),
        ...(options.headers || {}),
      },
      signal: controller.signal,
    });
  } catch (err) {
    throw new Error(
      err?.name === "AbortError"
        ? `The backend at ${API_URL} took too long to respond.`
        : `Cannot reach the Flask backend at ${API_URL}. Make sure it is running.`
    );
  } finally {
    clearTimeout(timer);
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail ? `: ${data.detail}` : "";
    const err = new Error((data.error || `Request failed (${res.status})`) + detail);
    err.fields = data.fields || {};
    err.status = res.status;
    throw err;
  }
  return data;
}

export const predict = (payload) =>
  request("/api/predict", {
    method: "POST",
    body: JSON.stringify(payload),
  });
export const getStats = () => request("/api/stats");
export const getTransactions = (limit = 15) => request(`/api/transactions?limit=${limit}`);
export const getModelInfo = () => request("/api/model-info");
export const getOptions = () => request("/api/options");
export const clearTransactions = () => request("/api/transactions", { method: "DELETE" });
export const register = (payload) =>
  request("/api/auth/register", { method: "POST", body: JSON.stringify(payload) });
export const login = (payload) =>
  request("/api/auth/login", { method: "POST", body: JSON.stringify(payload) });
export const getCurrentUser = () => request("/api/auth/me");
