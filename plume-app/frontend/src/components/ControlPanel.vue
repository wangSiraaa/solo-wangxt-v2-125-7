<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MetRow, SnapshotMeta, SourceRow } from '../types'
import type { FormState } from '../form'

const props = defineProps<{
  sources: SourceRow[]
  meteorology: MetRow[]
  form: FormState
  loading: boolean
  snapshots: SnapshotMeta[]
  snapshotPersistence: string
  snapshotBusy: boolean
}>()

const emit = defineEmits<{
  (e: 'update:form', v: FormState): void
  (e: 'select-source', id: number): void
  (e: 'select-met', id: number): void
  (e: 'run'): void
  (e: 'save-snapshot', name: string): void
  (e: 'restore-snapshot', id: number): void
  (e: 'rename-snapshot', id: number, name: string): void
  (e: 'delete-snapshot', id: number): void
}>()

function patch(p: Partial<FormState>) {
  emit('update:form', { ...props.form, ...p })
}

const isCalm = computed(() => props.form.windSpeed < props.form.calmThreshold)

// ---- 情景快照（本地 UI 状态） ----
const newSnapshotName = ref('')
const editingId = ref<number | null>(null)
const editingName = ref('')

function saveSnapshot() {
  const name = newSnapshotName.value.trim()
  if (!name) return
  emit('save-snapshot', name)
  newSnapshotName.value = ''
}

function startRename(s: SnapshotMeta) {
  editingId.value = s.id
  editingName.value = s.name
}

function commitRename() {
  const name = editingName.value.trim()
  if (editingId.value != null && name) {
    emit('rename-snapshot', editingId.value, name)
  }
  editingId.value = null
}

function removeSnapshot(s: SnapshotMeta) {
  if (
    window.confirm(
      `删除快照「${s.name}」？\n只删除该快照，排放源与气象记录不受影响。`,
    )
  ) {
    emit('delete-snapshot', s.id)
  }
}

function fmtTime(iso: string) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function snapshotInvalidReason(s: SnapshotMeta): string | null {
  if (s.source_id != null && s.source_exists === false) {
    return `引用的排放源记录 #${s.source_id} 已不存在`
  }
  if (s.met_id != null && s.met_exists === false) {
    return `引用的气象情景记录 #${s.met_id} 已不存在`
  }
  return null
}
</script>

