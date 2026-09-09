<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import client from '@/api/client'

const rows = ref([])
const loading = ref(true)
const updatingId = ref('')
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
onMounted(load)
</script>

<template>
  <div>
    <div class="page-title"><div><h1>用户管理</h1><p>查看注册用户、登录状态和租约使用情况。</p></div><a-button @click="load()">刷新</a-button></div>
    <a-card class="filter-card"><a-space wrap><a-input v-model:value="filters.keyword" allow-clear placeholder="邮箱、名称或用户 ID" style="width: 260px" @press-enter="search" /><a-select v-model:value="filters.state" allow-clear placeholder="全部状态" style="width: 140px"><a-select-option value="active">已启用</a-select-option><a-select-option value="disabled">已停用</a-select-option></a-select><a-button type="primary" @click="search">查询</a-button><a-button @click="reset">重置</a-button></a-space></a-card>
    <a-card><a-table :data-source="rows" :loading="loading" :pagination="pagination" row-key="id" @change="changePage"><a-table-column title="用户" key="user" :width="250"><template #default="{ record }"><div class="user-cell"><a-avatar size="small">{{ record.name?.slice(0, 1) }}</a-avatar><div><strong>{{ record.name }}</strong><span>{{ record.email }}</span></div></div></template></a-table-column><a-table-column title="用户 ID" data-index="id" ellipsis /><a-table-column title="租约数" data-index="lease_count" :width="100" /><a-table-column title="注册时间" key="created_at" :width="190"><template #default="{ record }">{{ formatDate(record.created_at) }}</template></a-table-column><a-table-column title="状态" key="status" :width="120"><template #default="{ record }"><a-switch :checked="record.enabled" :loading="updatingId === record.id" checked-children="启用" un-checked-children="停用" @change="changeStatus(record, $event)" /></template></a-table-column></a-table></a-card>
  </div>
</template>
