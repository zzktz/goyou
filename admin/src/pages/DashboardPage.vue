<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { CheckCircleOutlined, CloudServerOutlined, ReloadOutlined, TeamOutlined, KeyOutlined } from '@ant-design/icons-vue'
import client from '@/api/client'

const data = ref(null)
const loading = ref(true)

function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
function relayStatusColor(value) { return value === 'ok' ? 'success' : value === 'warning' ? 'warning' : 'error' }
function relayStatusLabel(value) { return value === 'ok' ? '正常' : value === 'warning' ? '需关注' : '异常' }
function formatPorts(values) { return values?.length ? values.join('、') : '无' }
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
      <a-col :xs="24" :sm="12" :xl="6"><a-card class="stat-card"><a-statistic title="用户总数" :value="data?.users.total || 0" :loading="loading"><template #prefix><TeamOutlined /></template></a-statistic><div class="stat-note">启用 {{ data?.users.enabled || 0 }} · 停用 {{ data?.users.disabled || 0 }}</div></a-card></a-col>
      <a-col :xs="24" :sm="12" :xl="6"><a-card class="stat-card"><a-statistic title="活跃租约" :value="data?.leases.active || 0" :loading="loading"><template #prefix><KeyOutlined /></template></a-statistic><div class="stat-note">租约总数 {{ data?.leases.total || 0 }}</div></a-card></a-col>
      <a-col :xs="24" :sm="12" :xl="6"><a-card class="stat-card"><a-statistic title="已过期租约" :value="data?.leases.expired || 0" :loading="loading" /></a-card></a-col>
      <a-col :xs="24" :sm="12" :xl="6"><a-card class="stat-card"><a-statistic title="已撤销租约" :value="data?.leases.revoked || 0" :loading="loading" /></a-card></a-col>
    </a-row>
    <a-card v-if="data?.relays?.length" title="Relay 节点" class="panel-card relay-list-card" :loading="loading">
      <a-table :data-source="data.relays" row-key="id" :pagination="false" :scroll="{ x: 900 }" size="small">
        <a-table-column title="节点" key="name"><template #default="{ record }"><strong>{{ record.name }}</strong><br><span class="muted">{{ record.id }}</span></template></a-table-column>
        <a-table-column title="地址" key="host"><template #default="{ record }">{{ record.host }}<br><span class="muted">{{ record.port_start }}-{{ record.port_end }}</span></template></a-table-column>
        <a-table-column title="健康状态" key="health"><template #default="{ record }"><a-tag :color="relayStatusColor(record.health?.status)">{{ relayHealthText(record) }}</a-tag><br><span class="muted">{{ record.health?.heartbeat_stale ? '心跳超时' : record.health?.process_running ? '进程运行中' : '进程未运行' }}</span></template></a-table-column>
        <a-table-column title="租约" key="leases"><template #default="{ record }">{{ record.active_lease_count }} 活跃<br><span class="muted">{{ record.draining ? '排空中' : record.enabled ? '已启用' : '已停用' }}</span></template></a-table-column>
        <a-table-column title="端口检查" key="ports"><template #default="{ record }"><span v-if="record.health?.missing_ports?.length" class="warning-text">缺失 {{ record.health.missing_ports.length }}</span><span v-else>正常</span><span v-if="record.health?.duplicate_ports?.length" class="error-text"> · 冲突 {{ record.health.duplicate_ports.length }}</span></template></a-table-column>
      </a-table>
    </a-card>
    <a-row :gutter="20">
      <a-col :xs="24" :xl="14"><a-card title="中转服务" class="panel-card" :loading="loading"><a-descriptions v-if="data" :column="1" bordered size="small"><a-descriptions-item label="服务状态"><a-tag :color="relayStatusColor(data.relay?.status)">{{ relayStatusLabel(data.relay?.status) }}</a-tag></a-descriptions-item><a-descriptions-item label="sing-box 进程"><a-tag :color="data.relay?.process_running ? 'success' : 'error'">{{ data.relay?.process_running ? '运行中' : '未运行' }}</a-tag></a-descriptions-item><a-descriptions-item label="心跳状态"><span>{{ data.relay?.heartbeat_stale ? '已超时' : '正常' }} · {{ formatDate(data.relay?.heartbeat_at) }}</span></a-descriptions-item><a-descriptions-item label="端口冲突"><a-tag :color="data.relay?.duplicate_ports?.length ? 'error' : 'success'">{{ data.relay?.duplicate_ports?.length ? formatPorts(data.relay.duplicate_ports) : '无' }}</a-tag></a-descriptions-item><a-descriptions-item label="未监听租约端口"><a-tag :color="data.relay?.missing_ports?.length ? 'warning' : 'success'">{{ data.relay?.missing_ports?.length ? formatPorts(data.relay.missing_ports) : '无' }}</a-tag></a-descriptions-item><a-descriptions-item label="监听端口">{{ formatPorts(data.relay?.listen_ports) }}</a-descriptions-item><a-descriptions-item label="中转地址">{{ data.service.relay_host }}:{{ data.service.relay_port_start }}-{{ data.service.relay_port_end }}</a-descriptions-item><a-descriptions-item label="加密方式">{{ data.service.relay_method }}</a-descriptions-item><a-descriptions-item label="活跃刷新令牌">{{ data.active_refresh_tokens }}</a-descriptions-item></a-descriptions><a-alert v-if="data.relay?.error" type="warning" show-icon :message="data.relay.error" class="relay-health-alert" /></a-card></a-col>
      <a-col :xs="24" :xl="10"><a-card title="管理入口" class="panel-card"><div class="quick-item"><CloudServerOutlined /><div><strong>API 健康检查</strong><span>/healthz</span></div><a-tag color="success">正常</a-tag></div><div class="quick-item"><TeamOutlined /><div><strong>普通用户登录</strong><span>客户端认证与租约申请</span></div><a-tag color="blue">启用</a-tag></div><div class="quick-item"><KeyOutlined /><div><strong>租约策略</strong><span>短期租约，支持手动撤销</span></div><a-tag color="orange">1 小时</a-tag></div></a-card></a-col>
    </a-row>
  </div>
</template>
