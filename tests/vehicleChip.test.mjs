// v3.7 CS-B4 unit test: topbar chip logic vs mocked fleet/health probes.
// Run: node --experimental-strip-types tests/vehicleChip.test.mjs
// (or plain `node tests/vehicleChip.test.mjs` on node >= 23 where type
// stripping is on by default — repo node v26 runs it directly.)
import test from 'node:test'
import assert from 'node:assert/strict'
import { armChip, vehicleChip } from '../src/lib/vehicleStatus.ts'

test('fleet endpoint answering with robots[] => LIVE (the old chip wrongly said mock)', () => {
  assert.deepEqual(vehicleChip(true), { label: 'AGV DATA · live', tone: 'green' })
})

test('fleet endpoint unreachable => offline, red', () => {
  assert.deepEqual(vehicleChip(false), { label: 'AGV DATA · offline', tone: 'red' })
})

test('arm bridge reports mock => amber mock chip (separate from AGV chip)', () => {
  assert.deepEqual(armChip({ status: 'ok', mock: true }), { label: 'bridge · mock', tone: 'amber' })
  assert.deepEqual(armChip({ status: 'ok', mock: false }), { label: 'bridge · live', tone: 'green' })
})

test('bridge down/null => offline red; never crashes on garbage', () => {
  assert.deepEqual(armChip(null), { label: 'bridge · offline', tone: 'red' })
  assert.deepEqual(armChip({}), { label: 'bridge · offline', tone: 'red' })
  assert.deepEqual(armChip({ status: 'error' }), { label: 'bridge · offline', tone: 'red' })
})
