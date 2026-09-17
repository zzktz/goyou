<script setup>
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import client from '@/api/client'

const apiBase = import.meta.env.VITE_API_BASE_URL || '当前访问域名'
const loading = ref(true)
const saving = ref(false)
const registrationEnabled = ref(true)
const defaultAccountValidDays = ref(365)

async function load() {
  loading.value = true
  try {
    const { data } = await client.get('/v1/admin/settings')
    registrationEnabled.value = data.registration_enabled
    defaultAccountValidDays.value = data.default_account_valid_days
  } catch (error) {
    if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '系统设置加载失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const { data } = await client.patch('/v1/admin/settings', {
      registration_enabled: registrationEnabled.value,
      default_account_valid_days: defaultAccountValidDays.value,
    })
    registrationEnabled.value = data.registration_enabled
    defaultAccountValidDays.value = data.default_account_valid_days
    message.success('设置已保存')
    return true
  } catch (error) {
    if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '系统设置保存失败')
    return false
  } finally {
    saving.value = false
  }
}

async function saveRegistration(checked) {
  registrationEnabled.value = checked
  const saved = await save()
  if (!saved) registrationEnabled.value = !checked
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-title">
      <div>
        <h1>系统设置</h1>
        <p>管理控制面接入信息与用户注册策略。</p>
      </div>
    </div>
    <a-card title="控制面连接" class="settings-card" :loading="loading">
      <a-form layout="vertical">
        <a-form-item label="管理 API">
          <a-input :value="apiBase" disabled />
        </a-form-item>
        <a-form-item label="管理员认证">
          <a-tag color="blue">独立管理员令牌</a-tag>
          <span class="setting-hint">管理员账号由服务器环境变量配置</span>
        </a-form-item>
        <a-form-item label="租约默认有效期">
          <a-input value="1 小时" disabled />
        </a-form-item>
      </a-form>
    </a-card>
    <a-card title="用户注册" class="settings-card">
      <a-form layout="vertical">
        <a-form-item label="开放注册">
          <a-switch
            v-model:checked="registrationEnabled"
            @change="saveRegistration"
            checked-children="开启"
            un-checked-children="关闭"
            :loading="loading || saving"
          />
          <span class="setting-hint">修改后自动保存；关闭后，客户端将无法获取注册验证码或创建新账号。</span>
        </a-form-item>
        <a-form-item label="新用户默认有效期">
          <a-input-number
            v-model:value="defaultAccountValidDays"
            :min="0"
            :max="3650"
            :precision="0"
            addon-after="天"
            style="width: 180px"
            :disabled="loading || saving"
          />
          <span class="setting-hint">从注册当天开始计算，0 表示注册当天到期；只影响之后注册的用户。</span>
        </a-form-item>
        <a-form-item>
          <a-button type="primary" :loading="saving" @click="save">保存设置</a-button>
        </a-form-item>
      </a-form>
    </a-card>
  </div>
</template>
