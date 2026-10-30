// Offline-tolerant GPS queue, kept free of browser APIs so it can be tested under Node.
// Every function takes the queue and returns the new one; app.js stores the result.
"use strict";
const MAX_QUEUED_FIXES = 2000; // about a day at one fix per 45 s
const MAX_BATCH = 500;         // POST /api/telemetry limit

const RQueue = {
  MAX_QUEUED_FIXES,
  MAX_BATCH,
  add: (queue, fix) => [...queue, fix].slice(-MAX_QUEUED_FIXES), // when full, the oldest fix goes first
  nextBatch: (queue) => queue.slice(0, MAX_BATCH),
  removeSent: (queue, batch) => queue.slice(batch.length),
  // What to do with a batch after the server answers (status null means no answer at all).
  // 401 keeps the fixes: they become sendable once the device registers again.
  afterSend: (queue, batch, status) => (status === 202 || status === 422 ? RQueue.removeSent(queue, batch) : queue),
};

if (typeof module !== "undefined") module.exports = RQueue;
