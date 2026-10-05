export type TaskItem = {
  id: number
  title: string
  status: string
  dueDate: string | null
  projectId: number
  assignee?: string
}

export type ProjectTasks = { projectId: number; name: string; items: TaskItem[] }

export type Snapshot = {
  /** Vai trò của người dùng (staff | dept_head | director | admin). */
  role: string
  userName: string
  mine: TaskItem[]
  team: ProjectTasks[]
  /** Thời điểm lấy dữ liệu, ms kể từ epoch. */
  fetchedAt: number
}

export type View = {
  snapshot: Snapshot | null
  /** Thông báo lỗi gần nhất (rỗng khi lần làm mới gần nhất thành công). */
  error: string
  isLoading: boolean
}

declare module 'claude-code' {
  interface PluginState {
    'pdca-tasks': { view: View }
  }
}
