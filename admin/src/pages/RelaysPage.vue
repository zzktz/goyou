<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import client from '@/api/client'

const rows = ref([])
const loading = ref(true)
const saving = ref(false)
const modalOpen = ref(false)
const editingId = ref('')
const tokenOpen = ref(false)
const createdToken = ref('')

const emptyForm = () => ({
  name: '',
  host: '',
  port_start: 30000,
  port_end: 39999,
  method: 'chacha20-ietf-poly1305',
  legacy_port: 0,
  region: '',
  weight: 100,
  enabled: true,
  draining: false,
  regenerate_token: false,
})
const form = reactive(emptyForm())

function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
function statusColor(relay) { return relay.health?.status === 'ok' ? 'success' : relay.health?.status === 'warning' ? 'warning' : 'error' }
function statusLabel(relay) { return relay.health?.status === 'ok' ? '正常' : relay.health?.status === 'warning' ? '需关注' : '异常' }
function healthText(relay) { return relay.health?.heartbeat_stale ? '心跳超时' : relay.health?.process_running ? '进程运行中' : '进程未运行' }

async function load() {
  loading.value = true
  try { rows.value = (await client.get('/v1/admin/relays')).data.items } catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || 'Relay 列表加载失败') } finally { loading.value = false }
}

function openCreate() {
  Object.assign(form, emptyForm())
  editingId.value = ''
  modalOpen.value = true
}

function openEdit(record) {
  Object.assign(form, {
    name: record.name,
    host: record.host,
    port_start: record.port_start,
    port_end: record.port_end,
    method: record.method,
    legacy_port: record.legacy_port,
    region: record.region,
    weight: record.weight,
    enabled: record.enabled,
    draining: record.draining,
    regenerate_token: false,
  })
  editingId.value = record.id
  modalOpen.value = true
}

async function save() {
  if (!form.name.trim() || !form.host.trim()) return message.warning('请填写 relay 名称和地址')
  if (form.port_start > form.port_end) return message.warning('端口起始值不能大于结束值')
  saving.value = true
  try {
    const response = editingId.value
      ? await client.patch(`/v1/admin/relays/${editingId.value}`, form)
      : await client.post('/v1/admin/relays', form)
    modalOpen.value = false
    if (response.data.token) {
      createdToken.value = response.data.token
      tokenOpen.value = true
    }
    message.success(editingId.value ? 'Relay 设置已保存' : 'Relay 已创建')
    await load()
  } catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || 'Relay 设置保存失败') } finally { saving.value = false }
}

async function updateRelay(record, patch) {
  try { await client.patch(`/v1/admin/relays/${record.id}`, patch); message.success('Relay 状态已更新'); await load() } catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || 'Relay 状态更新失败') }
}

