<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { CheckCircleOutlined, ClockCircleOutlined, CloudServerOutlined, KeyOutlined, ReloadOutlined, StopOutlined, TeamOutlined } from '@ant-design/icons-vue'
import client from '@/api/client'

const data = ref(null)
const loading = ref(true)

function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
function formatGigabytes(value) {
  const gigabytes = Number(value || 0) / 1000000000
  const precision = gigabytes >= 100 || gigabytes === 0 ? 0 : 1
  return `${gigabytes.toFixed(precision)} GB`
}
function formatBytes(value) {
  const bytes = Number(value || 0)
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const amount = bytes / (1024 ** index)
  const precision = index === 0 ? 0 : amount >= 100 ? 0 : amount >= 10 ? 1 : 2
  return `${amount.toFixed(precision)} ${units[index]}`
}
function monthlyUsageColor(usage) { return usage?.exceeded ? '#cf1322' : usage?.percentage >= 80 ? '#d48806' : '#1677ff' }
function relayStatusColor(value) { return value === 'ok' ? 'success' : value === 'warning' ? 'warning' : 'error' }
function relayHealthText(relay) { return relay.health?.status === 'ok' ? '正常' : relay.health?.status === 'warning' ? '需关注' : '异常' }
async function load() {
  loading.value = true
  try { data.value = (await client.get('/v1/admin/overview')).data } catch (error) { if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '总览加载失败') } finally { loading.value = false }
}
onMounted(load)
</script>

