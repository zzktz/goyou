import axios from 'axios'

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || window.location.origin,
  timeout: 15000,
})

client.interceptors.request.use((config) => {
  const token = localStorage.getItem('goyou_admin_token') || localStorage.getItem('proxyswitch_admin_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

export default client
