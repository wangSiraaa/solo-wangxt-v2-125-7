<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ControlPanel from './components/ControlPanel.vue'
import MapView from './components/MapView.vue'
import ResultPanel from './components/ResultPanel.vue'
import { api } from './api'
import { DEFAULT_FORM, type FormState } from './form'
import type {
  MetRow,
  PlumeGridResponse,
  SnapshotPayload,
  SnapshotRow,
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
const snapshots = ref<SnapshotRow[]>([])
const snapshotStoreNote = ref('')
const snapshotBusy = ref(false)
const snapshotMessage = ref<string | null>(null)
const snapshotMessageKind = ref<'info' | 'err'>('info')

const isCalm = computed(() => form.value.windSpeed < form.value.calmThreshold)

onMounted(async () => {
  try {
    const [h, s, m] = await Promise.all([
      api.health(),
      api.sources(),
      api.meteorology(),
    ])
    repo.value = h.repository
    snapshotStoreNote.value = h.snapshot_store_note ?? ''
    sources.value = s
    meteorology.value = m
    applySource(s[0])
    applyMet(m[0])
    await Promise.all([run(), refreshSnapshots()])
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

// ---- 命名情景快照：只保存输入有效值；恢复时重新调用计算接口 ----

function snapshotNote(text: string, kind: 'info' | 'err' = 'info') {
  snapshotMessage.value = text
  snapshotMessageKind.value = kind
}

async function refreshSnapshots() {
  const r = await api.snapshots()
  snapshots.value = r.snapshots
  snapshotStoreNote.value = r.snapshot_store_note
}

function buildSnapshotPayload(): SnapshotPayload {
  const s = sources.value.find((x) => x.id === form.value.sourceId)
  if (!s) throw new Error('当前选择的排放源已不存在，无法保存快照')
  return {
    source_id: s.id,
    met_id: form.value.metId,
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
    plume_rise: { use_plume_rise: form.value.useRise },
    parameterization: form.value.parameterization,
    power_law:
      form.value.parameterization === 'power_law'
        ? { ay: form.value.ay, py: form.value.py, az: form.value.az, pz: form.value.pz }
        : null,
    grid: {
      downwind_extent_m: form.value.downwindExtent,
      crosswind_extent_m: form.value.crosswindExtent,
      upwind_extent_m: form.value.upwindExtent,
      nx: form.value.nx,
      ny: form.value.ny,
    },
    calm_threshold_ms: form.value.calmThreshold,
  }
}

async function saveSnapshot(name: string) {
  snapshotBusy.value = true
  try {
    await api.createSnapshot(name, buildSnapshotPayload())
    await refreshSnapshots()
    snapshotNote(`已保存快照「${name}」（仅输入参数，不含计算结果）。`)
  } catch (e: any) {
    snapshotNote('保存快照失败：' + (e.message || e), 'err')
  } finally {
    snapshotBusy.value = false
  }
}

async function restoreSnapshot(id: number) {
  snapshotBusy.value = true
  try {
    // 后端校验引用的源/气象记录仍存在；失效时返回 409，绝不静默改用别的记录
    const snap = await api.restoreSnapshot(id)
    const p = snap.payload
    // 刷新源/气象列表，确保下拉框能正确显示快照引用的记录
    const [s, m] = await Promise.all([api.sources(), api.meteorology()])
    sources.value = s
    meteorology.value = m
    if (!s.some((x) => x.id === p.source_id) || !m.some((x) => x.id === p.met_id)) {
      snapshotNote(
        `快照「${snap.name}」引用的源或气象情景已不存在，无法按原样恢复；输入未改动。`,
        'err',
      )
      return
    }
    form.value = {
      ...form.value,
      sourceId: p.source_id,
      metId: p.met_id,
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
      ...(p.power_law
        ? { ay: p.power_law.ay, py: p.power_law.py, az: p.power_law.az, pz: p.power_law.pz }
        : {}),
      downwindExtent: p.grid.downwind_extent_m,
      crosswindExtent: p.grid.crosswind_extent_m,
      upwindExtent: p.grid.upwind_extent_m,
      nx: p.grid.nx,
      ny: p.grid.ny,
      calmThreshold: p.calm_threshold_ms,
    }
    // 用恢复出的输入重新调用当前计算接口；历史网格数值不作为结果
    await run()
    snapshotNote(
      isCalm.value
        ? `已恢复快照「${snap.name}」的输入；当前为静风条件，模型拒绝计算（见地图上方提示）。`
        : `已恢复快照「${snap.name}」的输入并重新计算（未使用任何历史结果）。`,
    )
  } catch (e: any) {
    if (e.apiError?.error === 'stale_reference') {
      snapshotNote(e.apiError.message, 'err')
    } else {
      snapshotNote('恢复快照失败：' + (e.message || e), 'err')
    }
  } finally {
    snapshotBusy.value = false
  }
}

async function renameSnapshot(id: number, name: string) {
  snapshotBusy.value = true
  try {
    await api.renameSnapshot(id, name)
    await refreshSnapshots()
    snapshotNote(`已重命名为「${name}」。`)
  } catch (e: any) {
    snapshotNote('重命名失败：' + (e.message || e), 'err')
  } finally {
    snapshotBusy.value = false
  }
}

async function deleteSnapshot(id: number) {
  snapshotBusy.value = true
  try {
    // 只删除快照本身；源/气象记录不受影响
    await api.deleteSnapshot(id)
    await refreshSnapshots()
    snapshotNote('快照已删除（源与气象记录不受影响）。')
  } catch (e: any) {
    snapshotNote('删除快照失败：' + (e.message || e), 'err')
  } finally {
    snapshotBusy.value = false
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
  const s = sources.value.find((x) => x.id === form.value.sourceId)
  if (!s) {
    loading.value = false
    result.value = null
    error.value = `当前选择的排放源（id=${form.value.sourceId}）已不存在，请重新选择。`
    return
  }
  try {
    result.value = await api.plumeGrid({
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
    })
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
      :snapshot-store-note="snapshotStoreNote"
      :snapshot-busy="snapshotBusy"
      :snapshot-message="snapshotMessage"
      :snapshot-message-kind="snapshotMessageKind"
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
