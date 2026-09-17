<script setup>
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import client from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const loading = ref(true)
const saving = ref(false)
const form = reactive({
  name: '',
  email: '',
  current_password: '',
  new_password: '',
  confirm_password: '',
})

async function load() {
  loading.value = true
  try {
    const { data } = await client.get('/v1/admin/profile')
    form.name = data.user.name
    form.email = data.user.email
    auth.user = data.user
  } catch (error) {
    if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '个人信息加载失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.current_password) {
    message.error('请输入当前密码')
    return
  }
  if (!form.name.trim()) {
    message.error('请输入管理员姓名')
    return
  }
  if (form.new_password && form.new_password !== form.confirm_password) {
    message.error('两次输入的新密码不一致')
    return
  }
  saving.value = true
  try {
    const { data } = await client.patch('/v1/admin/profile', {
      current_password: form.current_password,
      name: form.name.trim(),
      email: form.email,
      new_password: form.new_password || null,
    })
    auth.user = data.user
    form.name = data.user.name
    form.email = data.user.email
    form.current_password = ''
    form.new_password = ''
    form.confirm_password = ''
    message.success('个人信息已更新')
  } catch (error) {
    if (!error.goyouAdminAuthExpired) message.error(error.response?.data?.detail || '个人信息保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-title">
      <div>
        <h1>个人中心</h1>
        <p>修改管理员姓名、登录邮箱和密码。</p>
      </div>
    </div>
    <a-card title="登录信息" class="settings-card" :loading="loading">
      <a-form layout="vertical">
        <a-form-item label="管理员姓名" required>
          <a-input v-model:value="form.name" maxlength="80" placeholder="请输入管理员姓名" />
        </a-form-item>
        <a-form-item label="登录邮箱">
          <a-input v-model:value="form.email" type="email" autocomplete="username" />
        </a-form-item>
        <a-form-item label="当前密码" required>
          <a-input-password v-model:value="form.current_password" autocomplete="current-password" placeholder="验证身份后才能保存" />
        </a-form-item>
        <a-form-item label="新密码">
          <a-input-password v-model:value="form.new_password" autocomplete="new-password" placeholder="留空表示不修改密码" />
        </a-form-item>
        <a-form-item v-if="form.new_password" label="确认新密码" required>
          <a-input-password v-model:value="form.confirm_password" autocomplete="new-password" />
        </a-form-item>
        <a-form-item>
          <a-button type="primary" :loading="saving" @click="save">保存修改</a-button>
        </a-form-item>
      </a-form>
    </a-card>
  </div>
</template>
