<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AppstoreOutlined, CloudServerOutlined, DownOutlined, MenuFoldOutlined, MenuUnfoldOutlined, SettingOutlined, TeamOutlined, LogoutOutlined, FileTextOutlined } from '@ant-design/icons-vue'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const collapsed = ref(false)
const settingsOpen = ref(false)
const active = computed(() => route.path.startsWith('/users') ? '/users' : route.path.startsWith('/leases') ? '/leases' : route.path.startsWith('/traffic-logs') ? '/traffic-logs' : route.path)
const title = computed(() => route.meta.title || '运行总览')
const initials = computed(() => (auth.user?.name || '管').slice(0, 1))

function go(path) { router.push(path) }
function logout() { auth.logout(); router.replace('/login') }
</script>

<template>
  <a-layout class="shell">
    <a-layout-sider v-model:collapsed="collapsed" class="sider" :width="224" :collapsed-width="64" collapsible :trigger="null">
      <div class="brand" :class="{ compact: collapsed }">
        <CloudServerOutlined />
        <span>GoYou 管理</span>
      </div>
      <a-menu theme="dark" mode="inline" :selected-keys="[active]" @click="({ key }) => go(key)">
        <a-menu-item key="/dashboard"><AppstoreOutlined /><span>运行总览</span></a-menu-item>
        <a-menu-item key="/users"><TeamOutlined /><span>用户管理</span></a-menu-item>
        <a-menu-item key="/leases"><CloudServerOutlined /><span>代理租约</span></a-menu-item>
        <a-menu-item key="/traffic-logs"><FileTextOutlined /><span>流量日志</span></a-menu-item>
        <a-menu-divider />
        <a-menu-item key="/settings"><SettingOutlined /><span>系统设置</span></a-menu-item>
      </a-menu>
    </a-layout-sider>

    <a-layout class="main-layout" :class="{ 'is-collapsed': collapsed }">
      <a-layout-header class="header">
        <div class="header-left">
          <a-button class="header-icon" type="text" :aria-label="collapsed ? '展开侧边栏' : '收起侧边栏'" @click="collapsed = !collapsed">
            <MenuUnfoldOutlined v-if="collapsed" /><MenuFoldOutlined v-else />
          </a-button>
          <a-breadcrumb><a-breadcrumb-item>管理控制台</a-breadcrumb-item><a-breadcrumb-item>{{ title }}</a-breadcrumb-item></a-breadcrumb>
        </div>
        <div class="header-actions">
          <a-tooltip title="界面设置"><a-button class="header-icon" type="text" aria-label="界面设置" @click="settingsOpen = true"><SettingOutlined /></a-button></a-tooltip>
          <a-dropdown>
            <a class="user" @click.prevent><a-avatar size="small" class="user-avatar">{{ initials }}</a-avatar><span>{{ auth.user?.name || '管理员' }}</span><DownOutlined /></a>
            <template #overlay>
              <a-menu><a-menu-item @click="go('/settings')"><SettingOutlined />系统设置</a-menu-item><a-menu-divider /><a-menu-item @click="logout"><LogoutOutlined />退出登录</a-menu-item></a-menu>
            </template>
          </a-dropdown>
        </div>
      </a-layout-header>
      <div class="page-tabs"><a-tag class="page-tab" color="blue">{{ title }}</a-tag></div>
      <a-layout-content class="content"><router-view /></a-layout-content>
    </a-layout>
  </a-layout>

  <a-drawer v-model:open="settingsOpen" title="界面设置" placement="right" :width="300">
    <a-form layout="vertical"><a-form-item label="导航模式"><a-segmented :options="['侧边菜单']" value="侧边菜单" block /></a-form-item><a-form-item label="内容区域"><a-switch checked-children="铺满" un-checked-children="定宽" /></a-form-item></a-form>
  </a-drawer>
</template>
