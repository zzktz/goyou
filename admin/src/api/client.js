import axios from 'axios'
import { message } from 'ant-design-vue'

const ERROR_FIELDS = new Set(['detail', 'error', 'download_error'])

function formatErrorMessage(value) {
  if (typeof value !== 'string') return value
  return value
    .replace(/error\s+sending\s+request\s+for\s+url\s*\([^)]*\)/giu, '网络请求失败')
    .replace(/\b(?:https?|wss?):\/\/[^\s"'<>]+/giu, '请求地址')
    .replace(
      /\b(?:[a-z\d](?:[a-z\d-]{0,61}[a-z\d])?\.)+[a-z]{2,63}(?::\d+)?(?:[/?#][^\s"'<>)]*)?/giu,
      '请求地址',
    )
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function sanitizeErrorFields(value) {
  if (!value || typeof value !== 'object') return
  if (Array.isArray(value)) {
    value.forEach(sanitizeErrorFields)
    return
  }
  Object.entries(value).forEach(([key, nestedValue]) => {
    if (ERROR_FIELDS.has(key) && typeof nestedValue === 'string') {
      value[key] = formatErrorMessage(nestedValue)
    } else if (nestedValue && typeof nestedValue === 'object') {
      sanitizeErrorFields(nestedValue)
    }
  })
}

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
  (response) => {
    sanitizeErrorFields(response.data)
    return response
  },
  (error) => {
    sanitizeErrorFields(error.response?.data)
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
