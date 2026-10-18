// Browser credentials are scoped to this tab's session, never bundled or put in URLs.
const STORAGE_KEY = "atlas.api-credential";
const CHANGE_EVENT = "atlas:credential-change";

export function getApiCredential(): string {
  if (typeof window !== "undefined") {
    try {
      const stored = window.sessionStorage.getItem(STORAGE_KEY);
      if (stored) return stored;
    } catch {
      // Ignore sessionStorage exceptions
    }
  }
  if (typeof process !== "undefined" && process.env.NEXT_PUBLIC_ATLAS_API_KEY) {
    return process.env.NEXT_PUBLIC_ATLAS_API_KEY;
  }
  return "";
}

export function setApiCredential(value: string): void {
  const credential = value.trim();
  // RFC 6455 subprotocol token syntax. One credential works for HTTP and sockets.
  if (credential && !/^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(credential)) {
    throw new Error("Use an API key without spaces or unsupported characters.");
  }
  if (typeof window === "undefined") return;
  if (credential) window.sessionStorage.setItem(STORAGE_KEY, credential);
  else window.sessionStorage.removeItem(STORAGE_KEY);
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function authHeaders(initial?: HeadersInit): Record<string, string> {
  const headers = new Headers(initial);
  const credential = getApiCredential();
  if (credential) {
    if (!headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${credential}`);
    }
    if (!headers.has("X-Atlas-API-Key")) {
      headers.set("X-Atlas-API-Key", credential);
    }
  }
  return Object.fromEntries(headers.entries());
}

export function websocketProtocols(): string[] {
  const credential = getApiCredential();
  return credential ? ["atlas", `atlas.api-key.${credential}`] : ["atlas"];
}

export function subscribeCredentialChange(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(CHANGE_EVENT, listener);
  return () => window.removeEventListener(CHANGE_EVENT, listener);
}
