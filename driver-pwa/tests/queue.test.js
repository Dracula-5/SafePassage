// Run: node --test driver-pwa/tests/queue.test.js   (also run by backend/tests/senses/test_driver_pwa.py)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const q = require("../queue.js");

const fixes = (n) => Array.from({ length: n }, (_, i) => ({ recorded_at: i }));

test("a dead zone keeps fixes in order until the signal returns", () => {
  let queue = [];
  for (const fix of fixes(5)) queue = q.add(queue, fix);
  const batch = q.nextBatch(queue);
  queue = q.afterSend(queue, batch, null); // no network: nothing lost
  assert.equal(queue.length, 5);
  queue = q.afterSend(queue, batch, 202);
  assert.deepEqual(queue, []);
});

test("a very long outage drops the oldest fixes, never the newest", () => {
  let queue = fixes(q.MAX_QUEUED_FIXES);
  queue = q.add(queue, { recorded_at: "latest" });
  assert.equal(queue.length, q.MAX_QUEUED_FIXES);
  assert.equal(queue.at(-1).recorded_at, "latest");
  assert.equal(queue[0].recorded_at, 1);
});

test("batches respect the API limit and the rest waits for the next flush", () => {
  const queue = fixes(q.MAX_BATCH + 3);
  const batch = q.nextBatch(queue);
  assert.equal(batch.length, q.MAX_BATCH);
  assert.equal(q.afterSend(queue, batch, 202).length, 3);
});

test("a fix arriving during an upload is not lost when the upload succeeds", () => {
  let queue = fixes(2);
  const batch = q.nextBatch(queue);
  queue = q.add(queue, { recorded_at: "during" });
  queue = q.afterSend(queue, batch, 202);
  assert.deepEqual(queue, [{ recorded_at: "during" }]);
});

test("fixes the server rejects as too old are dropped so they cannot block the queue", () => {
  const queue = fixes(3);
  assert.deepEqual(q.afterSend(queue, q.nextBatch(queue), 422), []);
});

test("server errors and an unknown device keep the fixes for a retry", () => {
  const queue = fixes(3);
  assert.equal(q.afterSend(queue, q.nextBatch(queue), 503).length, 3);
  assert.equal(q.afterSend(queue, q.nextBatch(queue), 401).length, 3);
});
