<template>
  <div class="ai-agent">
    <!-- 顶部说明 -->
    <header class="ai-header">
      <div class="ai-title">
        <el-icon class="ai-title-icon"><MagicStick /></el-icon>
        <span class="ai-title-text">AI 数据分析代理</span>
        <el-tag type="warning" size="small" effect="light">内测预览</el-tag>
      </div>
      <div class="ai-subtitle">
        用自然语言描述需求，自动选择统计口径、生成图表与结论
        <span class="ai-scope">· 当前库：{{ dbLabel || dbKey }}</span>
      </div>
    </header>

    <div class="ai-body">
      <!-- 对话区 -->
      <section class="chat-panel">
        <div class="chat-scroll" ref="scrollRef">
          <div
            v-for="(m, i) in messages"
            :key="i"
            class="msg-row"
            :class="m.role"
          >
            <div class="avatar">
              <el-icon v-if="m.role === 'ai'"><MagicStick /></el-icon>
              <el-icon v-else><User /></el-icon>
            </div>
            <div class="bubble">
              <div class="bubble-text">{{ m.text }}</div>
            </div>
          </div>
        </div>

        <!-- 输入区（暂未开放，仅展示） -->
        <div class="chat-input">
          <el-input
            v-model="draft"
            type="textarea"
            :rows="2"
            resize="none"
            maxlength="500"
            placeholder="例如：对比广州和佛山 2024 年每月的实销盒数，并指出下滑的品类"
            @keyup.enter.exact.prevent="handleSend"
          />
          <div class="input-actions">
            <span class="hint">Enter 发送 / Shift + Enter 换行</span>
            <el-button type="primary" :icon="Promotion" @click="handleSend">
              发送
            </el-button>
          </div>
        </div>
      </section>

      <!-- 右侧：能力预览 + 推荐问题 -->
      <aside class="side-panel">
        <div class="side-block">
          <div class="side-title">能力预览</div>
          <ul class="ability-list">
            <li v-for="a in abilities" :key="a.name">
              <el-icon class="ability-icon"><component :is="a.icon" /></el-icon>
              <div class="ability-text">
                <div class="ability-name">{{ a.name }}</div>
                <div class="ability-desc">{{ a.desc }}</div>
              </div>
            </li>
          </ul>
        </div>

        <div class="side-block">
          <div class="side-title">推荐提问</div>
          <div class="chip-wrap">
            <span
              v-for="q in suggestions"
              :key="q"
              class="chip"
              @click="draft = q"
            >
              {{ q }}
            </span>
          </div>
        </div>

        <div class="dev-tip">
          <el-icon><InfoFilled /></el-icon>
          <span>功能开发中，当前仅为界面预览，发送不会真正执行查询。</span>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  MagicStick,
  Promotion,
  User,
  InfoFilled,
  ChatDotRound,
  DataLine,
  TrendCharts,
  Opportunity,
} from '@element-plus/icons-vue'
import { getDbConfig } from '@/utils/dbConfig'

const props = defineProps<{
  dbKey: string
}>()

interface ChatMsg {
  role: 'ai' | 'user'
  text: string
}

const messages = ref<ChatMsg[]>([
  {
    role: 'ai',
    text: '你好，我是 AI 数据分析代理。你可以直接用中文提问，例如「广州上月销量前 10 的门店」「各品类月度实销盒数趋势」，我会自动选择统计维度并给出结论。',
  },
  {
    role: 'user',
    text: '广州 2024 年各品类的实销盒数趋势？',
  },
  {
    role: 'ai',
    text: '（示例输出）已按「城市=广州、时间=2024 年」聚合，共命中 12 个品类、实销盒数合计 1,284,530。Top3 品类为……整体呈 Q1 高、Q3 回落趋势。',
  },
])

const draft = ref('')

