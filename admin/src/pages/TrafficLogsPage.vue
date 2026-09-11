<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import client from '@/api/client'

function today() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai' }).format(new Date())
}

const rows = ref([])
const loading = ref(true)
const filters = reactive({ date: today(), keyword: '' })
const pagination = reactive({ current: 1, pageSize: 50, total: 0, showSizeChanger: true, showTotal: total => `共 ${total} 条记录` })

function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
function formatBytes(value) {
  const megabytes = Number(value || 0) / 1000000
  const precision = megabytes < 1 ? 2 : megabytes < 10 || megabytes >= 1000 ? 1 : 0
  return `${megabytes.toFixed(precision)} MB`
}
function formatTarget(record) {
  if (!record.target_host) return '未识别'
  return record.target_port ? `${record.target_host}:${record.target_port}` : record.target_host
}
async function load(page = pagination.current, pageSize = pagination.pageSize) {
  loading.value = true
  try {
    const { data } = await client.get('/v1/admin/traffic-logs', {
      params: { date: filters.date || undefined, keyword: filters.keyword || undefined, page, page_size: pageSize },
    })
    rows.value = data.items
    pagination.current = data.pagination.page
    pagination.pageSize = data.pagination.page_size
    pagination.total = data.pagination.total
  } catch (error) {
    if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '流量日志加载失败')
  } finally { loading.value = false }
}
function search() { load(1) }
function reset() { filters.date = today(); filters.keyword = ''; load(1) }
function changePage(pager) { load(pager.current, pager.pageSize) }
onMounted(load)
</script>

<template>
  <div>
    <div class="page-title"><div><h1>流量日志</h1><p>查看用户代理连接产生的流量及目标域名、端口。</p></div><a-button @click="load()">刷新</a-button></div>
    <a-card class="filter-card"><a-space wrap><a-input v-model:value="filters.date" type="date" style="width: 170px" /><a-input v-model:value="filters.keyword" allow-clear placeholder="用户、租约、连接 ID 或目标域名" style="width: 300px" @press-enter="search" /><a-button type="primary" @click="search">查询</a-button><a-button @click="reset">重置</a-button></a-space></a-card>
    <a-card><a-table :data-source="rows" :loading="loading" :pagination="pagination" row-key="report_id" @change="changePage"><a-table-column title="时间" key="reported_at" :width="180"><template #default="{ record }">{{ formatDate(record.reported_at) }}</template></a-table-column><a-table-column title="用户" key="user" :width="220"><template #default="{ record }"><div class="user-cell"><a-avatar size="small">{{ record.name?.slice(0, 1) }}</a-avatar><div><strong>{{ record.name }}</strong><span>{{ record.email }}</span></div></div></template></a-table-column><a-table-column title="目标域名 / 端口" key="target" :width="220"><template #default="{ record }"><a-tag v-if="record.target_host" color="blue">{{ formatTarget(record) }}</a-tag><span v-else class="muted">未识别</span></template></a-table-column><a-table-column title="上传" key="upload_bytes" :width="110"><template #default="{ record }">{{ formatBytes(record.upload_bytes) }}</template></a-table-column><a-table-column title="下载" key="download_bytes" :width="110"><template #default="{ record }">{{ formatBytes(record.download_bytes) }}</template></a-table-column><a-table-column title="总流量" key="total_bytes" :width="110"><template #default="{ record }"><strong>{{ formatBytes(record.total_bytes) }}</strong></template></a-table-column><a-table-column title="连接 ID" data-index="connection_id" ellipsis /></a-table></a-card>
  </div>
</template>
