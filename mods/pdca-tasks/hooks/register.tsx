import { atom, read, update } from 'claude-code'
import type { EngineInterface, PluginOptions, Register } from 'claude-code'

import type { Snapshot, TaskItem, View } from '../types'

const PANE = 'pdca-tasks'
const EMPTY: View = { snapshot: null, error: '', isLoading: false }
const view = atom({ plugin: 'pdca-tasks', key: 'view' } as const, EMPTY)

const DAY_MS = 24 * 60 * 60 * 1000
const BANGKOK_OFFSET_MS = 7 * 60 * 60 * 1000
const MIN_REFRESH_SECONDS = 15
const TEAM_ROLES = ['dept_head', 'director']
const STATUS_LABEL: Record<string, string> = {
  blocked: 'Bị chặn',
  in_progress: 'Đang làm',
  todo: 'Chưa làm',
}
const BAR_CELLS = 8

type Json = Record<string, any>
type Config = {
  url: string
  token: string
  seconds: number
  maxProjects: number
  autoOpen: boolean
}
type Due = { text: string; tone: 'bad' | 'warn' | 'normal' }

/** Gọi một tool của PDCA MCP Server (JSON-RPC qua HTTP, token Bearer). */
async function callTool(
  $: EngineInterface,
  url: string,
  token: string,
  tool: string,
  args: Json,
): Promise<Json> {
  const res = await $.http.fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      Accept: 'application/json, text/event-stream',
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'tools/call',
      params: { name: tool, arguments: args },
    }),
  })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  const message = JSON.parse(res.text) as Json
  const result = message.result as Json | undefined
  if (!result) {
    throw new Error(String(message.error?.message ?? 'Không có kết quả'))
  }
  const text = String(result.content?.[0]?.text ?? '')
  if (result.isError) {
    throw new Error(text.replace(/^Error executing tool [^:]+: /, '') || 'Lỗi tool')
  }
  return (result.structuredContent as Json | undefined) ?? (JSON.parse(text) as Json)
}

function todayBangkok(nowMs: number): string {
  return new Date(nowMs + BANGKOK_OFFSET_MS).toISOString().slice(0, 10)
}

function timeBangkok(nowMs: number): string {
  return new Date(nowMs + BANGKOK_OFFSET_MS).toISOString().slice(11, 16)
}

function daysUntil(due: string, today: string): number {
  return Math.round((Date.parse(due) - Date.parse(today)) / DAY_MS)
}

function dueInfo(task: TaskItem, today: string): Due {
  if (task.dueDate === null) {
    return { text: 'không hạn', tone: 'normal' }
  }
  const days = daysUntil(task.dueDate, today)
  if (days < 0) {
    return { text: `quá hạn ${-days} ngày`, tone: 'bad' }
  }
  if (days === 0) {
    return { text: 'hạn hôm nay', tone: 'warn' }
  }
  return { text: `hạn ${task.dueDate.slice(8, 10)}/${task.dueDate.slice(5, 7)}`, tone: 'normal' }
}

function isOverdue(task: TaskItem, today: string): boolean {
  return task.dueDate !== null && task.dueDate < today
}

/** Việc cần chú ý: quá hạn, đến hạn hôm nay hoặc bị chặn. */
function needsAttention(task: TaskItem, today: string): boolean {
  return task.status === 'blocked' || (task.dueDate !== null && task.dueDate <= today)
}

function byDueDate(a: TaskItem, b: TaskItem): number {
  return (a.dueDate ?? '9999-12-31').localeCompare(b.dueDate ?? '9999-12-31') || a.id - b.id
}

/** Thanh tiến độ bằng ký tự: tỷ lệ việc đã xong trên tổng (đã xong + đang mở). */
function progressBar(done: number, open: number): string {
  const total = done + open
  const filled = total === 0 ? 0 : Math.round((done / total) * BAR_CELLS)
  return '█'.repeat(filled) + '░'.repeat(BAR_CELLS - filled)
}

