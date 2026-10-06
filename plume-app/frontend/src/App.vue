<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ControlPanel from './components/ControlPanel.vue'
import MapView from './components/MapView.vue'
import ResultPanel from './components/ResultPanel.vue'
import { api } from './api'
import { DEFAULT_FORM, type FormState } from './form'
import type {
  MetRow,
  PlumeGridRequest,
  PlumeGridResponse,
  SnapshotMeta,
  SourceRow,
} from './types'
import { legendStops } from './colors'

const sources = ref<SourceRow[]>([])
const meteorology = ref<MetRow[]>([])
const form = ref<FormState>({ ...DEFAULT_FORM })
const result = ref<PlumeGridResponse | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)
const repo = ref<string>('…')
const showFill = ref(true)
const showIso = ref(true)
const showBg = ref(true)
const snapshots = ref<SnapshotMeta[]>([])
const snapshotPersistence = ref<string>('')
const snapshotBusy = ref(false)

const isCalm = computed(() => form.value.windSpeed < form.value.calmThreshold)

onMounted(async () => {
  try {
    const [h, s, m, snap] = await Promise.all([
      api.health(),
      api.sources(),
      api.meteorology(),
      api.snapshots(),
    ])
    repo.value = h.repository
    sources.value = s
    meteorology.value = m
    snapshots.value = snap.snapshots
    snapshotPersistence.value = snap.persistence
    applySource(s[0])
    applyMet(m[0])
    await run()
  } catch (e: any) {
    error.value = '初始化失败：' + e.message
  }
})

function applySource(s: SourceRow) {
  form.value = {
    ...form.value,
    sourceId: s.id,
    stackHeight: s.stack_height_m,
    emission: s.emission_rate_g_s,
    stackDia: s.stack_diameter_m,
    exitV: s.exit_velocity_ms,
    stackT: s.stack_temp_k,
  }
}

function applyMet(m: MetRow) {
  form.value = {
    ...form.value,
    metId: m.id,
    windFrom: m.wind_from_deg,
    windSpeed: m.wind_speed_ms,
    stability: m.stability_class,
    ambientT: m.ambient_temp_k,
    pressure: m.pressure_hpa,
    background: m.background_conc_ug_m3,
  }
}

function onSelectSource(id: number) {
  const s = sources.value.find((x) => x.id === id)
  if (s) {
    applySource(s)
    run()
  }
}
function onSelectMet(id: number) {
  const m = meteorology.value.find((x) => x.id === id)
  if (m) {
    applyMet(m)
    run()
  }
}

/** 由当前表单构造计算请求（有效输入值）；保存快照复用同一构造，保证存的就是算的。 */
function buildGridRequest(): PlumeGridRequest {
  const s = sources.value.find((x) => x.id === form.value.sourceId)!
  return {
    source: {
      name: s.name,
      lon: s.lon,
      lat: s.lat,
      stack_height_m: form.value.stackHeight,
      emission_rate_g_s: form.value.emission,
      stack_diameter_m: form.value.stackDia,
      exit_velocity_ms: form.value.exitV,
      stack_temp_k: form.value.stackT,
      pollutant: s.pollutant,
    },
    meteorology: {
      name: '界面情景',
      wind_from_deg: form.value.windFrom,
      wind_speed_ms: form.value.windSpeed,
      stability_class: form.value.stability,
      ambient_temp_k: form.value.ambientT,
      pressure_hpa: form.value.pressure,
      background_conc_ug_m3: form.value.background,
    },
    grid: {
      downwind_extent_m: form.value.downwindExtent,
      crosswind_extent_m: form.value.crosswindExtent,
      upwind_extent_m: form.value.upwindExtent,
      nx: form.value.nx,
      ny: form.value.ny,
    },
    plume_rise: { use_plume_rise: form.value.useRise },
    parameterization: form.value.parameterization,
    power_law:
      form.value.parameterization === 'power_law'
        ? { ay: form.value.ay, py: form.value.py, az: form.value.az, pz: form.value.pz }
        : null,
    calm_threshold_ms: form.value.calmThreshold,
  }
}

