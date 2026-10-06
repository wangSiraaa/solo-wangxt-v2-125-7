/**
 * 情景快照 E2E（需 Playwright Chromium 与本前后端已启动）：
 * 保存 -> 改风向/分辨率 -> 恢复（重新计算）-> 重命名 -> 删除，
 * 并校验删除不影响源/气象记录。
 */
import { chromium } from 'playwright'

function assert(cond: unknown, msg: string) {
  if (!cond) throw new Error('断言失败: ' + msg)
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } })
const errors: string[] = []
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))

await page.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
await page.waitForTimeout(2500)

// 1. 保存快照（默认输入：风向 270°, nx=121）
await page.locator('[data-test="snapshot-name"]').fill('E2E 课堂快照')
await page.locator('[data-test="snapshot-save"]').click()
await page.waitForTimeout(600)
assert((await page.locator('.snap-item').count()) >= 1, '快照应出现在列表中')
console.log('1) 快照已保存并列出的条数:', await page.locator('.snap-item').count())

// 2. 修改风向 270 -> 90、分辨率 nx 121 -> 61 并重新运行
await page.locator('[data-test="windfrom"]').fill('90')
await page.locator('[data-test="nx"]').fill('61')
await page.getByRole('button', { name: '运行烟羽计算' }).click()
await page.waitForTimeout(1200)
const changedInfo = (await page.locator('.mapinfo').textContent()) || ''
assert(changedInfo.includes('61×'), '修改后地图应为 61 列网格')
console.log('2) 修改后 mapinfo:', changedInfo.trim().slice(0, 60))

// 3. 恢复快照 -> 表单回到原输入，重新计算后风向图对应 270°（输运方位 90°）
const snapId: number = await page.evaluate(async () => {
  const r = await fetch('/api/snapshots').then((x) => x.json())
  return r.snapshots[r.snapshots.length - 1].id
})
await page.locator(`[data-test="snapshot-restore-${snapId}"]`).click()
await page.waitForTimeout(1500)
const windInput = await page.locator('[data-test="windfrom"]').inputValue()
const nxInput = await page.locator('[data-test="nx"]').inputValue()
assert(windInput === '270', `恢复后风向应为 270，实际 ${windInput}`)
assert(nxInput === '121', `恢复后 nx 应为 121，实际 ${nxInput}`)
const restoredInfo = (await page.locator('.mapinfo').textContent()) || ''
assert(restoredInfo.includes('121×'), '恢复后地图应重新按 121 列网格计算')
// 风矢要素来自新响应的采样角点：270° 来风 -> 箭头向东
const windGeom = await page.evaluate(() => {
  const map = (window as any).__map
  const feats = map.getSource('wind')._data.features
  const line = feats.find((f: any) => f.geometry.type === 'LineString')
  const [a, b] = line.geometry.coordinates
  return { dLon: b[0] - a[0], dLat: b[1] - a[1] }
})
assert(windGeom.dLon > 0 && Math.abs(windGeom.dLat) < 1e-6, '恢复后风矢应指向正东')
console.log('3) 恢复后表单 windFrom=270, nx=121；风矢重新计算且指向正东')

// 4. 重命名
await page
  .locator(`.snap-item:has([data-test="snapshot-restore-${snapId}"]) button`, { hasText: '重命名' })
  .first()
  .click()
await page.locator('.snap-item input.num').fill('E2E 重命名后')
await page.getByRole('button', { name: '确定' }).click()
await page.waitForTimeout(600)
const renamed = await page.locator('.snap-name').first().textContent()
assert(renamed === 'E2E 重命名后', `重命名未生效: ${renamed}`)
console.log('4) 重命名成功:', renamed)

// 5. 删除（自动接受 confirm），源/气象记录数不变
const before = await page.evaluate(async () => ({
  s: (await (await fetch('/api/sources')).json()).length,
  m: (await (await fetch('/api/meteorology')).json()).length,
}))
page.on('dialog', (d) => d.accept())
await page.locator('.snap-item button', { hasText: '删除' }).first().click()
await page.waitForTimeout(600)
const after = await page.evaluate(async () => ({
  s: (await (await fetch('/api/sources')).json()).length,
  m: (await (await fetch('/api/meteorology')).json()).length,
  snaps: (await (await fetch('/api/snapshots')).json()).snapshots.length,
}))
assert(after.s === before.s && after.m === before.m, '删除快照不得影响源/气象记录')
assert(after.snaps === 0, '快照应已删除')
console.log('5) 删除后源/气象记录数不变:', JSON.stringify(after))

assert(errors.length === 0, '页面运行错误: ' + errors.join('; '))
console.log('pageerrors: 无')
console.log('E2E 快照流程全部通过')
await browser.close()
