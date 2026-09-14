import {
  History,
  LogIn,
  PackageOpen,
  Settings,
  Upload,
  UsersRound,
  type LucideIcon,
} from 'lucide-vue-next'

import type { UserRole } from '@/stores/auth'
import type { PortalContour } from '@/stores/runtime'

export type NavigationItem = {
  to: string
  label: string
  icon: LucideIcon
}

export function navigationForRole(
  role: UserRole | undefined,
  contour?: PortalContour,
): NavigationItem[] {
  const items: NavigationItem[] = [
    { to: '/', label: 'Главная', icon: PackageOpen },
    { to: '/history', label: 'История', icon: History },
  ]

  if (role === 'admin' || role === 'operator') {
    if (contour === 'SOURCE') {
      items.splice(1, 0, { to: '/export', label: 'Отправка', icon: Upload })
    }
    if (contour === 'TARGET') {
      items.splice(1, 0, { to: '/import', label: 'Приём', icon: LogIn })
    }
  }

  if (role === 'admin') {
    items.push({ to: '/users', label: 'Пользователи', icon: UsersRound })
    items.push({ to: '/settings', label: 'Настройки', icon: Settings })
  }

  return items
}
