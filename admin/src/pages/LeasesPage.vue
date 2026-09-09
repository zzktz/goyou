<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import client from '@/api/client'

const rows = ref([])
const loading = ref(true)
const filters = reactive({ keyword: '', state: undefined })
const pagination = reactive({ current: 1, pageSize: 20, total: 0, showSizeChanger: true, showTotal: total => `共 ${total} 条租约` })
const colors = { active: 'success', expired: 'warning', revoked: 'error', unknown: 'default' }
const labels = { active: '使用中', expired: '已过期', revoked: '已撤销', unknown: '未知' }

function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
async function load(page = pagination.current, pageSize = pagination.pageSize) {
  loading.value = true
  try { const { data } = await client.get('/v1/admin/leases', { params: { page, page_size: pageSize, keyword: filters.keyword || undefined, state: filters.state } }); rows.value = data.items; pagination.current = data.pagination.page; pagination.pageSize = data.pagination.page_size; pagination.total = data.pagination.total } catch (error) { message.error(error.response?.data?.detail || '租约列表加载失败') } finally { loading.value = false }
}
function search() { load(1) }
function reset() { filters.keyword = ''; filters.state = undefined; load(1) }
function changePage(pager) { load(pager.current, pager.pageSize) }
function revoke(record) {
  Modal.confirm({ title: '撤销这条代理租约？', content: `用户 ${record.email} 将无法继续使用这条租约。`, okText: '确认撤销', okType: 'danger', cancelText: '取消', async onOk() { await client.post(`/v1/admin/leases/${record.id}/revoke`); message.success('租约已撤销'); await load() } })
}
onMounted(load)
</script>

<template>
  <div>
    <div class="page-title"><div><h1>代理租约</h1><p>查看客户端申请的中转凭据并在异常时立即撤销。</p></div><a-button @click="load()">刷新</a-button></div>
    <a-card class="filter-card"><a-space wrap><a-input v-model:value="filters.keyword" allow-clear placeholder="租约 ID、邮箱或设备 ID" style="width: 270px" @press-enter="search" /><a-select v-model:value="filters.state" allow-clear placeholder="全部状态" style="width: 140px"><a-select-option value="active">使用中</a-select-option><a-select-option value="expired">已过期</a-select-option><a-select-option value="revoked">已撤销</a-select-option></a-select><a-button type="primary" @click="search">查询</a-button><a-button @click="reset">重置</a-button></a-space></a-card>
    <a-card><a-table :data-source="rows" :loading="loading" :pagination="pagination" row-key="id" @change="changePage"><a-table-column title="用户" key="user" :width="230"><template #default="{ record }"><div class="user-cell"><a-avatar size="small">{{ record.name?.slice(0, 1) }}</a-avatar><div><strong>{{ record.name }}</strong><span>{{ record.email }}</span></div></div></template></a-table-column><a-table-column title="租约 ID" data-index="id" ellipsis /><a-table-column title="设备 ID" data-index="device_id" ellipsis /><a-table-column title="到期时间" key="expires_at" :width="190"><template #default="{ record }">{{ formatDate(record.expires_at) }}</template></a-table-column><a-table-column title="状态" key="state" :width="110"><template #default="{ record }"><a-tag :color="colors[record.state]">{{ labels[record.state] }}</a-tag></template></a-table-column><a-table-column title="操作" key="action" :width="100"><template #default="{ record }"><a-button v-if="record.state === 'active'" type="link" danger @click="revoke(record)">撤销</a-button><span v-else class="muted">无操作</span></template></a-table-column></a-table></a-card>
  </div>
</template>
