import { History, LogIn, PackageOpen, Settings, Upload, type LucideIcon } from 'lucide-vue-next'

import type { UserRole } from '@/stores/auth'

export type NavigationItem = {
  to: string
  label: string
  icon: LucideIcon
}

export function navigationForRole(role: UserRole | undefined): NavigationItem[] {
  const items: NavigationItem[] = [
    { to: '/', label: 'Главная', icon: PackageOpen },
    { to: '/history', label: 'История', icon: History },
  ]

  if (role === 'admin' || role === 'operator') {
    items.splice(1, 0, { to: '/export', label: 'Отправка', icon: Upload })
    items.splice(2, 0, { to: '/import', label: 'Приём', icon: LogIn })
  }

  if (role === 'admin') {
    items.push({ to: '/settings', label: 'Настройки', icon: Settings })
  }

  return items
}
