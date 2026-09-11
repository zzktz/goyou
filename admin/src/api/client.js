import axios from 'axios'
import { message } from 'ant-design-vue'

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || window.location.origin,
  timeout: 15000,
})

let authExpiredRedirectTimer = null
let authExpiredNoticeShown = false

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('goyou_admin_token') || localStorage.getItem('proxyswitch_admin_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

client.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status
    const requestUrl = error.config?.url || ''
    const isAdminLoginRequest = requestUrl.includes('/v1/admin/auth/login')

    if (status === 401 && !isAdminLoginRequest && !authExpiredRedirectTimer) {
      localStorage.removeItem('goyou_admin_token')
      localStorage.removeItem('proxyswitch_admin_token')
      authExpiredNoticeShown = true
      message.error('管理员登录已过期，2 秒后跳转到登录页', 2)
      authExpiredRedirectTimer = window.setTimeout(() => {
        window.location.replace('/admin/login')
      }, 2000)
    }

    if (authExpiredNoticeShown && status === 401) {
      error.goyouAdminAuthExpired = true
    }
    return Promise.reject(error)
  },
)

export default client
