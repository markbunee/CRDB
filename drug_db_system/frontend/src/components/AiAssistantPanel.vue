<template>
  <div class="ai-assistant">
    <!-- 主体：左案例 / 右对话（窄屏上下堆叠） -->
    <div class="ai-main">
      <!-- 左侧：案例互动区 -->
      <aside class="ai-cases">
        <div class="cases-title">
          <el-icon><MagicStick /></el-icon>
          <span>案例互动 · 点一下直接查</span>
        </div>

        <div class="db-pick">
          <span class="db-label">数据源</span>
          <el-select v-model="dbKey" size="small" style="flex: 1">
            <el-option v-for="d in DB_OPTIONS" :key="d.key" :label="d.label" :value="d.key" />
          </el-select>
        </div>

        <!-- 模型配置入口：显示当前使用中的那一套，点设置可管理多套并切换 -->
        <div class="model-bar">
          <span class="db-label">模型</span>
          <span class="model-name" :class="{ none: !activeModel }" :title="activeModel || ''">
            {{ activeModel || '未配置（仅规则问数）' }}
          </span>
          <el-button size="small" text :icon="Setting" @click="modelDialog = true">设置</el-button>
        </div>

        <div class="case-grid">
          <button
            v-for="c in cases"
            :key="c.id"
            class="case-card"
            type="button"
            :disabled="busy"
            @click="send(c.prompt)"
          >
            <div class="case-icon"><el-icon><component :is="c.icon" /></el-icon></div>
            <div class="case-text">
              <div class="case-name">{{ c.name }}</div>
              <div class="case-prompt">{{ c.prompt }}</div>
            </div>
          </button>
        </div>

        <div class="cases-tip">
          <el-icon><InfoFilled /></el-icon>
          <span>点击案例会真实查询数据源。条件不全时我会先把条件列出来请你确认，不会替你猜。</span>
        </div>
      </aside>

      <!-- 右侧：对话区 -->
      <section class="ai-chat">
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
              <div class="bubble-text" v-html="m.html"></div>

              <!-- 确认卡：条件不全时，一次列全让人补 -->
              <AiQueryCard
                v-if="m.card"
                :card="m.card"
                :busy="busy"
                @submit="onCardSubmit(m.card!, $event)"
                @cancel="onCardCancel(m.card!)"
              />

              <!-- 回答的口径说明 + 赞踩 -->
              <template v-if="m.answer">
                <ul v-if="m.caveats?.length" class="caveats">
                  <li v-for="(c, k) in m.caveats" :key="k">{{ c }}</li>
                </ul>
                <div class="feedback">
                  <el-button
                    size="small"
                    text
                    :type="m.vote === 1 ? 'success' : 'info'"
                    @click="vote(m, 1)"
                  >
                    有用
                  </el-button>
                  <el-button
                    size="small"
                    text
                    :type="m.vote === -1 ? 'danger' : 'info'"
                    @click="vote(m, -1)"
                  >
                    没用
                  </el-button>
                </div>
              </template>
            </div>
          </div>

          <div v-if="busy" class="msg-row ai">
            <div class="avatar"><el-icon><MagicStick /></el-icon></div>
            <div class="bubble">
              <div class="typing"><span></span><span></span><span></span></div>
            </div>
          </div>
        </div>

        <div class="chat-input">
          <el-input
            v-model="draft"
            type="textarea"
            :rows="2"
            resize="none"
            maxlength="200"
            :disabled="busy"
            placeholder="试着输入你的问题，例如：广东省 2026 年 1-9 月维生素D 的实销情况和同比"
            @keyup.enter.exact.prevent="send(draft)"
          />
          <div class="input-actions">
            <span class="hint">Enter 发送 · Shift + Enter 换行</span>
            <el-button type="primary" :icon="Promotion" :loading="busy" @click="send(draft)">
              发送
            </el-button>
          </div>
        </div>
      </section>
    </div>

    <!-- 模型配置弹窗（保存多套 + 切换 + 测试连接） -->
    <AiModelDialog v-model="modelDialog" @saved="loadActiveModel" />
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  MagicStick,
  Promotion,
  User,
  InfoFilled,
  Trophy,
  TrendCharts,
  DataAnalysis,
  Shop,
  Money,
  Goods,
  Setting,
} from '@element-plus/icons-vue'
import AiQueryCard from './AiQueryCard.vue'
import AiModelDialog from './AiModelDialog.vue'
import { askAi, confirmAi, abandonAi, feedbackAi, fetchAiModels } from '@/api/client'
import type { AiCard } from '@/api/client'

