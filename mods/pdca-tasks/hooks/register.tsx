import { atom, read, update } from 'claude-code'
import type { EngineInterface, PluginOptions, Register } from 'claude-code'

import type { ProjectTasks, Snapshot, TaskItem, View } from '../types'

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
const STATUS_ORDER = ['blocked', 'in_progress', 'todo']

type Json = Record<string, any>

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

function daysUntil(due: string, today: string): number {
  return Math.round((Date.parse(due) - Date.parse(today)) / DAY_MS)
}

function toItem(raw: Json, projectId: number): TaskItem {
  return {
    id: Number(raw.id),
    title: String(raw.title),
    status: String(raw.status),
    dueDate: raw.due_date ? String(raw.due_date) : null,
    projectId: Number(raw.project_id ?? projectId),
    assignee: raw.assignee_name ? String(raw.assignee_name) : undefined,
  }
}

async function fetchSnapshot(
  $: EngineInterface,
  url: string,
  token: string,
  maxProjects: number,
): Promise<Snapshot> {
  const me = await callTool($, url, token, 'whoami', {})
  const mineData = await callTool($, url, token, 'get_my_tasks', { limit: 100 })
  const mine = (mineData.items as Json[]).map(raw => toItem(raw, 0))

  const team: ProjectTasks[] = []
  if (TEAM_ROLES.includes(String(me.role))) {
    const projects = (me.projects as Json[]).slice(0, Math.max(0, maxProjects))
    for (const project of projects) {
      const projectId = Number(project.id)
      try {
        const data = await callTool($, url, token, 'list_project_tasks', {
          project_id: projectId,
          limit: 100,
        })
        team.push({
          projectId,
          name: String(project.name),
          items: (data.items as Json[]).map(raw => toItem(raw, projectId)),
        })
      } catch {
        // Project ngoài phạm vi quyền thì bỏ qua, không làm hỏng cả bảng.
      }
    }
  }
  return {
    role: String(me.role),
    userName: String(me.name),
    mine,
    team,
    fetchedAt: await $.clock.now(),
  }
}

function summary(snapshot: Snapshot, today: string): string {
  const overdue = snapshot.mine.filter(t => t.dueDate !== null && t.dueDate < today).length
  const blocked = snapshot.mine.filter(t => t.status === 'blocked').length
  const parts = [`PDCA: ${snapshot.mine.length} việc mở`]
  if (overdue > 0) parts.push(`${overdue} quá hạn`)
  if (blocked > 0) parts.push(`${blocked} bị chặn`)
  return parts.join(' · ')
}

type Config = { url: string; token: string; seconds: number; maxProjects: number }

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

/** Cấu hình từ `userConfig`; thiếu token/URL thì lấy từ biến môi trường PDCA_TOKEN, PDCA_SERVER_URL. */
async function loadConfig($: EngineInterface, options: PluginOptions): Promise<Config> {
  const envToken = await $.env.get('PDCA_TOKEN')
  const envUrl = await $.env.get('PDCA_SERVER_URL')
  return {
    url: String(options.server_url || envUrl || 'http://localhost:8010/mcp'),
    token: String(options.api_token || envToken || ''),
    seconds: Math.max(MIN_REFRESH_SECONDS, Number(options.refresh_seconds ?? 60)),
    maxProjects: Number(options.max_projects ?? 3),
  }
}

export const register: Register = (on, options) => {

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'pdca-tasks',
      description: 'Mở pane theo dõi task PDCA (việc của tôi, và của nhóm nếu là trưởng phòng)',
    })
    const config = await loadConfig($, options)
    if (config.token === '') {
      $.ui.toast('pdca-tasks: chưa có API token (biến môi trường PDCA_TOKEN hoặc cấu hình plugin)')
    } else {
      void refresh($, config)
      $.clock.every(config.seconds * 1000, () => void refresh($, config))
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

    if (snapshot === null) {
      return (
        <Box flexDirection="column">
          <Text dimColor>{state.error === '' ? 'Đang tải task...' : `Lỗi: ${state.error}`}</Text>
          <Button key="refresh" label="Làm mới" hotkey="r" onPress={() => refresh($, config)} />
        </Box>
      )
    }

    const today = todayBangkok(snapshot.fetchedAt)
    const room = Math.max(4, (e.viewport?.rows ?? 24) - 6)
    const rows = (items: TaskItem[], showAssignee: boolean) => {
      const sorted = [...items].sort((a, b) => {
        const byStatus = STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status)
        return byStatus !== 0
          ? byStatus
          : (a.dueDate ?? '9999-12-31').localeCompare(b.dueDate ?? '9999-12-31')
      })
      return sorted.slice(0, room).map(task => {
        const days = task.dueDate === null ? null : daysUntil(task.dueDate, today)
        const isOverdue = days !== null && days < 0
        const due =
          task.dueDate === null
            ? 'không hạn'
            : isOverdue
              ? `quá hạn ${-days} ngày`
              : days === 0
                ? 'hạn hôm nay'
                : `hạn ${task.dueDate}`
        const who = showAssignee && task.assignee ? ` · ${task.assignee}` : ''
        return (
          <Text color={isOverdue || task.status === 'blocked' ? 'red' : undefined}>
            #{task.id} [{STATUS_LABEL[task.status] ?? task.status}] {task.title}
            {who} — {due}
          </Text>
        )
      })
    }

    return (
      <Box flexDirection="column">
        <Text bold>Việc của tôi ({snapshot.mine.length})</Text>
        {snapshot.mine.length === 0 && <Text dimColor>Không có việc đang mở.</Text>}
        {rows(snapshot.mine, false)}
        {snapshot.team.map(project => (
          <Box flexDirection="column">
            <Text bold>
              Nhóm — {project.name} ({project.items.length})
            </Text>
            {project.items.length === 0 && <Text dimColor>Không có việc đang mở.</Text>}
            {rows(project.items, true)}
          </Box>
        ))}
        <Text dimColor>
          {state.error === '' ? '' : `Lỗi lần làm mới gần nhất: ${state.error}. `}
          {state.isLoading ? 'Đang làm mới...' : `Cập nhật ${snapshot.userName}`}
        </Text>
        <Button key="refresh" label="Làm mới" hotkey="r" onPress={() => refresh($, config)} />
      </Box>
    )
  })
}
