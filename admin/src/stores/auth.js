import { defineStore } from 'pinia'
import client from '@/api/client'

const TOKEN_KEY = 'goyou_admin_token'
const LEGACY_TOKEN_KEY = 'proxyswitch_admin_token'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null,
    token: localStorage.getItem(TOKEN_KEY) || localStorage.getItem(LEGACY_TOKEN_KEY) || '',
  }),
  actions: {
    async login(email, password) {
      const { data } = await client.post('/v1/admin/auth/login', { email, password })
      this.token = data.access_token
      this.user = data.user
      localStorage.setItem(TOKEN_KEY, data.access_token)
      localStorage.removeItem(LEGACY_TOKEN_KEY)
    },
    async restore() {
      if (!this.token) return false
      try {
        this.user = (await client.get('/v1/admin/auth/me')).data.user
        return true
      } catch {
        this.logout()
        return false
      }
    },
    logout() {
      this.user = null
      this.token = ''
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(LEGACY_TOKEN_KEY)
    },
  },
})