interface ChatMsg {
  role: 'ai' | 'user'
  html: string
  card?: AiCard
  answer?: boolean
  caveats?: string[]
  questionId?: number
  vote?: number
}

const DB_OPTIONS = [
  { key: 'dashenlin', label: '大参林' },
  { key: 'gaoji', label: '高济' },
  { key: 'haiwang', label: '海王' },
]

const dbKey = ref('dashenlin')

const messages = ref<ChatMsg[]>([
  {
    role: 'ai',
    html: '你好，我是 <b>AI 数据分析助手</b>。你可以直接说人话，例如「广东省 2026 年 1-9 月的实销情况」。' +
      '条件不全时，我会把要补的条件一次列出来请你确认 —— <b>不会替你猜</b>。' +
      '<br/>👇 左侧挑一个案例点一下，直接出真实数据。',
  },
])

const draft = ref('')
const busy = ref(false)
const scrollRef = ref<HTMLElement | null>(null)
const sessionId = ref<string | undefined>(undefined)

/** 模型配置 */
const modelDialog = ref(false)
const activeModel = ref('')

/** 显示当前启用的那套模型（没配就提示「仅规则问数」） */
async function loadActiveModel() {
  try {
    const r = await fetchAiModels()
    const act = (r.items || []).find((i) => i.is_active)
    activeModel.value = act ? `${act.model}` : ''
  } catch {
    activeModel.value = ''
  }
}

onMounted(loadActiveModel)

const cases = [
  { id: 'overview', name: '实销情况', prompt: '广东省 2026 年 1-9 月的实销情况和同比', icon: DataAnalysis },
  { id: 'boxes', name: '品类盒数', prompt: '广东的维生素D 的实销盒数', icon: Goods },
  { id: 'amount', name: '实销金额', prompt: '广东的实销金额情况', icon: Money },
  { id: 'store_rank', name: '加盟店排行', prompt: '广州市排行前 10 的加盟店卖得最好的品种', icon: Trophy },
  { id: 'trend', name: '月度趋势', prompt: '广东省各品类的月度实销盒数趋势', icon: TrendCharts },
  { id: 'chain', name: '连锁 vs 加盟', prompt: '广东省连锁和加盟的实销金额对比', icon: Shop },
]

/** 先转义，再把 **加粗** 转成 <b>，避免把用户原话当 HTML 渲染 */
function renderText(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/\n/g, '<br/>')
}