<template>
  <div class="panel">
    <div class="section">
      <h2>虚构情景（教学数据，非真实设施）</h2>
      <label class="field">
        <span class="lbl">排放源</span>
        <select
          class="num"
          :value="form.sourceId"
          @change="emit('select-source', Number(($event.target as HTMLSelectElement).value))"
        >
          <option v-for="s in sources" :key="s.id" :value="s.id">
            {{ s.name }}（{{ s.pollutant }}，H={{ s.stack_height_m }} m）
          </option>
        </select>
      </label>
      <label class="field">
        <span class="lbl">气象情景</span>
        <select
          class="num"
          :value="form.metId"
          @change="emit('select-met', Number(($event.target as HTMLSelectElement).value))"
        >
          <option v-for="m in meteorology" :key="m.id" :value="m.id">
            {{ m.name }}
          </option>
        </select>
      </label>
      <div class="muted" style="font-size:11px">
        下列调整只用于本次计算请求，不会写回数据库记录。
      </div>
    </div>

    <div class="section">
      <h2>① 源项</h2>
      <label class="field">
        <span class="lbl">烟囱高度 H（m）<b>{{ form.stackHeight }}</b></span>
        <input data-test="height" type="range" min="5" max="250" step="1" :value="form.stackHeight"
          @input="patch({ stackHeight: Number(($event.target as HTMLInputElement).value) })" />
      </label>
      <label class="field">
        <span class="lbl">排放率 Q（g/s）<b>{{ form.emission }}</b></span>
        <input data-test="emission" type="range" min="0" max="200" step="0.5" :value="form.emission"
          @input="patch({ emission: Number(($event.target as HTMLInputElement).value) })" />
      </label>
      <details class="params">
        <summary>抬升参数（Holland，默认关闭）</summary>
        <div class="toggle">
          <input type="checkbox" :checked="form.useRise"
            @change="patch({ useRise: ($event.target as HTMLInputElement).checked })" />
          叠加烟气抬升 Δh
        </div>
        <div class="row2">
          <label class="field"><span class="lbl">出口内径 d（m）</span>
            <input class="num" type="number" step="0.1" :value="form.stackDia"
              @input="patch({ stackDia: Number(($event.target as HTMLInputElement).value) })" /></label>
          <label class="field"><span class="lbl">出口流速（m/s）</span>
            <input class="num" type="number" step="0.5" :value="form.exitV"
              @input="patch({ exitV: Number(($event.target as HTMLInputElement).value) })" /></label>
          <label class="field"><span class="lbl">烟气温度（K）</span>
            <input class="num" type="number" step="1" :value="form.stackT"
              @input="patch({ stackT: Number(($event.target as HTMLInputElement).value) })" /></label>
        </div>
      </details>
    </div>

    <div class="section">
      <h2>② 气象（稳态风）</h2>
      <label class="field">
        <span class="lbl">风速 u（m/s）<b>{{ form.windSpeed }}</b></span>
        <input data-test="windspeed" type="range" min="0" max="12" step="0.1" :value="form.windSpeed"
          @input="patch({ windSpeed: Number(($event.target as HTMLInputElement).value) })" />
      </label>
      <div v-if="isCalm" class="notice err">
        静风：u={{ form.windSpeed }} m/s &lt; 阈值 {{ form.calmThreshold }} m/s。
        定常高斯烟羽不适用，模型将<b>拒绝计算</b>（不用近零风速除出巨大浓度）。
      </div>
      <label class="field">
        <span class="lbl">
          风向（气象来向角，0=北来风，顺时针）<b>{{ form.windFrom }}°</b>
        </span>
        <input data-test="windfrom" type="range" min="0" max="359" step="1" :value="form.windFrom"
          @input="patch({ windFrom: Number(($event.target as HTMLInputElement).value) })" />
      </label>
      <label class="field">
        <span class="lbl">Pasquill 稳定度</span>
        <select class="num" :value="form.stability"
          @change="patch({ stability: ($event.target as HTMLSelectElement).value as FormState['stability'] })">
          <option value="A">A 极不稳定</option>
          <option value="B">B 不稳定</option>
          <option value="C">C 弱不稳定</option>
          <option value="D">D 中性</option>
          <option value="E">E 较稳定</option>
          <option value="F">F 稳定</option>
        </select>
      </label>
      <div class="row2">
        <label class="field"><span class="lbl">环境温度（K）</span>
          <input class="num" type="number" step="0.1" :value="form.ambientT"
            @input="patch({ ambientT: Number(($event.target as HTMLInputElement).value) })" /></label>
        <label class="field"><span class="lbl">气压（hPa）</span>
          <input class="num" type="number" step="1" :value="form.pressure"
            @input="patch({ pressure: Number(($event.target as HTMLInputElement).value) })" /></label>
      </div>
      <label class="field">
        <span class="lbl">背景浓度（μg/m³，与烟羽分开计量）<b>{{ form.background }}</b></span>
        <input data-test="background" type="range" min="0" max="100" step="0.5" :value="form.background"
          @input="patch({ background: Number(($event.target as HTMLInputElement).value) })" />
      </label>
    </div>

    <div class="section">
      <h2>③ 弥散参数化（显式系数）</h2>
      <label class="field">
        <select class="num" :value="form.parameterization"
          @change="patch({ parameterization: ($event.target as HTMLSelectElement).value as any })">
          <option value="briggs_rural">Briggs 乡村系数（默认，建议 0.1–10 km）</option>
          <option value="power_law">幂律 σ=a·x^p（解析核对用）</option>
        </select>
      </label>
      <details v-if="form.parameterization === 'power_law'" class="params">
        <summary>幂律系数</summary>
        <div class="row2">
          <label class="field"><span class="lbl">ay</span>
            <input class="num" type="number" step="0.01" :value="form.ay"
              @input="patch({ ay: Number(($event.target as HTMLInputElement).value) })" /></label>
          <label class="field"><span class="lbl">py</span>
            <input class="num" type="number" step="0.1" :value="form.py"
              @input="patch({ py: Number(($event.target as HTMLInputElement).value) })" /></label>
          <label class="field"><span class="lbl">az</span>
            <input class="num" type="number" step="0.01" :value="form.az"
              @input="patch({ az: Number(($event.target as HTMLInputElement).value) })" /></label>
          <label class="field"><span class="lbl">pz</span>
            <input class="num" type="number" step="0.1" :value="form.pz"
              @input="patch({ pz: Number(($event.target as HTMLInputElement).value) })" /></label>
        </div>
      </details>
    </div>

    <div class="section">
      <h2>④ 采样网格（只改变采样，不改变输入）</h2>
      <div class="row2">
        <label class="field"><span class="lbl">下风向范围（m）</span>
          <input class="num" type="number" step="100" :value="form.downwindExtent"
            @input="patch({ downwindExtent: Number(($event.target as HTMLInputElement).value) })" /></label>
        <label class="field"><span class="lbl">横风向范围（m）</span>
          <input class="num" type="number" step="100" :value="form.crosswindExtent"
            @input="patch({ crosswindExtent: Number(($event.target as HTMLInputElement).value) })" /></label>
        <label class="field"><span class="lbl">上风向延伸（m）</span>
          <input class="num" type="number" step="50" :value="form.upwindExtent"
            @input="patch({ upwindExtent: Number(($event.target as HTMLInputElement).value) })" /></label>
      </div>
      <label class="field">
        <span class="lbl">下风向格点数 nx <b>{{ form.nx }}</b></span>
        <input data-test="nx" type="range" min="21" max="201" step="10" :value="form.nx"
          @input="patch({ nx: Number(($event.target as HTMLInputElement).value) })" />
      </label>
      <label class="field">
        <span class="lbl">横风向格点数 ny <b>{{ form.ny }}</b></span>
        <input data-test="ny" type="range" min="11" max="121" step="2" :value="form.ny"
          @input="patch({ ny: Number(($event.target as HTMLInputElement).value) })" />
      </label>
      <label class="field"><span class="lbl">静风阈值（m/s）</span>
        <input class="num" type="number" min="0.1" max="5" step="0.1" :value="form.calmThreshold"
          @input="patch({ calmThreshold: Number(($event.target as HTMLInputElement).value) })" /></label>
      <button @click="emit('run')" :disabled="loading || isCalm">
        {{ loading ? '计算中…' : '运行烟羽计算' }}
      </button>
      <span v-if="isCalm" class="badge bad">静风，已停用</span>
    </div>

    <div class="section">
      <h2>⑤ 情景快照（只保存输入，恢复时重新计算）</h2>
      <div class="muted" style="font-size:11px;margin-bottom:6px">
        保存当前源/气象有效值、模型类型、抬升与采样网格参数；
        不保存浓度结果，恢复时将重新调用计算接口生成新结果。
      </div>
      <div class="snap-save">
        <input
          data-test="snapshot-name"
          class="num"
          type="text"
          maxlength="120"
          placeholder="快照名称，如：第 6 周课堂演示"
          v-model="newSnapshotName"
          @keyup.enter="saveSnapshot"
        />
        <button
          data-test="snapshot-save"
          @click="saveSnapshot"
          :disabled="snapshotBusy || !newSnapshotName.trim()"
        >
          保存
        </button>
      </div>
      <div v-if="snapshotPersistence" class="muted" style="font-size:11px;margin:4px 0 8px">
        存储方式：{{ snapshotPersistence }}
      </div>
      <div v-if="!snapshots.length" class="muted" style="font-size:12px">
        暂无快照。
      </div>
      <div v-for="s in snapshots" :key="s.id" class="snap-item" :data-test="`snapshot-${s.id}`">
        <template v-if="editingId === s.id">
          <div class="snap-save">
            <input
              class="num"
              type="text"
              maxlength="120"
              v-model="editingName"
              @keyup.enter="commitRename"
              @keyup.esc="editingId = null"
            />
            <button class="ghost" @click="commitRename" :disabled="!editingName.trim()">确定</button>
            <button class="ghost" @click="editingId = null">取消</button>
          </div>
        </template>
        <template v-else>
          <div class="snap-head">
            <span class="snap-name" :title="s.name">{{ s.name }}</span>
            <span class="muted" style="font-size:10px;white-space:nowrap">{{ fmtTime(s.created_at) }}</span>
          </div>
          <div v-if="snapshotInvalidReason(s)" class="notice warn" style="margin:4px 0;padding:4px 8px;font-size:11px">
            已失效：{{ snapshotInvalidReason(s) }}。不会改用其他源/气象，请处理后再恢复。
          </div>
          <div class="snap-actions">
            <button
              class="ghost"
              :data-test="`snapshot-restore-${s.id}`"
              @click="emit('restore-snapshot', s.id)"
              :disabled="snapshotBusy || !!snapshotInvalidReason(s)"
              :title="snapshotInvalidReason(s) || '回填输入并重新运行计算'"
            >
              恢复
            </button>
            <button class="ghost" @click="startRename(s)" :disabled="snapshotBusy">重命名</button>
            <button class="ghost" @click="removeSnapshot(s)" :disabled="snapshotBusy">删除</button>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
