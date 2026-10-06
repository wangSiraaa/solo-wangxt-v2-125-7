/**
 * 命名情景快照的端到端验收（需 vite dev 服务器 + 后端 API）：
 * 1. 保存快照（西风 270°、nx=121）→ 改风向 90° 与分辨率 nx=41 → 恢复
 *    => 表单与风向图回到 270°/nx=121，且为重新计算的结果；
 * 2. 重命名、删除（删除后源/气象下拉记录不变）；
 * 3. 引用已不存在源的快照：恢复时给出可理解的失效提示，表单不被改动。
 */
import { chromium } from 'playwright'

const errors: string[] = []
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } })
page.on('pageerror', (e) => errors.push(e.message))

async function setRange(test: string, val: string) {
  const loc = page.locator(`input[data-test="${test}"]`)
  await loc.fill(val)
  await loc.dispatchEvent('input')
}
async function runAndWait() {
  await page.getByRole('button', { name: /运行烟羽计算/ }).click()
  await page.waitForTimeout(1200)
}
async function windArrow() {
  return await page.evaluate(() => {
    // @ts-ignore
    const wind = window.__map.getSource('wind')._data
    const line = wind.features.find((f: any) => f.geometry.type === 'LineString')
    return { head: line.geometry.coordinates[1], tail: line.geometry.coordinates[0] }
  })
}
const windFromVal = () =>
  page.locator('input[data-test="windfrom"]').inputValue()
const nxVal = () => page.locator('input[data-test="nx"]').inputValue()

// 通过 API 准备一个"悬空引用"快照（源 id=9999 不存在），稍后验证失效提示
await page.goto('http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
await page.waitForTimeout(2000)
const staleId = await page.evaluate(async () => {
  const payload = {
    source_id: 9999, met_id: 1,
    source: {
      name: 'x', lon: 116.4, lat: 39.9, stack_height_m: 120, emission_rate_g_s: 50,
      stack_diameter_m: 4, exit_velocity_ms: 18, stack_temp_k: 410, pollutant: 'SO2',
    },
    meteorology: {
      name: 'x', wind_from_deg: 180, wind_speed_ms: 3, stability_class: 'B',
      ambient_temp_k: 293.15, pressure_hpa: 1013, background_conc_ug_m3: 8,
    },
    plume_rise: { use_plume_rise: false },
    parameterization: 'briggs_rural', power_law: null,
    grid: { downwind_extent_m: 6000, crosswind_extent_m: 2000, upwind_extent_m: 300, nx: 61, ny: 41 },
    calm_threshold_ms: 1,
  }
  const r = await fetch('/api/snapshots', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'E2E 悬空引用', payload }),
  })
  return (await r.json()).id as number
})

// ---- 1. 保存 → 修改 → 恢复 ----
await setRange('windfrom', '270')
await setRange('nx', '121')
await runAndWait()
await page.locator('input[data-test="snapshot-name"]').fill('E2E 西风快照')
await page.locator('[data-test="snapshot-save"]').click()
await page.waitForTimeout(800)
const savedItem = page.locator('.snap-item', { hasText: 'E2E 西风快照' })
const savedVisible = await savedItem.count()
console.log('1a 快照已保存并列出:', savedVisible === 1)

// 修改风向与分辨率（模拟保存后继续探索）
await setRange('windfrom', '90')
await setRange('nx', '41')
await runAndWait()
console.log('1b 修改后表单: windfrom=', await windFromVal(), 'nx=', await nxVal())

// 恢复快照
await savedItem.getByRole('button', { name: '恢复并重算' }).click()
await page.waitForTimeout(1500)
const restoredWind = await windFromVal()
const restoredNx = await nxVal()
const arrow = await windArrow()
const pointsEast = arrow.head[0] > arrow.tail[0] // 270° 西风 -> 向东输运
console.log('1c 恢复后表单: windfrom=', restoredWind, 'nx=', restoredNx)
console.log('1d 风向图向东（270° 西风）:', pointsEast,
  `(lon ${arrow.tail[0].toFixed(4)} -> ${arrow.head[0].toFixed(4)})`)
const restoreMsg = await page.locator('.snap-item').first().isVisible()
console.log('1e 恢复提示可见:', restoreMsg)

// ---- 2. 重命名 + 删除 ----
await savedItem.getByRole('button', { name: '重命名' }).click()
// 重命名模式下名称从文本变为输入框（原 hasText 定位随之失效），改为全局结构定位
await page.locator('.snap-item .snap-save input').fill('E2E 改名后')
await page.getByRole('button', { name: '确定' }).click()
await page.waitForTimeout(800)
const renamed = await page.locator('.snap-item', { hasText: 'E2E 改名后' }).count()
console.log('2a 重命名生效:', renamed === 1)

const sourcesBefore = await page.locator('.panel select').first().locator('option').count()
await page.locator('.snap-item', { hasText: 'E2E 改名后' })
  .getByRole('button', { name: '删除' }).click()
await page.waitForTimeout(800)
const afterDelete = await page.locator('.snap-item', { hasText: 'E2E 改名后' }).count()
const sourcesAfter = await page.locator('.panel select').first().locator('option').count()
console.log('2b 快照已删除:', afterDelete === 0,
  '| 源记录数不变:', sourcesBefore === sourcesAfter, `(${sourcesBefore})`)

// ---- 3. 悬空引用：可理解的失效提示，表单不被改动 ----
await setRange('windfrom', '270')
const staleItem = page.locator('.snap-item', { hasText: 'E2E 悬空引用' })
await staleItem.getByRole('button', { name: '恢复并重算' }).click()
await page.waitForTimeout(1000)
const errText = await page.locator('.notice.err').first().textContent()
const staleOk = !!errText && errText.includes('已不存在') && errText.includes('9999')
const formUntouched = (await windFromVal()) === '270'
console.log('3a 失效提示可理解:', staleOk, `（${errText?.trim().slice(0, 60)}…）`)
console.log('3b 失效时表单未被静默改动:', formUntouched)
// 清理
await page.evaluate(async (id) => {
  await fetch(`/api/snapshots/${id}`, { method: 'DELETE' })
}, staleId)

await page.screenshot({ path: '/tmp/plume-snapshots.png' })
const pass =
  savedVisible === 1 && restoredWind === '270' && restoredNx === '121' &&
  pointsEast && renamed === 1 && afterDelete === 0 &&
  sourcesBefore === sourcesAfter && staleOk && formUntouched
if (errors.length || !pass) {
  console.log('JS ERRORS:', errors, 'PASS:', pass)
  process.exit(1)
}
console.log('all snapshot e2e checks passed, no JS errors')
await browser.close()