async function run() {
  if (isCalm.value) {
    result.value = null
    error.value =
      `静风（u=${form.value.windSpeed} m/s < 阈值 ${form.value.calmThreshold} m/s）：` +
      '定常高斯烟羽输运假设失效，模型拒绝硬算，不输出任何浓度场。'
    return
  }
  loading.value = true
  error.value = null
  try {
    result.value = await api.plumeGrid(buildGridRequest())
  } catch (e: any) {
    result.value = null
    if (e.apiError?.error === 'calm_wind') {
      error.value = e.apiError.message
    } else {
      error.value = e.message || '计算失败'
    }
  } finally {
    loading.value = false
  }
}

// ---- 命名情景快照 -------------------------------------------------------
// 快照只保存输入；恢复 = 回填表单 + 重新调用当前计算接口（run），
// 旧网格数值不会被当作新模型结果。

async function refreshSnapshots() {
  const snap = await api.snapshots()
  snapshots.value = snap.snapshots
  snapshotPersistence.value = snap.persistence
}

async function saveSnapshot(name: string) {
  snapshotBusy.value = true
  error.value = null
  try {
    const req = buildGridRequest()
    await api.createSnapshot(name, {
      source_id: form.value.sourceId,
      met_id: form.value.metId,
      source: req.source,
      meteorology: req.meteorology,
      grid: req.grid,
      plume_rise: req.plume_rise,
      parameterization: req.parameterization,
      power_law: req.power_law ?? null,
      calm_threshold_ms: req.calm_threshold_ms,
    })
    await refreshSnapshots()
  } catch (e: any) {
    error.value = '保存快照失败：' + (e.message || '未知错误')
  } finally {
    snapshotBusy.value = false
  }
}

async function restoreSnapshot(id: number) {
  snapshotBusy.value = true
  error.value = null
  try {
    const snap = await api.getSnapshot(id)
    const p = snap.payload
    // 引用校验：源/气象记录已不存在时给出可理解的失效提示，
    // 绝不静默改用列表里的其他源/气象。
    if (p.source_id != null && !sources.value.some((s) => s.id === p.source_id)) {
      error.value =
        `快照「${snap.name}」无法恢复：其引用的排放源记录 #${p.source_id} 已不存在。` +
        '快照未被修改；如需继续，请在数据库中恢复该源记录后重试，或将当前输入另存为新快照。'
      return
    }
    if (p.met_id != null && !meteorology.value.some((m) => m.id === p.met_id)) {
      error.value =
        `快照「${snap.name}」无法恢复：其引用的气象情景记录 #${p.met_id} 已不存在。` +
        '快照未被修改；如需继续，请在数据库中恢复该气象记录后重试，或将当前输入另存为新快照。'
      return
    }
    // 回填全部有效输入（含界面调整值与网格/模型参数）
    form.value = {
      ...form.value,
      sourceId: p.source_id ?? form.value.sourceId,
      metId: p.met_id ?? form.value.metId,
      stackHeight: p.source.stack_height_m,
      emission: p.source.emission_rate_g_s,
      stackDia: p.source.stack_diameter_m,
      exitV: p.source.exit_velocity_ms,
      stackT: p.source.stack_temp_k,
      windFrom: p.meteorology.wind_from_deg,
      windSpeed: p.meteorology.wind_speed_ms,
      stability: p.meteorology.stability_class,
      ambientT: p.meteorology.ambient_temp_k,
      pressure: p.meteorology.pressure_hpa,
      background: p.meteorology.background_conc_ug_m3,
      useRise: p.plume_rise.use_plume_rise,
      parameterization: p.parameterization,
      ay: p.power_law?.ay ?? DEFAULT_FORM.ay,
      py: p.power_law?.py ?? DEFAULT_FORM.py,
      az: p.power_law?.az ?? DEFAULT_FORM.az,
      pz: p.power_law?.pz ?? DEFAULT_FORM.pz,
      downwindExtent: p.grid.downwind_extent_m,
      crosswindExtent: p.grid.crosswind_extent_m,
      upwindExtent: p.grid.upwind_extent_m,
      nx: p.grid.nx,
      ny: p.grid.ny,
      calmThreshold: p.calm_threshold_ms,
    }
    // 用恢复出的输入重新调用当前计算接口，得到新的结果与风向图
    await run()
  } catch (e: any) {
    error.value = '读取快照失败：' + (e.message || '未知错误')
  } finally {
    snapshotBusy.value = false
  }
}

