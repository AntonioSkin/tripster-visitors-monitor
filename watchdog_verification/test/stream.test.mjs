import test from 'node:test';
import assert from 'node:assert/strict';
import {jsonStream} from '../json-stream.mjs';

test('extracts successive pretty JSON events across arbitrary chunks', () => {
  const values = [], push = jsonStream(v => values.push(v));
  const raw = 'Connected\n' + JSON.stringify({logs:[{message:['escaped " quote and { braces }']}]},null,2) + '\n' + JSON.stringify({event:{cron:'* * * * *'}});
  for (const char of raw) push(char);
  assert.equal(values.length, 2);
  assert.equal(values[0].logs[0].message[0], 'escaped " quote and { braces }');
  assert.equal(values[1].event.cron, '* * * * *');
});

test('does not forward incomplete JSON or text as an event', () => {
  const values = [], push = jsonStream(v => values.push(v));
  push('Waiting for events\n{"logs":[');
  assert.deepEqual(values, []);
});
