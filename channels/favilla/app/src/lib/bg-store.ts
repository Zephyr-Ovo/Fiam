// Background image lives in IndexedDB, not in the localStorage config blob.
// A picked image is a multi-hundred-KB data URI; stuffing it into
// localStorage["favilla:config"] overflows the (small, on Android WebView)
// quota, saveConfig's catch then drops `bg`, and the background silently
// never changes. IndexedDB has no such practical limit here.

const DB_NAME = "favilla-bg";
const STORE_NAME = "kv";
const DB_VERSION = 1;
const KEY = "bg";

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME);
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function saveBgImage(dataUri: string): Promise<void> {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, "readwrite");
  tx.objectStore(STORE_NAME).put(dataUri, KEY);
  await new Promise<void>((res, rej) => {
    tx.oncomplete = () => res();
    tx.onerror = () => rej(tx.error);
  });
  db.close();
}

export async function loadBgImage(): Promise<string | null> {
  try {
    const db = await openDB();
    const tx = db.transaction(STORE_NAME, "readonly");
    const req = tx.objectStore(STORE_NAME).get(KEY);
    return await new Promise<string | null>((resolve) => {
      req.onsuccess = () => {
        db.close();
        resolve(typeof req.result === "string" ? req.result : null);
      };
      req.onerror = () => { db.close(); resolve(null); };
    });
  } catch {
    return null;
  }
}

export async function clearBgImage(): Promise<void> {
  try {
    const db = await openDB();
    const tx = db.transaction(STORE_NAME, "readwrite");
    tx.objectStore(STORE_NAME).delete(KEY);
    await new Promise<void>((res) => { tx.oncomplete = () => res(); });
    db.close();
  } catch {
    // ignore
  }
}
