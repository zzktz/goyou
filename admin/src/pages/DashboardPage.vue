<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { CheckCircleOutlined, CloudServerOutlined, ReloadOutlined, TeamOutlined, KeyOutlined } from '@ant-design/icons-vue'
import client from '@/api/client'

const data = ref(null)
const loading = ref(true)

function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
async function load() {
  loading.value = true
  try { data.value = (await client.get('/v1/admin/overview')).data } catch (error) { message.error(error.response?.data?.detail || '总览加载失败') } finally { loading.value = false }
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
    <a-row :gutter="20">
      <a-col :xs="24" :xl="14"><a-card title="中转服务" class="panel-card" :loading="loading"><a-descriptions v-if="data" :column="1" bordered size="small"><a-descriptions-item label="服务状态"><a-tag color="success">正常</a-tag></a-descriptions-item><a-descriptions-item label="中转地址">{{ data.service.relay_host }}:{{ data.service.relay_port_start }}-{{ data.service.relay_port_end }}</a-descriptions-item><a-descriptions-item label="加密方式">{{ data.service.relay_method }}</a-descriptions-item><a-descriptions-item label="活跃刷新令牌">{{ data.active_refresh_tokens }}</a-descriptions-item></a-descriptions></a-card></a-col>
      <a-col :xs="24" :xl="10"><a-card title="管理入口" class="panel-card"><div class="quick-item"><CloudServerOutlined /><div><strong>API 健康检查</strong><span>/healthz</span></div><a-tag color="success">正常</a-tag></div><div class="quick-item"><TeamOutlined /><div><strong>普通用户登录</strong><span>客户端认证与租约申请</span></div><a-tag color="blue">启用</a-tag></div><div class="quick-item"><KeyOutlined /><div><strong>租约策略</strong><span>短期租约，支持手动撤销</span></div><a-tag color="orange">1 小时</a-tag></div></a-card></a-col>
    </a-row>
  </div>
</template>
