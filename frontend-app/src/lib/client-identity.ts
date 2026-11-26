const STORAGE_KEY = "operations_copilot_client_id";
const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function readOrCreateId(): string {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && UUID_V4.test(saved)) return saved.toLowerCase();
    const id = window.crypto.randomUUID();
    window.localStorage.setItem(STORAGE_KEY, id);
    return id;
  } catch {
    throw new Error(
      "Browser storage is required to identify this browser. Allow local storage and reload the page.",
    );
  }
}

/** Called only from browser API requests, never during server rendering. */
export async function browserClientId(): Promise<string> {
  // Serialize first creation when two tabs open together on a fresh browser profile.
  if (navigator.locks)
    return navigator.locks.request(STORAGE_KEY, readOrCreateId);
  return readOrCreateId();
}