function toItem(raw: Json, assigneeId: number, assignee: string): TaskItem {
  return {
    id: Number(raw.id),
    title: String(raw.title),
    status: String(raw.status),
    dueDate: raw.due_date ? String(raw.due_date) : null,
    projectId: Number(raw.project_id ?? 0),
    assigneeId: Number(raw.assignee_id ?? assigneeId),
    assignee: String(raw.assignee_name ?? assignee),
  }
}

async function fetchSnapshot(
  $: EngineInterface,
  url: string,
  token: string,
  maxProjects: number,
): Promise<Snapshot> {
  const me = await callTool($, url, token, 'whoami', {})
  const role = String(me.role)
  const userId = Number(me.user_id)
  const userName = String(me.name)
  const isTeam = TEAM_ROLES.includes(role)

  const tasks = new Map<number, TaskItem>()
  const done = new Map<number, number>()

  if (role !== 'admin') {
    const mine = await callTool($, url, token, 'get_my_tasks', { limit: 100 })
    for (const raw of mine.items as Json[]) {
      tasks.set(Number(raw.id), toItem(raw, userId, userName))
    }
    if (isTeam) {
      const mineDone = await callTool($, url, token, 'get_my_tasks', { status: 'done', limit: 100 })
      for (const raw of mineDone.items as Json[]) {
        done.set(Number(raw.id), userId)
      }
    }
  }

  if (isTeam) {
    const projects = (me.projects as Json[]).slice(0, Math.max(0, maxProjects))
    for (const project of projects) {
      const projectId = Number(project.id)
      try {
        const open = await callTool($, url, token, 'list_project_tasks', {
          project_id: projectId,
          limit: 100,
        })
        for (const raw of open.items as Json[]) {
          tasks.set(Number(raw.id), toItem(raw, userId, userName))
        }
        const finished = await callTool($, url, token, 'list_project_tasks', {
          project_id: projectId,
          status: 'done',
          limit: 100,
        })
        for (const raw of finished.items as Json[]) {
          done.set(Number(raw.id), Number(raw.assignee_id))
        }
      } catch {
        // Project ngoài phạm vi quyền thì bỏ qua, không làm hỏng cả bảng.
      }
    }
  }

  const doneByUser: Record<string, number> = {}
  for (const assigneeId of done.values()) {
    doneByUser[String(assigneeId)] = (doneByUser[String(assigneeId)] ?? 0) + 1
  }
  return {
    role,
    userId,
    userName,
    tasks: [...tasks.values()],
    doneByUser,
    fetchedAt: await $.clock.now(),
  }
}

function summary(snapshot: Snapshot, today: string): string {
  const overdue = snapshot.tasks.filter(t => isOverdue(t, today)).length
  const blocked = snapshot.tasks.filter(t => t.status === 'blocked').length
  const scope = TEAM_ROLES.includes(snapshot.role) ? 'nhóm' : 'việc'
  const parts = [`PDCA: ${snapshot.tasks.length} ${scope} mở`]
  if (overdue > 0) parts.push(`${overdue} quá hạn`)
  if (blocked > 0) parts.push(`${blocked} bị chặn`)
  return parts.join(' · ')
}

/** Cấu hình từ `userConfig`; thiếu token/URL thì lấy từ biến môi trường PDCA_TOKEN, PDCA_SERVER_URL. */
async function loadConfig($: EngineInterface, options: PluginOptions): Promise<Config> {
  const envToken = await $.env.get('PDCA_TOKEN')
  const envUrl = await $.env.get('PDCA_SERVER_URL')
  const envAutoOpen = await $.env.get('PDCA_AUTO_OPEN')
  return {
    url: String(options.server_url || envUrl || 'http://localhost:8010/mcp'),
    token: String(options.api_token || envToken || ''),
    seconds: Math.max(MIN_REFRESH_SECONDS, Number(options.refresh_seconds ?? 60)),
    maxProjects: Number(options.max_projects ?? 3),
    autoOpen: options.auto_open !== false && envAutoOpen !== '0',
  }
}