const abilities = [
  {
    name: '自然语言问数',
    desc: '自动识别时间 / 地域 / 品类条件',
    icon: ChatDotRound,
  },
  {
    name: '自动选维度',
    desc: '在门店数、盒数、趋势间自动切换',
    icon: DataLine,
  },
  {
    name: '异常归因',
    desc: '定位下滑门店与品类并给出原因',
    icon: TrendCharts,
  },
  {
    name: '结论与报告',
    desc: '输出可复制的文字结论与图表',
    icon: Opportunity,
  },
]

const suggestions = [
  '广州上月销量前 10 的门店',
  '各品类月度实销盒数趋势',
  '同比下滑最多的城市',
  '动销门店数低于均值的品类',
]

const dbLabel = ref('')

async function loadDbLabel() {
  const cfg = await getDbConfig(props.dbKey)
  dbLabel.value = cfg?.label ?? ''
}

onMounted(loadDbLabel)
watch(() => props.dbKey, loadDbLabel)

/** 暂未接入后端：仅提示，不发起任何请求 */
function handleSend() {
  const text = draft.value.trim()
  if (!text) {
    ElMessage.warning('请先输入你想问的问题')
    return
  }
  ElMessage.info('AI 代理功能开发中，敬请期待')
}
</script>

<style scoped>
.ai-agent {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  gap: 12px;
}

/* ---------- 顶部 ---------- */
.ai-header {
  flex-shrink: 0;
  padding: 14px 18px;
  background: linear-gradient(135deg, #1a4fc0 0%, #3b6bd6 100%);
  border-radius: 10px;
  color: #fff;
}

.ai-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ai-title-icon {
  font-size: 18px;
}

.ai-title-text {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.3px;
}

.ai-subtitle {
  margin-top: 6px;
  font-size: 12px;
  opacity: 0.85;
}

.ai-scope {
  margin-left: 4px;
  opacity: 0.9;
}

/* ---------- 主体 ---------- */
.ai-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 14px;
}

/* ---------- 对话区 ---------- */
.chat-panel {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  background: #fff;
  overflow: hidden;
}

.chat-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px;
  background: #fafbfc;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.msg-row {
  display: flex;
  gap: 10px;
  max-width: 78%;
}

.msg-row.user {
  align-self: flex-end;
  flex-direction: row-reverse;
}

.avatar {
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  color: #fff;
  background: #3b6bd6;
}

.msg-row.user .avatar {
  background: #67c23a;
}

.bubble {
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.7;
  color: #303133;
  background: #fff;
  border: 1px solid #ebeef5;
  word-break: break-word;
}

.msg-row.user .bubble {
  background: #ecf5ff;
  border-color: #d9ecff;
}

.chat-input {
  flex-shrink: 0;
  border-top: 1px solid #ebeef5;
  padding: 12px;
  background: #fff;
}

.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}

.hint {
  font-size: 11px;
  color: #c0c4cc;
}

/* ---------- 右侧栏 ---------- */
.side-panel {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}

.side-block {
  border: 1px solid #ebeef5;
  border-radius: 10px;
  background: #fff;
  padding: 14px;
}

.side-title {
  font-size: 12px;
  font-weight: 600;
  color: #909399;
  letter-spacing: 0.5px;
  margin-bottom: 10px;
}

.ability-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.ability-list li {
  display: flex;
  gap: 10px;
  align-items: flex-start;
}

.ability-icon {
  font-size: 16px;
  color: #3b6bd6;
  margin-top: 2px;
  flex-shrink: 0;
}

.ability-text {
  min-width: 0;
}

.ability-name {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.ability-desc {
  font-size: 11px;
  color: #a8abb2;
  line-height: 1.4;
}

.chip-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.chip {
  font-size: 12px;
  padding: 5px 10px;
  border-radius: 14px;
  background: #f4f6fa;
  color: #5a6b8c;
  cursor: pointer;
  transition: all 0.2s;
}

.chip:hover {
  background: #e8f0ff;
  color: #3b6bd6;
}

.dev-tip {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: 11px;
  line-height: 1.5;
  color: #e6a23c;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 8px;
  padding: 10px;
}

.dev-tip .el-icon {
  margin-top: 1px;
  flex-shrink: 0;
}
</style>
