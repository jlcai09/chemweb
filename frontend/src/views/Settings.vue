<template>
  <div class="settings-view">
    <section class="settings-section">
      <div class="panel-header">
        <div>
          <span class="eyebrow">system</span>
          <strong>{{ t('settings.title') }}</strong>
        </div>
      </div>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item :label="t('settings.user')">{{ systemInfo?.username }}</el-descriptions-item>
        <el-descriptions-item :label="t('settings.host')">{{ systemInfo?.hostname }}</el-descriptions-item>
        <el-descriptions-item :label="t('settings.python')">{{ systemInfo?.python_version }}</el-descriptions-item>
        <el-descriptions-item :label="t('settings.scheduler')">{{ systemInfo?.scheduler }}</el-descriptions-item>
        <el-descriptions-item :label="t('settings.workspace')">{{ systemInfo?.workspace_root }}</el-descriptions-item>
      </el-descriptions>
    </section>

    <section class="settings-section">
      <div class="panel-header">
        <div>
          <span class="eyebrow">theme</span>
          <strong>{{ t('settings.themeTitle') }}</strong>
        </div>
      </div>
      <div class="settings-theme-panel">
        <label class="settings-theme-row">
          <span>{{ t('settings.themeAnimatedBackdrop') }}</span>
          <el-switch
            :model-value="themePreferences.animatedBackdrop"
            size="small"
            @update:model-value="setAnimatedBackdrop"
          />
        </label>
        <label class="settings-theme-row">
          <span>{{ t('settings.themeGlassBlur') }}</span>
          <el-switch
            :model-value="themePreferences.glassBlur"
            size="small"
            @update:model-value="setGlassBlur"
          />
        </label>
      </div>
    </section>

    <section class="settings-section settings-danger-section">
      <div class="panel-header">
        <div>
          <span class="eyebrow">cache</span>
          <strong>{{ t('settings.cacheTitle') }}</strong>
        </div>
      </div>
      <div class="settings-cache-panel">
        <div>
          <span>{{ t('settings.clientId') }}</span>
          <code>{{ clientId }}</code>
        </div>
        <div>
          <span>{{ t('settings.clientIdSource') }}</span>
          <code>{{ clientIdSourceLabel }}</code>
        </div>
        <div>
          <span>{{ t('settings.workspaceScope') }}</span>
          <code>{{ workspaceScopeKey }}</code>
        </div>
        <p>{{ t('settings.cacheDescription') }}</p>
        <el-button type="danger" :loading="clearing" @click="confirmClearCache">
          {{ t('settings.clearCache') }}
        </el-button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { clearClientCache } from '../api/clientCache'
import { clearLocalClientPreferences } from '../api/clientPreferences'
import { getClientId, getClientIdSource } from '../api/clientSession'
import { storeToRefs } from 'pinia'
import type { ThemePreferences } from '../types/canvasBoard'
import { scopedLocalStorageKey, getCurrentWorkspaceScopeKey } from '../api/workspaceScope'
import { t } from '../i18n'
import { useSystemStore } from '../stores/system'
import { usePreferencesStore } from '../stores/preferences'

const systemStore = useSystemStore()
const preferencesStore = usePreferencesStore()
const { systemInfo } = storeToRefs(systemStore)
const { themePreferences } = storeToRefs(preferencesStore)

const clientId = getClientId()
const clientIdSource = getClientIdSource()
const workspaceScopeKey = getCurrentWorkspaceScopeKey()

const clientIdSourceLabel = computed(() => {
  return clientIdSource === 'launcher'
    ? t('settings.clientIdSourceLauncher')
    : t('settings.clientIdSourceBrowser')
})

const clearing = ref(false)

function setThemePreference(key: keyof ThemePreferences, value: string | number | boolean) {
  preferencesStore.setThemePreference(key, value === true)
}

function setAnimatedBackdrop(value: string | number | boolean) {
  setThemePreference('animatedBackdrop', value)
}

function setGlassBlur(value: string | number | boolean) {
  setThemePreference('glassBlur', value)
}

async function confirmClearCache() {
  try {
    await ElMessageBox.confirm(t('settings.clearCacheWarning'), t('settings.clearCacheTitle'), {
      type: 'warning',
      confirmButtonText: t('settings.clearCacheConfirm'),
      cancelButtonText: t('common.cancel')
    })
  } catch {
    return
  }

  clearing.value = true
  try {
    await clearClientCache()
    clearLocalClientPreferences()
    clearLocalLayoutCache()
    ElMessage.success(t('settings.cacheCleared'))
    window.setTimeout(() => window.location.reload(), 300)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('settings.cacheClearFailed'))
  } finally {
    clearing.value = false
  }
}

function clearLocalLayoutCache() {
  const keys = [
    scopedLocalStorageKey('chemssh.canvas.boards.v1'),
    'chemssh.canvas.boards.v1',
    'chemssh.terminal.fontSize'
  ]
  for (const key of keys) {
    try {
      window.localStorage.removeItem(key)
    } catch {
      // Ignore storage failures while clearing best-effort local fallbacks.
    }
  }
}
</script>