async function scrollToBottom() {
  await nextTick()
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function pushUser(text: string) {
  messages.value.push({ role: 'user', html: renderText(text) })
  await scrollToBottom()
}

async function pushAi(msg: Omit<ChatMsg, 'role'>) {
  messages.value.push({ role: 'ai', ...msg })
  await scrollToBottom()
}

/** 统一的响应落地：回答 or 确认卡 */
function land(resp: any) {
  sessionId.value = resp.session_id || sessionId.value
  if (resp.mode === 'card' && resp.card) {
    // 连「要查什么」都没识别出来时，卡片会是空的，别把空卡片甩给用户
    if (!resp.card.capability) {
      pushAi({
        html:
          '没识别出你要查什么。换个说法试试，例如「广东省 2026 年 1-9 月的实销情况」' +
          '「广州市排行前 10 的加盟店」。',
      })
      return
    }
    pushAi({
      html: '这个问题还差几个条件，我一次列全了，确认一下就能查：',
      card: resp.card,
    })
    return
  }
  if (resp.ok === false) {
    pushAi({ html: `查询失败：${renderText(resp.error || '未知错误')}` })
    return
  }
  pushAi({
    html: renderText(resp.answer || '查询完成'),
    answer: true,
    caveats: resp.caveats || [],
    questionId: resp.question_id,
  })
}

async function send(text: string) {
  const t = (text || '').trim()
  if (!t) {
    ElMessage.warning('请输入你想问的问题')
    return
  }
  if (busy.value) return
  draft.value = ''
  await pushUser(t)
  busy.value = true
  try {
    const resp = await askAi(dbKey.value, t, sessionId.value)
    land(resp)
  } catch (e: any) {
    pushAi({ html: `请求失败：${renderText(e?.message || '网络错误')}` })
  } finally {
    busy.value = false
  }
}

async function onCardSubmit(
  card: AiCard,
  payload: { answers: Record<string, any>; edited_from: string },
) {
  if (busy.value) return
  busy.value = true
  try {
    const resp = await confirmAi(dbKey.value, {
      session_id: card.session_id,
      question_id: card.question_id,
      answers: payload.answers,
      edited_from: payload.edited_from,
    })
    // 提交后：卡片保留在历史里，回答追加到后面
    land(resp)
  } catch (e: any) {
    pushAi({ html: `查询失败：${renderText(e?.message || '网络错误')}` })
  } finally {
    busy.value = false
  }
}

async function onCardCancel(card: AiCard) {
  // 放弃是重要的负面信号（问太多 / 问错了），必须让后端记下来
  try {
    await abandonAi(dbKey.value, { session_id: card.session_id, question_id: card.question_id })
  } catch {
    /* 放弃埋点失败不影响界面 */
  }
  pushAi({ html: '好的，已取消这次查询。换个说法再问我试试。' })
}

async function vote(m: ChatMsg, rating: number) {
  if (!m.questionId || m.vote) return
  m.vote = rating
  try {
    await feedbackAi(dbKey.value, { question_id: m.questionId, rating })
  } catch {
    /* 反馈失败不影响界面 */
  }
}
</script>

<style scoped>
.ai-assistant {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.ai-main {
  display: flex;
  gap: 16px;
  height: 460px;
}

/* ---------- 左侧案例 ---------- */
.ai-cases {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 0;
}
.cases-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.cases-title .el-icon {
  color: #7c4dff;
}
.db-pick {
  display: flex;
  align-items: center;
  gap: 8px;
}
.db-label {
  font-size: 12px;
  color: #909399;
  flex-shrink: 0;
}
.model-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.model-name {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: ui-monospace, Menlo, Consolas, monospace;
}
.model-name.none {
  color: #c0c4cc;
  font-family: inherit;
}
.case-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  overflow-y: auto;
}
.case-card {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  text-align: left;
  padding: 10px;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
}
.case-card:hover:not(:disabled) {
  border-color: #b39dff;
  box-shadow: 0 4px 14px rgba(124, 77, 255, 0.14);
  transform: translateY(-2px);
}
.case-card:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.case-icon {
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #7c4dff, #5b8def);
  color: #fff;
  font-size: 16px;
}
.case-text {
  min-width: 0;
}
.case-name {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}
.case-prompt {
  margin-top: 2px;
  font-size: 11px;
  color: #909399;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.cases-tip {
  margin-top: auto;
  display: flex;
  gap: 6px;
  font-size: 11px;
  line-height: 1.5;
  color: #e6a23c;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 8px;
  padding: 10px;
}
.cases-tip .el-icon {
  flex-shrink: 0;
  margin-top: 1px;
}

/* ---------- 右侧对话 ---------- */
.ai-chat {
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
  max-width: 92%;
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
  background: linear-gradient(135deg, #7c4dff, #5b8def);
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
  min-width: 0;
  max-width: 100%;
}
.msg-row.user .bubble {
  background: #ecf5ff;
  border-color: #d9ecff;
}
.bubble-text :deep(b) {
  color: #5b8def;
}
.caveats {
  margin: 10px 0 0;
  padding: 8px 10px 8px 24px;
  font-size: 11px;
  line-height: 1.6;
  color: #909399;
  background: #fafafa;
  border-radius: 6px;
}
.feedback {
  display: flex;
  gap: 4px;
  margin-top: 6px;
}
.typing {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}
.typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #c0c4cc;
  animation: tp 1s infinite ease-in-out;
}
.typing span:nth-child(2) {
  animation-delay: 0.15s;
}
.typing span:nth-child(3) {
  animation-delay: 0.3s;
}
@keyframes tp {
  0%,
  60%,
  100% {
    transform: translateY(0);
    opacity: 0.4;
  }
  30% {
    transform: translateY(-4px);
    opacity: 1;
  }
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

/* ---------- 窄屏：上下堆叠 ---------- */
@media (max-width: 860px) {
  .ai-main {
    flex-direction: column;
    height: auto;
  }
  .ai-cases {
    width: 100%;
  }
  .case-grid {
    grid-template-columns: 1fr 1fr;
    max-height: 260px;
  }
  .ai-chat {
    height: 420px;
  }
}
</style>
