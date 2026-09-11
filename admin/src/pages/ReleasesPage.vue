<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { DeleteOutlined, EditOutlined, UploadOutlined } from '@ant-design/icons-vue'
import client from '@/api/client'

const platforms = [
  { key: 'windows-x86_64', label: 'Windows 64 位', packageHint: '-setup.exe' },
  { key: 'darwin-aarch64', label: 'macOS Apple 芯片', packageHint: '.app.tar.gz' },
  { key: 'darwin-x86_64', label: 'macOS Intel 芯片', packageHint: '.app.tar.gz' },
]
const rows = ref([])
const loading = ref(true)
const createOpen = ref(false)
const editOpen = ref(false)
const saving = ref(false)
const editing = ref(null)
const form = reactive({ version: '', notes: '' })
const selected = reactive({})
const artifactInputs = {}
const signatureInputs = {}
const uploading = ref('')

function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
function formatBytes(value) {
  const mb = Number(value || 0) / 1000000
  return `${mb.toFixed(mb >= 1000 ? 1 : mb >= 10 ? 1 : 2)} MB`
}
function assetFor(record, platform) { return record.assets?.find(asset => asset.platform === platform) }
function fileKey(record, platform) { return `${record.id}:${platform}` }
function resetFiles() { rows.value.forEach(record => platforms.forEach(({ key }) => { selected[fileKey(record, key)] = { artifact: null, signature: null } })) }
function openCreate() { Object.assign(form, { version: '', notes: '' }); createOpen.value = true }
function openEdit(record) { editing.value = record; Object.assign(form, { version: record.version, notes: record.notes || '' }); editOpen.value = true }
function chooseFile(key, kind, event) {
  selected[key] = selected[key] || { artifact: null, signature: null }
  selected[key][kind] = event.target.files?.[0] || null
  event.target.value = ''
}
async function load() {
  loading.value = true
  try { rows.value = (await client.get('/v1/admin/releases')).data.items || []; resetFiles() }
  catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '版本列表加载失败') }
  finally { loading.value = false }
}
async function createRelease() {
  if (!/^\d+\.\d+\.\d+/.test(form.version)) return message.warning('请输入正确的版本号，例如 1.0.22')
  saving.value = true
  try {
    await client.post('/v1/admin/releases', { version: form.version, notes: form.notes })
    createOpen.value = false; message.success('版本草稿已创建'); await load()
  } catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '创建版本失败') }
  finally { saving.value = false }
}
async function saveNotes() {
  if (!editing.value) return
  saving.value = true
  try {
    await client.patch(`/v1/admin/releases/${editing.value.id}`, { notes: form.notes })
    editOpen.value = false; message.success('更新说明已保存'); await load()
  } catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '保存更新说明失败') }
  finally { saving.value = false }
}
async function uploadAsset(record, platform) {
  const files = selected[fileKey(record, platform)] || {}
  if (!files.artifact || !files.signature) return message.warning('请同时选择更新包和 .sig 签名文件')
  uploading.value = `${record.id}:${platform}`
  const body = new FormData(); body.append('artifact', files.artifact); body.append('signature', files.signature)
  try {
    await client.post(`/v1/admin/releases/${record.id}/assets/${platform}`, body)
    selected[fileKey(record, platform)] = { artifact: null, signature: null }; message.success('更新包上传成功'); await load()
  } catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '更新包上传失败') }
  finally { uploading.value = '' }
}
async function publish(record) {
  try { await client.post(`/v1/admin/releases/${record.id}/publish`); message.success('版本已发布'); await load() }
  catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '版本发布失败') }
}
async function unpublish(record) {
  try { await client.post(`/v1/admin/releases/${record.id}/unpublish`); message.success('版本已撤回'); await load() }
  catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '版本撤回失败') }
}
function deleteRelease(record) {
  Modal.confirm({ title: '删除版本草稿', content: `确定删除 ${record.version} 及已上传文件吗？`, okText: '删除', okType: 'danger', cancelText: '取消', async onOk() {
    try { await client.delete(`/v1/admin/releases/${record.id}`); message.success('版本草稿已删除'); await load() }
    catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '删除版本失败') }
  } })
}
onMounted(() => { resetFiles(); load() })
</script>