<template>
  <div>
    <div class="page-title"><div><h1>运行总览</h1><p>查看管理 API、用户账户和代理租约的实时状态。</p></div><a-button :loading="loading" @click="load"><template #icon><ReloadOutlined /></template>刷新</a-button></div>
    <a-alert v-if="data" type="success" show-icon class="service-alert"><template #message><span>管理 API 运行正常</span></template><template #description>数据更新时间：{{ formatDate(data.generated_at) }}</template><template #icon><CheckCircleOutlined /></template></a-alert>
    <a-row :gutter="20" class="stat-row">
      <a-col :xs="24" :sm="12" :xl="6"><a-card class="stat-card stat-card-blue" :loading="loading"><div class="stat-card-top"><div class="stat-card-icon"><TeamOutlined /></div><span>用户总数</span></div><div class="stat-card-value">{{ data?.users?.total || 0 }}</div><div class="stat-note">启用 {{ data?.users?.enabled || 0 }} · 停用 {{ data?.users?.disabled || 0 }}</div></a-card></a-col>
      <a-col :xs="24" :sm="12" :xl="6"><a-card class="stat-card stat-card-cyan" :loading="loading"><div class="stat-card-top"><div class="stat-card-icon"><KeyOutlined /></div><span>活跃租约</span></div><div class="stat-card-value">{{ data?.leases?.active || 0 }}</div><div class="stat-note">租约总数 {{ data?.leases?.total || 0 }}</div></a-card></a-col>
      <a-col :xs="24" :sm="12" :xl="6"><a-card class="stat-card stat-card-orange" :loading="loading"><div class="stat-card-top"><div class="stat-card-icon"><ClockCircleOutlined /></div><span>已过期租约</span></div><div class="stat-card-value">{{ data?.leases?.expired || 0 }}</div><div class="stat-note">已结束的历史租约</div></a-card></a-col>
      <a-col :xs="24" :sm="12" :xl="6"><a-card class="stat-card stat-card-red" :loading="loading"><div class="stat-card-top"><div class="stat-card-icon"><StopOutlined /></div><span>已撤销租约</span></div><div class="stat-card-value">{{ data?.leases?.revoked || 0 }}</div><div class="stat-note">管理员主动撤销</div></a-card></a-col>
    </a-row>
    <a-row :gutter="20" class="overview-resource-row">
      <a-col :xs="24" :xl="12"><a-card v-if="data?.monthly_usage" title="月度流量" class="panel-card monthly-usage-card" :loading="loading">
        <div class="monthly-usage-content">
          <a-progress type="circle" :percent="Math.min(data.monthly_usage.percentage, 100)" :stroke-color="monthlyUsageColor(data.monthly_usage)" :width="132" />
          <div class="monthly-usage-details">
            <div class="monthly-usage-total"><strong>{{ formatGigabytes(data.monthly_usage.used_bytes) }}</strong><span> / {{ formatGigabytes(data.monthly_usage.quota_bytes) }}</span></div>
            <div class="monthly-usage-label">已使用 / 月度固定额度</div>
            <a-descriptions :column="1" size="small">
              <a-descriptions-item label="统计周期">{{ data.monthly_usage.period_start }} 00:00 至 {{ data.monthly_usage.period_end }} 00:00（每月 10 日切换）</a-descriptions-item>
              <a-descriptions-item label="剩余流量">{{ formatGigabytes(data.monthly_usage.remaining_bytes) }}</a-descriptions-item>
              <a-descriptions-item label="上传 / 下载">{{ formatGigabytes(data.monthly_usage.upload_bytes) }} / {{ formatGigabytes(data.monthly_usage.download_bytes) }}</a-descriptions-item>
            </a-descriptions>
            <a-alert v-if="data.monthly_usage.exceeded" type="error" show-icon message="本月流量额度已用尽" />
          </div>
        </div>
      </a-card></a-col>
      <a-col :xs="24" :xl="12"><a-card v-if="data?.database" title="数据库与备份" class="panel-card database-card" :loading="loading">
        <a-descriptions :column="1" size="small">
          <a-descriptions-item label="数据库"><a-tag color="blue">{{ data.database.engine }}</a-tag> {{ formatBytes(data.database.size_bytes) }}</a-descriptions-item>
          <a-descriptions-item label="数据表">{{ data.database.table_count }} 张</a-descriptions-item>
          <a-descriptions-item label="流量明细">{{ data.database.usage_report_count.toLocaleString() }} 条 · 保留 {{ data.database.retention_days }} 天</a-descriptions-item>
          <a-descriptions-item label="并发设置"><a-tag color="green">{{ data.database.journal_mode.toUpperCase() }}</a-tag> busy timeout {{ data.database.busy_timeout_ms }} ms</a-descriptions-item>
          <a-descriptions-item label="最近备份" v-if="data.database.backup">{{ formatDate(data.database.backup.created_at) }} · {{ formatBytes(data.database.backup.size_bytes) }}</a-descriptions-item>
          <a-descriptions-item label="最近备份" v-else><span class="warning-text">尚未生成</span></a-descriptions-item>
        </a-descriptions>
        <a-alert v-if="data.database.backup" type="success" show-icon message="在线备份正常" :description="`${data.database.backup.filename}（保留 ${data.database.backup_retention_days} 天）`" />
        <a-alert v-else type="warning" show-icon message="等待首次在线备份" />
      </a-card></a-col>
    </a-row>
    <a-card v-if="data?.relays?.length" title="Relay 节点" class="panel-card relay-list-card" :loading="loading">
      <a-table :data-source="data.relays" row-key="id" :pagination="false" :scroll="{ x: 900 }" size="small">
        <a-table-column title="节点" key="name"><template #default="{ record }"><strong>{{ record.name }}</strong><br><span class="muted">{{ record.id }}</span></template></a-table-column>
        <a-table-column title="地址" key="host"><template #default="{ record }">{{ record.host }}<br><span class="muted">{{ record.port_start }}-{{ record.port_end }}</span></template></a-table-column>
        <a-table-column title="健康状态" key="health"><template #default="{ record }"><a-tag :color="relayStatusColor(record.health?.status)">{{ relayHealthText(record) }}</a-tag><br><span class="muted">{{ record.health?.heartbeat_stale ? '心跳超时' : record.health?.process_running ? '进程运行中' : '进程未运行' }}</span></template></a-table-column>
        <a-table-column title="租约" key="leases"><template #default="{ record }">{{ record.active_lease_count }} 活跃<br><a-tag :color="record.draining ? 'warning' : record.enabled ? 'success' : 'error'">{{ record.draining ? '排空中' : record.enabled ? '已启用' : '已停用' }}</a-tag></template></a-table-column>
        <a-table-column title="端口检查" key="ports"><template #default="{ record }"><span v-if="record.health?.missing_ports?.length" class="warning-text">缺失 {{ record.health.missing_ports.length }}</span><span v-else>正常</span><span v-if="record.health?.duplicate_ports?.length" class="error-text"> · 冲突 {{ record.health.duplicate_ports.length }}</span></template></a-table-column>
      </a-table>
    </a-card>
    <a-row :gutter="20">
      <a-col :xs="24" :xl="12"><a-card title="中转服务" class="panel-card" :loading="loading"><a-descriptions v-if="data" :column="1" bordered size="small"><a-descriptions-item label="加密方式">{{ data.service?.relay_method || '-' }}</a-descriptions-item><a-descriptions-item label="活跃刷新令牌">{{ data.active_refresh_tokens || 0 }}</a-descriptions-item></a-descriptions></a-card></a-col>
      <a-col :xs="24" :xl="12"><a-card title="管理入口" class="panel-card"><div class="quick-item"><CloudServerOutlined /><div><strong>API 健康检查</strong><span>/healthz</span></div><a-tag color="success">正常</a-tag></div><div class="quick-item"><TeamOutlined /><div><strong>普通用户登录</strong><span>客户端认证与租约申请</span></div><a-tag color="blue">启用</a-tag></div><div class="quick-item"><KeyOutlined /><div><strong>租约策略</strong><span>短期租约，支持手动撤销</span></div><a-tag color="orange">1 小时</a-tag></div></a-card></a-col>
    </a-row>
  </div>
</template>
