export type TaskItem = {
  id: number
  title: string
  status: string
  dueDate: string | null
  projectId: number
  assigneeId: number
  assignee: string
}

export type Snapshot = {
  /** Vai trò của người dùng (staff | dept_head | director | admin). */
  role: string
  userId: number
  userName: string
  /** Task đang mở: của tôi, và của cả nhóm nếu là trưởng phòng/giám đốc (đã khử trùng theo id). */
  tasks: TaskItem[]
  /** Số task đã xong theo người thực hiện (khóa là user_id), chỉ có ở vai trò quản lý. */
  doneByUser: Record<string, number>
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
