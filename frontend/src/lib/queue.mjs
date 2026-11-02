// Offline-tolerant GPS queue, kept free of browser APIs so it can be tested under Node. Every function
// takes the queue and returns the new one; the driver screen stores the result.
export const MAX_QUEUED_FIXES = 2000; // about 16 hours without signal, at one fix per 30 s
export const MAX_BATCH = 500;         // POST /api/telemetry limit

export const add = (queue, fix) => [...queue, fix].slice(-MAX_QUEUED_FIXES); // when full, the oldest fix goes first
export const nextBatch = (queue) => queue.slice(0, MAX_BATCH);
export const removeSent = (queue, batch) => queue.slice(batch.length);
// What to do with a batch after the server answers (status null means no answer at all).
// 401 keeps the fixes: they become sendable once the device registers again.
export const afterSend = (queue, batch, status) => (status === 202 || status === 422 ? removeSent(queue, batch) : queue);