/** Làm mới dữ liệu; lỗi được ghi vào view, không ném ra ngoài. */
async function refresh($: EngineInterface, config: Config): Promise<void> {
  await update($, view, v => ({ ...v, isLoading: true }))
  try {
    const snapshot = await fetchSnapshot($, config.url, config.token, config.maxProjects)
    await update($, view, () => ({ snapshot, error: '', isLoading: false }))
    $.ui.status(summary(snapshot, todayBangkok(snapshot.fetchedAt)))
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    await update($, view, v => ({ ...v, error: message, isLoading: false }))
    $.ui.status('PDCA: lỗi kết nối')
  }
}

export const register: Register = (on, options) => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'pdca-tasks',
      description: 'Mở pane theo dõi task PDCA (nhân viên: việc của tôi; trưởng phòng: theo từng người)',
    })
    const config = await loadConfig($, options)
    if (config.token === '') {
      $.ui.toast('Chưa có API token PDCA (biến môi trường PDCA_TOKEN hoặc cấu hình plugin)')
    } else {
      void refresh($, config)
      $.clock.every(config.seconds * 1000, () => void refresh($, config))
      if (config.autoOpen) {
        // Mở không do người dùng yêu cầu: chỉ vào chỗ khi cửa sổ đủ rộng, hẹp hơn thì chờ.
        void $.ui.open({ id: PANE, title: 'Task PDCA' })
      }
    }

    return next(e)
  })

  on('command.run', { command: 'pdca-tasks' }, async $ => {
    const config = await loadConfig($, options)
    await $.ui.open({ id: PANE, title: 'Task PDCA' })
    if (config.token !== '') {
      void refresh($, config)
    }

    return {
      text:
        config.token !== ''
          ? 'Đã mở pane task PDCA.'
          : 'Chưa có API token PDCA (biến môi trường PDCA_TOKEN hoặc cấu hình plugin).',
    }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const config = await loadConfig($, options)
    const state = await read($, view)
    const snapshot = state.snapshot
    const refreshButton = (
      <Button key="refresh" label="Làm mới" hotkey="r" onPress={() => refresh($, config)} />
    )

    if (snapshot === null) {
      return (
        <Box flexDirection="column">
          <Text dimColor>{state.error === '' ? 'Đang tải task...' : `Lỗi: ${state.error}`}</Text>
          {refreshButton}
        </Box>
      )
    }

    const today = todayBangkok(snapshot.fetchedAt)
    const isTeam = TEAM_ROLES.includes(snapshot.role)
    let budget = Math.max(6, (e.viewport?.rows ?? 24) - 8)

    const row = (task: TaskItem, showStatus: boolean) => {
      const due = dueInfo(task, today)
      const color =
        due.tone === 'bad' || task.status === 'blocked'
          ? 'red'
          : due.tone === 'warn'
            ? 'yellow'
            : undefined
      const status = showStatus ? ` [${STATUS_LABEL[task.status] ?? task.status}]` : ''
      return (
        <Text color={color}>
          #{task.id}
          {status} {task.title} — {due.text}
        </Text>
      )
    }

    /** Vẽ tối đa `budget` dòng còn lại; phần thừa gom vào "và N việc nữa". */
    const rows = (items: TaskItem[], showStatus: boolean) => {
      const shown = items.slice(0, Math.max(0, budget))
      budget -= shown.length
      const hidden = items.length - shown.length
      return (
        <Box flexDirection="column">
          {shown.map(task => row(task, showStatus))}
          {hidden > 0 && <Text dimColor>…và {hidden} việc nữa</Text>}
        </Box>
      )
    }

    const footer = (
      <Box flexDirection="column">
        <Text dimColor>
          {state.error === '' ? '' : `Lỗi lần làm mới gần nhất: ${state.error}. `}
          {state.isLoading
            ? 'Đang làm mới...'
            : `Cập nhật lúc ${timeBangkok(snapshot.fetchedAt)} (${snapshot.userName})`}
        </Text>
        {refreshButton}
      </Box>
    )

    if (!isTeam) {
      // Mẫu A (nhân viên): chip số liệu, rồi Cần chú ý / Đang làm / Chưa làm.
      const open = [...snapshot.tasks].sort(byDueDate)
      const attention = open.filter(t => needsAttention(t, today))
      const rest = open.filter(t => !needsAttention(t, today))
      const doing = rest.filter(t => t.status === 'in_progress')
      const todo = rest.filter(t => t.status !== 'in_progress')
      const overdue = open.filter(t => isOverdue(t, today)).length
      const blocked = open.filter(t => t.status === 'blocked').length
      const dueToday = open.filter(t => t.dueDate === today).length

      return (
        <Box flexDirection="column">
          <Text bold>Task PDCA — {snapshot.userName}</Text>
          <Box>
            <Text dimColor>{open.length} đang mở  </Text>
            {overdue > 0 && <Text color="red">{overdue} quá hạn  </Text>}
            {blocked > 0 && <Text color="red">{blocked} bị chặn  </Text>}
            {dueToday > 0 && <Text color="yellow">{dueToday} hạn hôm nay</Text>}
          </Box>
          {open.length === 0 && <Text dimColor>Không có việc đang mở.</Text>}
          {attention.length > 0 && <Text bold>Cần chú ý ({attention.length})</Text>}
          {rows(attention, true)}
          {doing.length > 0 && <Text bold>Đang làm ({doing.length})</Text>}
          {rows(doing, false)}
          {todo.length > 0 && <Text bold>Chưa làm ({todo.length})</Text>}
          {rows(todo, false)}
          {footer}
        </Box>
      )
    }

    // Mẫu B (trưởng phòng, giám đốc): mỗi người một khối kèm thanh tỷ lệ việc đã xong.
    const groups = new Map<number, { name: string; items: TaskItem[] }>()
    for (const task of snapshot.tasks) {
      const group = groups.get(task.assigneeId) ?? {
        name: task.assigneeId === snapshot.userId ? 'Tôi' : task.assignee,
        items: [],
      }
      group.items.push(task)
      groups.set(task.assigneeId, group)
    }
    const ordered = [...groups.entries()]
      .map(([assigneeId, group]) => ({
        assigneeId,
        name: group.name,
        items: [...group.items].sort(byDueDate),
        isMe: assigneeId === snapshot.userId,
      }))
      .sort((a, b) => {
        if (a.isMe !== b.isMe) return a.isMe ? 1 : -1
        const aHot = a.items.some(t => needsAttention(t, today)) ? 0 : 1
        const bHot = b.items.some(t => needsAttention(t, today)) ? 0 : 1
        return aHot - bHot || a.name.localeCompare(b.name)
      })
    const overdueAll = snapshot.tasks.filter(t => isOverdue(t, today)).length
    const blockedAll = snapshot.tasks.filter(t => t.status === 'blocked').length

    return (
      <Box flexDirection="column">
        <Text bold>Task PDCA — nhóm</Text>
        <Box>
          <Text dimColor>{snapshot.tasks.length} đang mở  </Text>
          {overdueAll > 0 && <Text color="red">{overdueAll} quá hạn  </Text>}
          {blockedAll > 0 && <Text color="red">{blockedAll} bị chặn</Text>}
        </Box>
        {ordered.length === 0 && <Text dimColor>Không có việc đang mở.</Text>}
        {ordered.map(group => {
          const doneCount = snapshot.doneByUser[String(group.assigneeId)] ?? 0
          const late = group.items.filter(t => isOverdue(t, today)).length
          return (
            <Box flexDirection="column">
              <Text bold>
                {group.name} · {group.items.length} việc
                {late > 0 ? ` · ${late} quá hạn` : ''}
              </Text>
              <Text dimColor>
                {progressBar(doneCount, group.items.length)} xong {doneCount}/
                {doneCount + group.items.length}
              </Text>
              {rows(group.items, true)}
            </Box>
          )
        })}
        {footer}
      </Box>
    )
  })
}
