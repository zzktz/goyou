<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import client from '@/api/client'

const rows = ref([])
const loading = ref(true)
const updatingId = ref('')
const quotaOpen = ref(false)
const quotaSaving = ref(false)
const quotaUser = ref(null)
const quotaMegabytes = ref(500)
const filters = reactive({ keyword: '', state: undefined })
const pagination = reactive({ current: 1, pageSize: 20, total: 0, showSizeChanger: true, showTotal: total => `共 ${total} 个用户` })

function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
async function load(page = pagination.current, pageSize = pagination.pageSize) {
  loading.value = true
  try {
    const { data } = await client.get('/v1/admin/users', { params: { page, page_size: pageSize, keyword: filters.keyword || undefined, state: filters.state } })
    rows.value = data.items; pagination.current = data.pagination.page; pagination.pageSize = data.pagination.page_size; pagination.total = data.pagination.total
  } catch (error) { message.error(error.response?.data?.detail || '用户列表加载失败') } finally { loading.value = false }
}
function search() { load(1) }
function reset() { filters.keyword = ''; filters.state = undefined; load(1) }
function changePage(pager) { load(pager.current, pager.pageSize) }
async function changeStatus(record, enabled) {
  updatingId.value = record.id
  try { await client.patch(`/v1/admin/users/${record.id}/status`, { enabled }); record.enabled = enabled; message.success(enabled ? '用户已启用' : '用户已停用') } catch (error) { message.error(error.response?.data?.detail || '用户状态更新失败') } finally { updatingId.value = '' }
}
function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes < 1000000) return `${bytes} B`
  return `${(bytes / 1000000).toFixed(bytes >= 1000000000 ? 1 : 0)} MB`
}
function openQuota(record) {
  quotaUser.value = record
  quotaMegabytes.value = Math.round(Number(record.daily_quota_bytes || 0) / 1000000)
  quotaOpen.value = true
}
async function saveQuota() {
  if (!quotaUser.value || !Number.isFinite(Number(quotaMegabytes.value)) || Number(quotaMegabytes.value) < 0) return
  quotaSaving.value = true
  try {
    const { data } = await client.patch(`/v1/admin/users/${quotaUser.value.id}/quota`, { daily_limit_bytes: Math.round(Number(quotaMegabytes.value) * 1000000) })
    quotaUser.value.daily_quota_bytes = data.quota_bytes
    quotaUser.value.used_bytes = data.used_bytes
    quotaUser.value.quota_exceeded = data.exceeded
    quotaOpen.value = false
    message.success('每日流量额度已更新')
  } catch (error) { message.error(error.response?.data?.detail || '流量额度更新失败') } finally { quotaSaving.value = false }
}
onMounted(load)
</script>

<template>
  <div>
    <div class="page-title"><div><h1>用户管理</h1><p>查看注册用户、登录状态和租约使用情况。</p></div><a-button @click="load()">刷新</a-button></div>
    <a-card class="filter-card"><a-space wrap><a-input v-model:value="filters.keyword" allow-clear placeholder="邮箱、名称或用户 ID" style="width: 260px" @press-enter="search" /><a-select v-model:value="filters.state" allow-clear placeholder="全部状态" style="width: 140px"><a-select-option value="active">已启用</a-select-option><a-select-option value="disabled">已停用</a-select-option></a-select><a-button type="primary" @click="search">查询</a-button><a-button @click="reset">重置</a-button></a-space></a-card>
    <a-card><a-table :data-source="rows" :loading="loading" :pagination="pagination" row-key="id" @change="changePage"><a-table-column title="用户" key="user" :width="250"><template #default="{ record }"><div class="user-cell"><a-avatar size="small">{{ record.name?.slice(0, 1) }}</a-avatar><div><strong>{{ record.name }}</strong><span>{{ record.email }}</span></div></div></template></a-table-column><a-table-column title="用户 ID" data-index="id" ellipsis /><a-table-column title="今日流量" key="usage" :width="170"><template #default="{ record }"><span :class="{ 'quota-exceeded': record.quota_exceeded }">{{ formatBytes(record.used_bytes) }} / {{ formatBytes(record.daily_quota_bytes) }}</span></template></a-table-column><a-table-column title="租约数" data-index="lease_count" :width="90" /><a-table-column title="注册时间" key="created_at" :width="190"><template #default="{ record }">{{ formatDate(record.created_at) }}</template></a-table-column><a-table-column title="状态" key="status" :width="120"><template #default="{ record }"><a-switch :checked="record.enabled" :loading="updatingId === record.id" checked-children="启用" un-checked-children="停用" @change="changeStatus(record, $event)" /></template></a-table-column><a-table-column title="操作" key="action" :width="110"><template #default="{ record }"><a-button type="link" @click="openQuota(record)">设置额度</a-button></template></a-table-column></a-table></a-card>
    <a-modal v-model:open="quotaOpen" title="设置每日流量额度" :confirm-loading="quotaSaving" ok-text="保存" cancel-text="取消" @ok="saveQuota"><p v-if="quotaUser">用户：{{ quotaUser.email }}</p><a-form-item label="每日额度"><a-input-number v-model:value="quotaMegabytes" :min="0" :max="10000000" :precision="0" addon-after="MB" style="width: 100%" /></a-form-item><p class="muted">当前已使用：{{ formatBytes(quotaUser?.used_bytes) }}。设置为 0 MB 将禁止当天代理流量。</p></a-modal>
  </div>
</template>
