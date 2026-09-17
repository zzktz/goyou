<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { message, Modal } from "ant-design-vue";
import client from "@/api/client";

const rows = ref([]);
const loading = ref(true);
const filters = reactive({ status: "open", keyword: "" });
const pagination = reactive({
  current: 1,
  pageSize: 20,
  total: 0,
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 条反馈`,
});
const detail = ref(null);
const reply = ref("");
const replying = ref(false);
const previews = reactive({});
let previewUrls = [];

function formatDate(value) {
  return value
    ? new Date(value).toLocaleString("zh-CN", { hour12: false })
    : "-";
}
function statusLabel(value) {
  return value === "replied" ? "已处理" : "待处理";
}
function statusColor(value) {
  return value === "replied" ? "green" : "orange";
}
function clearPreviews() {
  previewUrls.forEach(URL.revokeObjectURL);
  previewUrls = [];
  Object.keys(previews).forEach((key) => delete previews[key]);
}
async function load(page = pagination.current, pageSize = pagination.pageSize) {
  loading.value = true;
  try {
    const { data } = await client.get("/v1/admin/feedback", {
      params: {
        page,
        page_size: pageSize,
        status: filters.status,
        keyword: filters.keyword || undefined,
      },
    });
    rows.value = data.items || [];
    pagination.current = data.pagination.page;
    pagination.pageSize = data.pagination.page_size;
    pagination.total = data.pagination.total;
  } catch (error) {
    if (!error.goyouAdminAuthExpired)
      message.error(error.response?.data?.detail || "问题反馈加载失败");
  } finally {
    loading.value = false;
  }
}
function search() {
  load(1);
}
function changeStatus(status) {
  filters.status = status;
  load(1);
}
function reset() {
  filters.status = "open";
  filters.keyword = "";
  load(1);
}
function changePage(pager) {
  load(pager.current, pager.pageSize);
}
async function loadPreview(record, attachment) {
  try {
    const { data } = await client.get(
      `/v1/admin/feedback/${record.id}/attachments/${attachment.id}`,
      { responseType: "blob" },
    );
    const url = URL.createObjectURL(data);
    previews[attachment.id] = url;
    previewUrls.push(url);
  } catch (error) {
    if (!error.goyouAdminAuthExpired)
      message.error(`无法加载截图：${attachment.filename}`);
  }
}
function openDetail(record) {
  clearPreviews();
  detail.value = record;
  reply.value = record.reply || "";
  record.attachments?.forEach((attachment) => {
    void loadPreview(record, attachment);
  });
}
async function submitReply() {
  if (!detail.value || !reply.value.trim())
    return message.warning("请填写回复内容");
  replying.value = true;
  try {
    const { data } = await client.post(
      `/v1/admin/feedback/${detail.value.id}/reply`,
      { reply: reply.value.trim() },
    );
    detail.value = data;
    reply.value = data.reply || "";
    const index = rows.value.findIndex((item) => item.id === data.id);
    if (index >= 0) rows.value[index] = data;
    message.success("回复已发送");
  } catch (error) {
    if (!error.goyouAdminAuthExpired)
      message.error(error.response?.data?.detail || "回复发送失败");
  } finally {
    replying.value = false;
  }
}
function removeFeedback(record) {
  Modal.confirm({
    title: "删除这条反馈？",
    content: "删除后将同时移除反馈内容和截图，且无法恢复。",
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    async onOk() {
      try {
        await client.delete(`/v1/admin/feedback/${record.id}`);
        if (detail.value?.id === record.id) closeDetail();
        message.success("反馈已删除");
        const page = rows.value.length === 1 && pagination.current > 1
          ? pagination.current - 1
          : pagination.current;
        await load(page);
      } catch (error) {
        if (!error.goyouAdminAuthExpired)
          message.error(error.response?.data?.detail || "反馈删除失败");
        throw error;
      }
    },
  });
}
function closeDetail() {
  detail.value = null;
  clearPreviews();
}
onMounted(load);
onBeforeUnmount(clearPreviews);
</script>

<template>
  <div>
    <div class="page-title">
      <div>
        <h1>问题反馈</h1>
        <p>查看用户问题、截图并直接回复。</p>
      </div>
      <a-button @click="load()">刷新</a-button>
    </div>
    <a-card class="filter-card">
      <a-tabs :active-key="filters.status" :animated="false" @change="changeStatus">
        <a-tab-pane key="open" tab="待处理" />
        <a-tab-pane key="replied" tab="已处理" />
      </a-tabs>
      <a-space wrap>
        <a-input
          v-model:value="filters.keyword"
          allow-clear
          placeholder="搜索用户或反馈内容"
          style="width: 300px"
          @press-enter="search"
        /><a-button type="primary" @click="search">查询</a-button
        ><a-button @click="reset">重置</a-button></a-space>
    </a-card>
    <a-card
      ><a-table
        :data-source="rows"
        :loading="loading"
        :pagination="pagination"
        row-key="id"
        @change="changePage"
        ><a-table-column title="提交时间" key="created_at" :width="180"
          ><template #default="{ record }">{{
            formatDate(record.created_at)
          }}</template></a-table-column
        ><a-table-column title="用户" key="user" :width="220"
          ><template #default="{ record }"
            ><div class="user-cell">
              <a-avatar size="small">{{
                record.user?.name?.slice(0, 1)
              }}</a-avatar>
              <div>
                <strong>{{ record.user?.name }}</strong
                ><span>{{ record.user?.email }}</span>
              </div>
            </div></template
          ></a-table-column
        ><a-table-column
          title="反馈内容"
          data-index="message"
          ellipsis
        /><a-table-column title="截图" key="attachments" :width="90"
          ><template #default="{ record }"
            >{{ record.attachments?.length || 0 }} 张</template
          ></a-table-column
        ><a-table-column title="状态" key="status" :width="100"
          ><template #default="{ record }"
            ><a-tag :color="statusColor(record.status)">{{
              statusLabel(record.status)
            }}</a-tag></template
          ></a-table-column
        ><a-table-column title="操作" key="action" :width="180"
          ><template #default="{ record }"
            ><a-space><a-button type="link" @click="openDetail(record)">{{
              record.status === "replied" ? "查看详情" : "处理"
            }}</a-button
            ><a-button type="link" danger @click="removeFeedback(record)">删除</a-button></a-space></template
          ></a-table-column
        ></a-table
      ></a-card
    >
    <a-modal
      :open="!!detail"
      :title="
        detail
          ? `反馈 · ${detail.user?.name || detail.user?.email}`
          : '问题反馈'
      "
      width="720px"
      :footer="null"
      @cancel="closeDetail"
    >
      <template v-if="detail"
        ><a-descriptions size="small" :column="2" bordered
          ><a-descriptions-item label="提交用户"
            >{{ detail.user?.name }}（{{
              detail.user?.email
            }}）</a-descriptions-item
          ><a-descriptions-item label="提交时间">{{
            formatDate(detail.created_at)
          }}</a-descriptions-item
          ><a-descriptions-item label="状态"
            ><a-tag :color="statusColor(detail.status)">{{
              statusLabel(detail.status)
            }}</a-tag></a-descriptions-item
          ><a-descriptions-item label="截图"
            >{{ detail.attachments?.length || 0 }} 张</a-descriptions-item
          ></a-descriptions
        >
        <div class="feedback-detail-block">
          <strong>用户反馈</strong>
          <p>{{ detail.message }}</p>
        </div>
        <div v-if="detail.attachments?.length" class="feedback-images">
          <a-image
            v-for="attachment in detail.attachments"
            :key="attachment.id"
            :src="previews[attachment.id]"
            :alt="attachment.filename"
            :width="150"
            :height="100"
            class="feedback-image"
          /><span
            v-if="Object.keys(previews).length < detail.attachments.length"
            class="muted"
            >正在加载截图…</span
          >
        </div>
        <div class="feedback-detail-block">
          <strong>回复用户</strong
          ><a-textarea
            v-model:value="reply"
            :rows="4"
            :maxlength="5000"
            placeholder="输入处理结果、使用建议或需要用户补充的信息"
          />
          <div class="feedback-reply-actions">
            <a-button @click="closeDetail">关闭</a-button
            ><a-button
              type="primary"
              :loading="replying"
              @click="submitReply"
              >{{
                detail.status === "replied" ? "更新回复" : "发送回复"
              }}</a-button
            >
          </div>
        </div></template
      >
    </a-modal>
  </div>
</template>