function remove(record) {
  Modal.confirm({
    title: '删除这个 relay？',
    content: '只有没有任何历史租约的 relay 才能删除；已有租约的节点请使用停用或排空。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try { await client.delete(`/v1/admin/relays/${record.id}`); message.success('Relay 已删除'); await load() } catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || 'Relay 删除失败'); throw error }
    },
  })
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-title">
      <div><h1>Relay 管理</h1><p>独立配置多个中转节点，管理分配、排空和健康状态。</p></div>
      <a-space><a-button @click="load">刷新</a-button><a-button type="primary" @click="openCreate">新增 Relay</a-button></a-space>
    </div>
    <a-alert type="info" show-icon class="service-alert" message="新建 Relay 后，请将一次性 Token 配置到对应 relay Agent 的 RELAY_TOKEN。" />
    <a-card :loading="loading">
      <a-table :data-source="rows" :loading="loading" row-key="id" :pagination="false" :scroll="{ x: 1180 }">
        <a-table-column title="Relay" key="name" :width="210"><template #default="{ record }"><div class="user-cell"><a-tag :color="statusColor(record)">{{ statusLabel(record) }}</a-tag><div><strong>{{ record.name }}</strong><span>{{ record.id }}</span></div></div></template></a-table-column>
        <a-table-column title="地址" key="address" :width="230"><template #default="{ record }">{{ record.host }}<br><span class="muted">端口 {{ record.port_start }}-{{ record.port_end }}{{ record.legacy_port ? ` · 旧端口 ${record.legacy_port}` : '' }}</span></template></a-table-column>
        <a-table-column title="区域 / 权重" key="region" :width="130"><template #default="{ record }">{{ record.region || '-' }} / {{ record.weight }}</template></a-table-column>
        <a-table-column title="租约" key="leases" :width="110"><template #default="{ record }">{{ record.active_lease_count }} 活跃<br><span class="muted">共 {{ record.total_lease_count }}</span></template></a-table-column>
        <a-table-column title="健康" key="health" :width="150"><template #default="{ record }">{{ healthText(record) }}<br><span class="muted">{{ formatDate(record.health?.heartbeat_at) }}</span></template></a-table-column>
        <a-table-column title="状态" key="state" :width="150"><template #default="{ record }"><a-switch :checked="record.enabled" checked-children="启用" un-checked-children="停用" @change="(checked) => updateRelay(record, { enabled: checked })" /><a-tag v-if="record.draining" color="orange" style="margin-left: 8px">排空中</a-tag></template></a-table-column>
        <a-table-column title="操作" key="action" :width="180"><template #default="{ record }"><a-space><a-button type="link" @click="openEdit(record)">编辑</a-button><a-button type="link" @click="updateRelay(record, { draining: !record.draining })">{{ record.draining ? '取消排空' : '排空' }}</a-button><a-button v-if="record.id !== 'default'" type="link" danger @click="remove(record)">删除</a-button></a-space></template></a-table-column>
      </a-table>
    </a-card>

    <a-modal v-model:open="modalOpen" :title="editingId ? '编辑 Relay' : '新增 Relay'" :confirm-loading="saving" ok-text="保存" cancel-text="取消" @ok="save">
      <a-form layout="vertical">
        <a-form-item label="名称" required><a-input v-model:value="form.name" placeholder="例如：东京节点" /></a-form-item>
        <a-form-item label="主机地址" required><a-input v-model:value="form.host" placeholder="relay.example.com" /></a-form-item>
        <a-row :gutter="12"><a-col :span="12"><a-form-item label="租约端口起始"><a-input-number v-model:value="form.port_start" :min="1" :max="65535" style="width: 100%" /></a-form-item></a-col><a-col :span="12"><a-form-item label="租约端口结束"><a-input-number v-model:value="form.port_end" :min="1" :max="65535" style="width: 100%" /></a-form-item></a-col></a-row>
        <a-row :gutter="12"><a-col :span="12"><a-form-item label="加密方式"><a-input v-model:value="form.method" /></a-form-item></a-col><a-col :span="12"><a-form-item label="旧固定端口"><a-input-number v-model:value="form.legacy_port" :min="0" :max="65535" style="width: 100%" /></a-form-item></a-col></a-row>
        <a-row :gutter="12"><a-col :span="12"><a-form-item label="区域"><a-input v-model:value="form.region" placeholder="东京 / 香港" /></a-form-item></a-col><a-col :span="12"><a-form-item label="权重"><a-input-number v-model:value="form.weight" :min="1" :max="1000" style="width: 100%" /></a-form-item></a-col></a-row>
        <a-form-item v-if="!editingId" label="创建后状态"><a-switch v-model:checked="form.enabled" checked-children="启用" un-checked-children="停用" /></a-form-item>
        <a-form-item v-else label="重新生成 Agent Token"><a-switch v-model:checked="form.regenerate_token" checked-children="生成" un-checked-children="保持" /></a-form-item>
      </a-form>
    </a-modal>
    <a-modal v-model:open="tokenOpen" title="Relay Agent Token" :footer="null"><a-alert type="warning" show-icon message="Token 只在本次显示，请立即保存到对应 relay Agent。" /><a-typography-paragraph copyable :content="createdToken" style="margin-top: 16px; word-break: break-all" /></a-modal>
  </div>
</template>
