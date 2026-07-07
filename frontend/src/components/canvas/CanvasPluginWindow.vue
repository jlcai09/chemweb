<template>
  <div class="canvas-plugin-window">
    <div v-if="!activeAssetUrl" class="canvas-plugin-picker">
      <div class="canvas-plugin-picker-header">
        <strong>{{ t('canvas.pluginSelect') }}</strong>
        <el-button :icon="Refresh" circle size="small" :loading="loading" @click="loadPlugins" />
      </div>
      <div class="canvas-plugin-list">
        <button
          v-for="item in panelOptions"
          :key="`${item.plugin.id}:${item.panel.id}`"
          class="canvas-plugin-item"
          type="button"
          @click="openPanel(item.plugin.id, item.panel.id)"
        >
          <strong>{{ item.panel.title || item.plugin.name }}</strong>
          <span>{{ item.plugin.name }}</span>
        </button>
      </div>
      <el-empty v-if="!loading && panelOptions.length === 0" :description="t('panel.empty')" />
    </div>
    <iframe
      v-else
      ref="frameRef"
      class="canvas-plugin-frame"
      :src="activeAssetUrl"
      :title="activeTitle"
      @load="sendInit"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { API_BASE, apiUrl, getAuthToken } from '../../api/http'
import { activatePlugin, listPlugins, type PluginManifest, type PluginPanel } from '../../api/plugins'
import type { FilePreviewProvider } from '../../api/filePreviewProviders'
import { t } from '../../i18n'

const props = defineProps<{
  instanceId: string
  pluginId?: string | null
  panelId?: string | null
  assetUrl?: string | null
  apiBase?: string | null
}>()

const emit = defineEmits<{
  'payload-change': [payload: Record<string, unknown>]
  'title-change': [title: string]
  'register-provider': [provider: FilePreviewProvider, ownerId: string]
  'unregister-provider': [providerId: string, ownerId: string]
}>()

const frameRef = ref<HTMLIFrameElement | null>(null)
const plugins = ref<PluginManifest[]>([])
const loading = ref(false)
const activePluginId = ref(props.pluginId ?? '')
const activePanelId = ref(props.panelId ?? '')
const activeAssetPath = ref(props.assetUrl ?? '')
const activeAssetUrl = ref(props.assetUrl ? apiUrl(props.assetUrl) : '')
const activeApiBase = ref(props.apiBase ?? '')
const activeTitle = ref('')
const registeredProviderIds = ref<string[]>([])

const panelOptions = computed(() => {
  return plugins.value.flatMap(plugin => (plugin.panels ?? []).map(panel => ({ plugin, panel })))
})

onMounted(() => {
  window.addEventListener('message', handlePluginMessage)
  void loadPlugins().then(() => {
    if (props.pluginId && props.panelId && !activeAssetUrl.value) {
      void openPanel(props.pluginId, props.panelId)
    }
  })
})

onBeforeUnmount(() => {
  window.removeEventListener('message', handlePluginMessage)
  cleanupRegisteredProviders()
})

watch(
  () => [props.pluginId, props.panelId] as const,
  ([pluginId, panelId]) => {
    if (!pluginId || !panelId) return
    if (pluginId === activePluginId.value && panelId === activePanelId.value) return
    void openPanel(pluginId, panelId)
  }
)

async function loadPlugins() {
  loading.value = true
  try {
    const response = await listPlugins()
    plugins.value = response.plugins
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('plugin.loadFailed'))
  } finally {
    loading.value = false
  }
}

async function openPanel(pluginId: string, panelId: string) {
  loading.value = true
  try {
    const manifest = plugins.value.find(plugin => plugin.id === pluginId)
    const panel = manifest?.panels.find(item => item.id === panelId)
    const activation = await activatePlugin(pluginId)
    const activatedPanel = activation.panels.find(item => item.id === panelId) ?? panel
    cleanupRegisteredProviders()
    activePluginId.value = pluginId
    activePanelId.value = panelId
    activeAssetPath.value = activation.asset_url
    activeAssetUrl.value = apiUrl(activeAssetPath.value)
    activeApiBase.value = activation.api_base
    activeTitle.value = activatedPanel?.title || manifest?.name || pluginId
    emit('title-change', activeTitle.value)
    emit('payload-change', {
      pluginId,
      panelId,
      assetUrl: activeAssetPath.value,
      apiBase: activeApiBase.value,
      title: activeTitle.value
    })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : t('plugin.activateFailed'))
  } finally {
    loading.value = false
  }
}

function sendInit() {
  const frame = frameRef.value
  if (!frame?.contentWindow || !activeAssetUrl.value) return
  frame.contentWindow.postMessage({
    type: 'chemssh:plugin:init',
    version: 1,
    pluginId: activePluginId.value,
    panelId: activePanelId.value,
    instanceId: props.instanceId,
    locale: window.navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en',
    theme: 'light',
    apiBase: activeApiBase.value,
    assetBase: activeAssetUrl.value,
    authToken: getAuthToken(),
    initialFile: null
  }, new URL(activeAssetUrl.value, window.location.href).origin)
}

function handlePluginMessage(event: MessageEvent) {
  if (event.source !== frameRef.value?.contentWindow) return
  if (!isTrustedPluginOrigin(event.origin)) return
  const data = event.data as { type?: string; provider?: FilePreviewProvider; providerId?: string } | null
  if (data?.type === 'chemssh:file-manager:register-preview-provider' && data.provider) {
    if (!registeredProviderIds.value.includes(data.provider.id)) {
      registeredProviderIds.value = [...registeredProviderIds.value, data.provider.id]
    }
    emit('register-provider', data.provider, props.instanceId)
  } else if (data?.type === 'chemssh:file-manager:unregister-preview-provider' && data.providerId) {
    if (!registeredProviderIds.value.includes(data.providerId)) return
    registeredProviderIds.value = registeredProviderIds.value.filter(id => id !== data.providerId)
    emit('unregister-provider', data.providerId, props.instanceId)
  }
}

function cleanupRegisteredProviders() {
  for (const providerId of registeredProviderIds.value) {
    emit('unregister-provider', providerId, props.instanceId)
  }
  registeredProviderIds.value = []
}

function isTrustedPluginOrigin(origin: string) {
  if (origin === window.location.origin) return true
  if (!API_BASE) return false
  try {
    return origin === new URL(API_BASE, window.location.href).origin
  } catch {
    return false
  }
}
</script>
