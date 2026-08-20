/** Vesta Desktop 的统一 lucide 图标入口。 */

import {
  Activity,
  AlertCircle,
  Archive,
  Bot,
  Check,
  ChevronDown,
  Clock3,
  Download,
  ExternalLink,
  FileText,
  ListChecks,
  MessageSquare,
  Monitor,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Send,
  Settings,
  ShieldCheck,
  Workflow,
  X,
  type LucideIcon,
  type LucideProps,
} from 'lucide-react'
import type { ReactElement } from 'react'

export type IconName =
  | 'chat'
  | 'runs'
  | 'automations'
  | 'approvals'
  | 'artifacts'
  | 'computer'
  | 'settings'
  | 'plus'
  | 'send'
  | 'chevronDown'
  | 'download'
  | 'external'
  | 'check'
  | 'alert'
  | 'activity'
  | 'panelClose'
  | 'panelOpen'
  | 'file'
  | 'close'
  | 'agent'

const ICONS: Record<IconName, LucideIcon> = {
  chat: MessageSquare,
  runs: ListChecks,
  automations: Workflow,
  approvals: ShieldCheck,
  artifacts: Archive,
  computer: Monitor,
  settings: Settings,
  plus: Plus,
  send: Send,
  chevronDown: ChevronDown,
  download: Download,
  external: ExternalLink,
  check: Check,
  alert: AlertCircle,
  activity: Activity,
  panelClose: PanelLeftClose,
  panelOpen: PanelLeftOpen,
  file: FileText,
  close: X,
  agent: Bot,
}

export interface IconProps extends Omit<LucideProps, 'name'> {
  name: IconName
  size?: number
}

export function Icon({ name, size = 16, ...rest }: IconProps): ReactElement {
  const Component = ICONS[name]
  return <Component size={size} strokeWidth={1.8} aria-hidden="true" {...rest} />
}

export const ActivityClockIcon = Clock3
