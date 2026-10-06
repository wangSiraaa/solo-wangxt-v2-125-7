<script setup lang="ts">
import { ref } from 'vue'
import type { SnapshotRow } from '../types'

const props = defineProps<{
  snapshots: SnapshotRow[]
  storeNote: string
  busy: boolean
  message: string | null
  messageKind: 'info' | 'err'
}>()

const emit = defineEmits<{
  (e: 'save', name: string): void
  (e: 'restore', id: number): void
  (e: 'rename', id: number, name: string): void
  (e: 'remove', id: number): void
}>()

const newName = ref('')
const renamingId = ref<number | null>(null)
const renameValue = ref('')

function save() {
  const name = newName.value.trim()
  if (!name) return
  emit('save', name)
  newName.value = ''
}

function startRename(s: SnapshotRow) {
  renamingId.value = s.id
  renameValue.value = s.name
}

function commitRename() {
  const name = renameValue.value.trim()
  if (renamingId.value !== null && name) {
    emit('rename', renamingId.value, name)
  }
  renamingId.value = null
}

function summarize(s: SnapshotRow): string {
  const p = s.payload
  return (
    `风向 ${p.meteorology.wind_from_deg}° · ${p.meteorology.wind_speed_ms} m/s · ` +
    `${p.meteorology.stability_class} 类 · ${p.parameterization === 'power_law' ? '幂律' : 'Briggs'}` +
    `${p.plume_rise.use_plume_rise ? ' · 含抬升' : ''} · 网格 ${p.grid.nx}×${p.grid.ny}`
  )
}
</script>

<template>
  <div class="section">
    <h2>命名情景快照（只存输入，不存结果）</h2>
    <div class="muted" style="font-size:11px;margin-bottom:6px">
      保存当前源/气象/模型/抬升/采样网格的有效输入；恢复时按原输入重新计算。
      {{ props.storeNote }}
    </div>
    <div class="snap-save">
      <input
        data-test="snapshot-name"
        class="num"
        v-model="newName"
        maxlength="80"
        placeholder="快照名称（如：第6周·西风讲解）"
        @keyup.enter="save"
      />
      <button data-test="snapshot-save" :disabled="busy || !newName.trim()" @click="save">
        保存快照
      </button>
    </div>
    <div v-if="props.message" class="notice" :class="props.messageKind" style="margin-top:8px">
      {{ props.message }}
    </div>
    <div v-if="!snapshots.length" class="muted" style="font-size:11px;margin-top:6px">
      暂无快照。
    </div>
    <div v-for="s in snapshots" :key="s.id" class="snap-item" :data-test="`snapshot-${s.id}`">
      <template v-if="renamingId === s.id">
        <div class="snap-save">
          <input
            class="num"
            v-model="renameValue"
            maxlength="80"
            @keyup.enter="commitRename"
            @keyup.esc="renamingId = null"
          />
          <button @click="commitRename">确定</button>
          <button class="ghost" @click="renamingId = null">取消</button>
        </div>
      </template>
      <template v-else>
        <div class="snap-head">
          <span class="snap-name">{{ s.name }}</span>
          <span class="snap-time">{{ s.created_at.slice(0, 19).replace('T', ' ') }}</span>
        </div>
        <div class="snap-sum">{{ summarize(s) }}</div>
        <div class="snap-actions">
          <button
            :data-test="`snapshot-restore-${s.id}`"
            :disabled="busy"
            @click="emit('restore', s.id)"
          >恢复并重算</button>
          <button class="ghost" :disabled="busy" @click="startRename(s)">重命名</button>
          <button
            class="ghost"
            :data-test="`snapshot-delete-${s.id}`"
            :disabled="busy"
            @click="emit('remove', s.id)"
          >删除</button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.snap-save {
  display: flex;
  gap: 6px;
  align-items: center;
}
.snap-save input {
  flex: 1;
  min-width: 0;
  padding: 4px 6px;
  border: 1px solid var(--line);
  border-radius: 4px;
  font-size: 12px;
}
.snap-item {
  border: 1px solid var(--line);
  border-radius: 5px;
  padding: 7px 9px;
  margin-top: 7px;
  font-size: 12px;
}
.snap-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 6px;
}
.snap-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.snap-time {
  color: var(--muted);
  font-size: 10px;
  white-space: nowrap;
}
.snap-sum {
  color: var(--muted);
  font-size: 11px;
  margin: 3px 0 6px;
}
.snap-actions {
  display: flex;
  gap: 6px;
}
.snap-actions button {
  padding: 3px 8px;
  font-size: 11px;
}
</style>
