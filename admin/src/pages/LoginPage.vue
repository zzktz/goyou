<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { CloudServerOutlined, LockOutlined, MailOutlined } from '@ant-design/icons-vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const email = ref('')
const password = ref('')
const loading = ref(false)

async function submit() {
  loading.value = true
  try {
    await auth.login(email.value, password.value)
    message.success('登录成功')
    router.replace('/dashboard')
  } catch (error) {
    message.error(error.response?.data?.detail || '登录失败，请检查管理员账号')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-panel">
      <div class="login-brand"><div class="logo"><CloudServerOutlined /></div><div><h1>GoYou</h1><p>中转代理管理控制台</p></div></div>
      <a-card :bordered="false" class="login-card">
        <h2>管理员登录</h2><p class="sub">管理用户、代理租约与中转服务状态</p>
        <form @submit.prevent="submit">
          <a-form-item label="管理员邮箱"><a-input v-model:value="email" size="large" placeholder="admin@example.com" autocomplete="username"><template #prefix><MailOutlined /></template></a-input></a-form-item>
          <a-form-item label="管理员密码"><a-input-password v-model:value="password" size="large" placeholder="请输入管理员密码" autocomplete="current-password"><template #prefix><LockOutlined /></template></a-input-password></a-form-item>
          <a-button type="primary" html-type="submit" size="large" block :loading="loading">登录控制台</a-button>
        </form>
      </a-card>
      <p class="copyright">GoYou Management · 管理服务器</p>
    </div>
  </div>
</template>
