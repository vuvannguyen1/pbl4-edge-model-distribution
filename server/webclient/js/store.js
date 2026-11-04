// Kho model trên điện thoại (IndexedDB của trình duyệt), tương đương data/client/<device_id>/
// của client Python: model tải một lần, lần sau mở link là dùng ngay, không tải lại.
// Tách 2 store: "meta" (nhỏ, liệt kê nhanh) và "files" (byte model).

const DB_NAME = "pbl4-mobile";
const DB_VERSION = 1;

let dbPromise = null;

function openDb() {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        req.result.createObjectStore("meta", { keyPath: "variant_id" });
        req.result.createObjectStore("files", { keyPath: "variant_id" });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return dbPromise;
}

async function run(stores, mode, fn) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(stores, mode);
    let result;
    const req = fn(tx);
    if (req) req.onsuccess = () => (result = req.result);
    tx.oncomplete = () => resolve(result);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("Giao dịch IndexedDB bị huỷ (có thể hết dung lượng)."));
  });
}

export async function listModels() {
  const rows = await run(["meta"], "readonly", (tx) => tx.objectStore("meta").getAll());
  return (rows || []).sort((a, b) => a.variant_id.localeCompare(b.variant_id));
}

export async function getMeta(variantId) {
  return run(["meta"], "readonly", (tx) => tx.objectStore("meta").get(variantId));
}

// meta: {variant_id, variant (khối variant của /api/request), size_bytes, sha256, saved_at}
export async function saveModel(meta, bytes) {
  await run(["meta", "files"], "readwrite", (tx) => {
    tx.objectStore("files").put({ variant_id: meta.variant_id, bytes });
    tx.objectStore("meta").put(meta);
  });
  // xin trình duyệt không tự xoá kho khi thiếu dung lượng (không bắt buộc)
  navigator.storage?.persist?.().catch(() => {});
}

export async function loadBytes(variantId) {
  const row = await run(["files"], "readonly", (tx) => tx.objectStore("files").get(variantId));
  return row ? row.bytes : null;
}

export async function deleteModel(variantId) {
  await run(["meta", "files"], "readwrite", (tx) => {
    tx.objectStore("files").delete(variantId);
    tx.objectStore("meta").delete(variantId);
  });
}

export async function freeStorageMb() {
  try {
    const { quota, usage } = await navigator.storage.estimate();
    return quota ? Math.max(0, (quota - usage) / 1048576) : null;
  } catch {
    return null;
  }
}
