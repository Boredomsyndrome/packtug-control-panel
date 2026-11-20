import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const source = readFileSync(new URL('../src/lib/digitalTwin.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
})
const { placementToArmTarget, placementTargetIssues, placementOrientation } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
)
const cage = { x: 0.1, y: -0.1, z: 0.02, yaw: 0, width: 0.55, depth: 0.25, height: 0.4, clearance_m: 0.03 }
const box = { skuId: 'DEMO-BOX', x: 119, y: 160, z: 0, w: 152, d: 85, h: 110 }
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`)

test('demo targets use the front-face centre for every cage rotation', () => {
  for (const yaw of [0, 90, -90, 37, 180]) {
    for (const [x, y, z, expectedX, expectedY, expectedZ] of [
      [119, 160, 0, -0.080, 0.160, 0.055],
      [280, 160, 0, 0.081, 0.160, 0.055],
      [199, 160, 110, 0, 0.160, 0.165],
      [119, 65, 0, -0.080, 0.065, 0.055],
      [280, 65, 0, 0.081, 0.065, 0.055],
    ]) {
      const placement = { ...box, x, y, z }
      const target = placementToArmTarget(placement, { ...cage, yaw })
      const angle = yaw * Math.PI / 180
      const dx = target.x - cage.x, dy = target.y - cage.y
      // Inverse transform used by the backend, independently of the frontend.
      near(dx * Math.cos(angle) + dy * Math.sin(angle), expectedX)
      near(-dx * Math.sin(angle) + dy * Math.cos(angle), expectedY)
      near(target.z - cage.z, expectedZ)
      assert.deepEqual(placementTargetIssues(placement, cage), [])
    }
  }
})

test('box centre reference and taught width are honoured', () => {
  const target = placementToArmTarget(box, { ...cage, width: 0.6 }, 'center')
  near(target.x, -0.005)
  near(target.y, 0.1025)
  near(target.z, 0.075)
})

test('packing width, not taught width, centres the packing bin', () => {
  const target = placementToArmTarget(box, { ...cage, width: 0.6 }, 'front', 0.55)
  near(target.x, 0.02)
  near(target.y, 0.06)
  near(target.z, 0.075)
})

test('out-of-bounds boxes and outside targets are rejected; opening-plane targets are allowed', () => {
  assert.ok(placementTargetIssues(box, { ...cage, depth: 0.17 }).length)
  assert.ok(placementTargetIssues({ ...box, x: 500 }, cage).length)
  assert.ok(placementTargetIssues({ ...box, y: 230 }, cage).length)
  assert.deepEqual(placementTargetIssues({ ...box, y: 0 }, cage), [])
  assert.ok(placementTargetIssues({ ...box, y: -1 }, cage).some(issue => issue.includes('front face')))
})

test('level attitude follows the cage depth, including wrapped yaw', () => {
  for (const yaw of [-450, -90, 0, 37, 180, 450]) {
    const pose = placementOrientation({ ...cage, yaw })
    assert.equal(pose.roll, 0)
    assert.equal(pose.pitch, 0)
    near(Math.cos(pose.yaw * Math.PI / 180), -Math.sin(yaw * Math.PI / 180))
    near(Math.sin(pose.yaw * Math.PI / 180), Math.cos(yaw * Math.PI / 180))
  }
})

const moduleUrl = (source) => 'data:text/javascript;base64,' + Buffer.from(ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText).toString('base64')
const mockUrl = moduleUrl(readFileSync(new URL('../src/data/mock.ts', import.meta.url), 'utf8'))
const { CAGE_BIN, SKUS } = await import(mockUrl)
const demoSource = readFileSync(new URL('../src/data/demoStack.ts', import.meta.url), 'utf8')
const { DEMO_STACK_PLAN } = await import(moduleUrl(demoSource.replace("'./mock'", JSON.stringify(mockUrl))))
const { packBoxes } = await import(moduleUrl(readFileSync(new URL('../src/lib/packing.ts', import.meta.url), 'utf8')))

test('actual demo and general packing fit the recorded hardware dimensions', () => {
  assert.deepEqual(CAGE_BIN, { w: 550, d: 250, h: 400, maxWeight: 20 })
  assert.deepEqual(DEMO_STACK_PLAN.bin, CAGE_BIN)
  const packed = packBoxes(SKUS.map(sku => ({ skuId: sku.id, name: sku.name, ...sku.size, weight: sku.weight })), CAGE_BIN)
  for (const plan of [DEMO_STACK_PLAN, packed]) {
    for (const bin of plan.cages) {
      for (const p of bin.placements) {
        for (const [position, size, limit] of [[p.x, p.w, 550], [p.y, p.d, 250], [p.z, p.h, 400]]) {
          assert.ok(position >= 0 && position + size <= limit)
        }
      }
    }
  }
  for (const p of DEMO_STACK_PLAN.cages[0].placements) assert.deepEqual(placementTargetIssues(p, cage), [])
})
