import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import BasicLayout from "@/layouts/BasicLayout.vue";
import LoginPage from "@/pages/LoginPage.vue";
import DashboardPage from "@/pages/DashboardPage.vue";
import UsersPage from "@/pages/UsersPage.vue";
import LeasesPage from "@/pages/LeasesPage.vue";
import RelaysPage from "@/pages/RelaysPage.vue";
import SettingsPage from "@/pages/SettingsPage.vue";
import ProfilePage from "@/pages/ProfilePage.vue";
import TrafficLogsPage from "@/pages/TrafficLogsPage.vue";
import ReleasesPage from "@/pages/ReleasesPage.vue";
import FeedbackPage from "@/pages/FeedbackPage.vue";

const router = createRouter({
  history: createWebHistory("/admin/"),
  routes: [
    { path: "/login", component: LoginPage, meta: { public: true } },
    {
      path: "/",
      component: BasicLayout,
      children: [
        { path: "", redirect: "/dashboard" },
        {
          path: "dashboard",
          component: DashboardPage,
          meta: { title: "运行总览" },
        },
        { path: "users", component: UsersPage, meta: { title: "用户管理" } },
        { path: "leases", component: LeasesPage, meta: { title: "代理租约" } },
        { path: "relays", component: RelaysPage, meta: { title: "Relay 管理" } },
        {
          path: "traffic-logs",
          component: TrafficLogsPage,
          meta: { title: "流量日志" },
        },
        {
          path: "releases",
          component: ReleasesPage,
          meta: { title: "版本发布" },
        },
        {
          path: "feedback",
          component: FeedbackPage,
          meta: { title: "问题反馈" },
        },
        {
          path: "settings",
          component: SettingsPage,
          meta: { title: "系统设置" },
        },
        {
          path: "profile",
          component: ProfilePage,
          meta: { title: "个人中心" },
        },
      ],
    },
  ],
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (to.meta.public) return auth.token ? "/dashboard" : true;
  if (await auth.restore()) return true;
  return "/login";
});

export default router;