async function renameSnapshot(id: number, name: string) {
  snapshotBusy.value = true
  error.value = null
  try {
    await api.renameSnapshot(id, name)
    await refreshSnapshots()
  } catch (e: any) {
    error.value = '重命名快照失败：' + (e.message || '未知错误')
  } finally {
    snapshotBusy.value = false
  }
}

async function deleteSnapshot(id: number) {
  snapshotBusy.value = true
  error.value = null
  try {
    // 只删除快照本身；排放源/气象记录不受任何影响
    await api.deleteSnapshot(id)
    await refreshSnapshots()
  } catch (e: any) {
    error.value = '删除快照失败：' + (e.message || '未知错误')
  } finally {
    snapshotBusy.value = false
  }
}

const stops = computed(() =>
  result.value ? legendStops(result.value.iso_levels_ug_m3) : [],
)
</script>

<template>
  <div class="layout">
    <header class="topbar">
      <h1>离线高斯烟羽情景演示</h1>
      <span class="sub">平坦地形 · 稳态风 · 显式参数化 · 环境课程教学</span>
      <span class="spacer" />
      <span class="repo">数据后端：{{ repo === 'postgis' ? 'PostgreSQL/PostGIS' : '内存虚构数据（PostGIS 未连接时回退）' }}</span>
    </header>

    <ControlPanel
      :sources="sources"
      :meteorology="meteorology"
      v-model:form="form"
      :loading="loading"
      :snapshots="snapshots"
      :snapshot-persistence="snapshotPersistence"
      :snapshot-busy="snapshotBusy"
      @select-source="onSelectSource"
      @select-met="onSelectMet"
      @run="run"
      @save-snapshot="saveSnapshot"
      @restore-snapshot="restoreSnapshot"
      @rename-snapshot="renameSnapshot"
      @delete-snapshot="deleteSnapshot"
    />

    <div class="map-wrap">
      <MapView
        :result="result"
        :show-fill="showFill"
        :show-iso="showIso"
        :show-bg="showBg"
      />
      <div
        v-if="result && stops.length"
        class="legend"
        style="left:12px;bottom:12px"
      >
        <div>
          <b>烟羽贡献浓度</b>（μg/m³，不含背景）
        </div>
        <div class="bar">
          <span
            v-for="s in stops"
            :key="s.level"
            :style="{ flex: 1, background: s.color }"
          />
        </div>
        <div class="labels">
          <span>{{ stops[0].level }}</span>
          <span>{{ stops[stops.length - 1].level }}</span>
        </div>
        <div class="bgrow">
          <label class="toggle" style="margin:4px 0 2px">
            <input type="checkbox" v-model="showFill" /> 烟羽贡献等值区
          </label>
          <label class="toggle" style="margin:2px 0">
            <input type="checkbox" v-model="showIso" /> 烟羽贡献等值线
          </label>
          <label class="toggle" style="margin:2px 0">
            <input type="checkbox" v-model="showBg" />
            背景值叠加（均匀 {{ result.background_conc_ug_m3 }} μg/m³）
          </label>
          <div class="muted" style="font-size:10px;margin-top:2px">
            总浓度＝烟羽贡献＋背景值，见右侧结果分解与悬停读数
          </div>
        </div>
      </div>
      <div v-if="isCalm" class="notice err" style="position:absolute;top:12px;left:50%;transform:translateX(-50%);z-index:6;max-width:560px">
        {{ error }}
      </div>
      <div class="disclaimer-foot">
        教学模型：不得用于真实事故预警或法规达标判定
      </div>
    </div>

    <ResultPanel :result="result" :error="error" />
  </div>
</template>