<template>
  <div>
    <div class="page-title"><div><h1>版本发布</h1><p>在后台管理 GoYou 更新版本、安装包和签名文件，发布后客户端会自动读取。</p></div><a-space><a-button @click="load">刷新</a-button><a-button type="primary" @click="openCreate">创建版本</a-button></a-space></div>
    <a-alert type="info" show-icon message="发布要求" description="每个版本需要同时上传 Windows、macOS Apple 芯片和 macOS Intel 芯片的 updater 安装包及 .sig 文件，发布后才会对客户端可见。" class="release-notice" />
    <a-card :bordered="false"><a-spin :spinning="loading"><a-empty v-if="!rows.length && !loading" description="还没有版本草稿" /><a-collapse v-else accordion>
      <a-collapse-panel v-for="record in rows" :key="record.id">
        <template #header><div class="release-header"><strong>v{{ record.version }}</strong><a-tag :color="record.status === 'published' ? 'green' : 'default'">{{ record.status === 'published' ? '已发布' : '草稿' }}</a-tag><span class="muted">{{ record.published_at ? `发布于 ${formatDate(record.published_at)}` : `创建于 ${formatDate(record.created_at)}` }}</span></div></template>
        <p class="release-notes">{{ record.notes || '暂无更新说明' }}</p>
        <div class="release-assets"><div v-for="platform in platforms" :key="platform.key" class="release-asset"><div><strong>{{ platform.label }}</strong><div v-if="assetFor(record, platform.key)" class="muted">{{ assetFor(record, platform.key).filename }} · {{ formatBytes(assetFor(record, platform.key).size_bytes) }} · SHA256 {{ assetFor(record, platform.key).sha256.slice(0, 12) }}…</div><div v-else class="muted">尚未上传（{{ platform.packageHint }}）</div></div><a-tag v-if="assetFor(record, platform.key)" color="green">已上传</a-tag><a-tag v-else>待上传</a-tag><template v-if="record.status === 'draft'"><input :ref="el => artifactInputs[fileKey(record, platform.key)] = el" type="file" hidden @change="chooseFile(fileKey(record, platform.key), 'artifact', $event)" /><input :ref="el => signatureInputs[fileKey(record, platform.key)] = el" type="file" hidden accept=".sig" @change="chooseFile(fileKey(record, platform.key), 'signature', $event)" /><a-button size="small" @click="artifactInputs[fileKey(record, platform.key)]?.click()">选择安装包</a-button><a-button size="small" @click="signatureInputs[fileKey(record, platform.key)]?.click()">选择签名</a-button><a-button size="small" type="primary" :loading="uploading === `${record.id}:${platform.key}`" :disabled="!(selected[fileKey(record, platform.key)]?.artifact && selected[fileKey(record, platform.key)]?.signature)" @click="uploadAsset(record, platform.key)"><UploadOutlined />上传</a-button></template></div></div>
        <div class="release-actions"><a-button v-if="record.status === 'draft'" @click="openEdit(record)"><EditOutlined />编辑说明</a-button><a-button v-if="record.status === 'draft'" type="primary" @click="publish(record)">发布版本</a-button><a-button v-else @click="unpublish(record)">撤回版本</a-button><a-button v-if="record.status === 'draft'" danger @click="deleteRelease(record)"><DeleteOutlined />删除草稿</a-button></div>
      </a-collapse-panel>
    </a-collapse></a-spin></a-card>
    <a-modal v-model:open="createOpen" title="创建版本草稿" :confirm-loading="saving" ok-text="创建" cancel-text="取消" @ok="createRelease"><a-form layout="vertical"><a-form-item label="版本号" required><a-input v-model:value="form.version" placeholder="例如 1.0.22" /></a-form-item><a-form-item label="更新说明"><a-textarea v-model:value="form.notes" :rows="6" placeholder="描述本次版本更新内容" /></a-form-item></a-form></a-modal>
    <a-modal v-model:open="editOpen" title="编辑更新说明" :confirm-loading="saving" ok-text="保存" cancel-text="取消" @ok="saveNotes"><a-textarea v-model:value="form.notes" :rows="8" placeholder="描述本次版本更新内容" /></a-modal>
  </div>
</template>
