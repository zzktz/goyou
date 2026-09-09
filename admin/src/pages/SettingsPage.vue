<script setup>
import { message } from 'ant-design-vue'
const apiBase = import.meta.env.VITE_API_BASE_URL || '当前访问域名'
function save() { message.success('设置已保存') }
</script>

<template>
  <div><div class="page-title"><div><h1>系统设置</h1><p>查看当前控制面接入信息与后台运行参数。</p></div></div><a-card title="控制面连接" class="settings-card"><a-form layout="vertical"><a-form-item label="管理 API"><a-input :value="apiBase" disabled /></a-form-item><a-form-item label="管理员认证"><a-tag color="blue">独立管理员令牌</a-tag><span class="setting-hint">管理员账号由服务器环境变量配置</span></a-form-item><a-form-item label="租约默认有效期"><a-input value="1 小时" disabled /></a-form-item><a-form-item><a-button type="primary" @click="save">保存设置</a-button></a-form-item></a-form></a-card></div>
</template>
