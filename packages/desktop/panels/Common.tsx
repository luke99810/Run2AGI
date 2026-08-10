import type { ReactNode } from 'react'
import type { PacketState, WorkPacket } from '@codentum/contracts'
import { PACKET_STATE_LABELS, packetTitle, roleLabel, shortPacketId } from '../renderer/src/domain'

export type IconName =
  | 'plus'
  | 'pulse'
  | 'board'
  | 'waves'
  | 'graph'
  | 'wallet'
  | 'people'
  | 'package'
  | 'folder'
  | 'file'
  | 'refresh'
  | 'chevron'
  | 'send'
  | 'pause'
  | 'stop'
  | 'back'
  | 'prompt'
  | 'insert'
  | 'check'
  | 'warning'
  | 'clock'
  | 'menu'
  | 'close'
  | 'chat'
  | 'plug'
  | 'book'
  | 'spark'
  | 'settings'
  | 'help'
  | 'shield'
  | 'search'

const PATHS: Readonly<Record<IconName, ReactNode>> = {
  plus: <><path d="M12 5v14M5 12h14" /></>,
  pulse: <><path d="M3 12h4l2-5 4 10 2-5h6" /></>,
  board: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16M15 4v16" /></>,
  waves: <><path d="M4 6h7M4 12h11M4 18h16" /><circle cx="13" cy="6" r="2" /><circle cx="17" cy="12" r="2" /><circle cx="22" cy="18" r="2" /></>,
  graph: <><circle cx="5" cy="12" r="2.5" /><circle cx="19" cy="6" r="2.5" /><circle cx="19" cy="18" r="2.5" /><path d="m7.5 11 9-4M7.5 13l9 4" /></>,
  wallet: <><path d="M4 6.5A2.5 2.5 0 0 1 6.5 4H19v16H6.5A2.5 2.5 0 0 1 4 17.5z" /><path d="M14 9h7v6h-7a3 3 0 0 1 0-6Z" /></>,
  people: <><circle cx="9" cy="8" r="3" /><circle cx="17" cy="9" r="2.5" /><path d="M3 20c.5-4 2.5-6 6-6s5.5 2 6 6M15 15c3 0 5 1.5 5.5 4.5" /></>,
  package: <><path d="m4 7 8-4 8 4v10l-8 4-8-4zM4 7l8 4 8-4M12 11v10" /></>,
  folder: <><path d="M3 6h7l2 2h9v11H3z" /></>,
  file: <><path d="M6 3h8l4 4v14H6zM14 3v5h5" /></>,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6.2 8A7 7 0 0 1 19 12M5 12a7 7 0 0 0 12.8 4" /></>,
  chevron: <><path d="m9 6 6 6-6 6" /></>,
  send: <><path d="m3 11 18-8-7 18-3-7zM11 14l4-4" /></>,
  pause: <><path d="M8 5v14M16 5v14" /></>,
  stop: <><rect x="6" y="6" width="12" height="12" rx="2" /></>,
  back: <><path d="m9 7-5 5 5 5M5 12h8a6 6 0 0 1 6 6" /></>,
  prompt: <><path d="M4 5h16v12H8l-4 4zM8 9h8M8 13h5" /></>,
  insert: <><path d="M12 3v18M3 12h18" /><circle cx="12" cy="12" r="8" /></>,
  check: <><path d="m5 12 4 4L19 6" /></>,
  warning: <><path d="M12 3 2.8 20h18.4zM12 9v4M12 17h.01" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v6l4 2" /></>,
  menu: <><path d="M4 7h16M4 12h16M4 17h16" /></>,
  close: <><path d="m6 6 12 12M18 6 6 18" /></>,
  chat: <><path d="M4 5h16v12H9l-5 4z" /><path d="M8 9h8M8 13h5" /></>,
  plug: <><path d="M8 3v5M16 3v5M6 8h12v3a6 6 0 0 1-6 6v4M9 21h6" /></>,
  book: <><path d="M4 4h6a3 3 0 0 1 3 3v13a3 3 0 0 0-3-3H4zM20 4h-4a3 3 0 0 0-3 3v13a3 3 0 0 1 3-3h4z" /></>,
  spark: <><path d="m12 3 1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5zM18.5 15l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.4 1a8 8 0 0 0-1.7-1L14.5 3h-5L9 6.1a8 8 0 0 0-1.7 1l-2.4-1-2 3.4 2 1.5a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.4-1a8 8 0 0 0 1.7 1l.5 3.1h5l.5-3.1a8 8 0 0 0 1.7-1l2.4 1 2-3.4-2-1.5a7 7 0 0 0 .1-1z" /></>,
  help: <><circle cx="12" cy="12" r="9" /><path d="M9.6 9a2.6 2.6 0 1 1 3.3 2.5c-.9.3-1.4.9-1.4 1.8M12 17h.01" /></>,
  shield: <><path d="M12 3 20 6v5c0 5-3.4 8.2-8 10-4.6-1.8-8-5-8-10V6z" /><path d="m9 12 2 2 4-4" /></>,
  search: <><circle cx="11" cy="11" r="7" /><path d="m16 16 5 5" /></>
}

export function Icon({ name, size = 20 }: { readonly name: IconName; readonly size?: number }): ReactNode {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  )
}

export function PageHeader({ eyebrow, title, description, actions }: {
  readonly eyebrow?: string
  readonly title: string
  readonly description: string
  readonly actions?: ReactNode
}): ReactNode {
  return (
    <header className="page-header">
      <div>
        {eyebrow === undefined ? null : <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions === undefined ? null : <div className="page-actions">{actions}</div>}
    </header>
  )
}

export function EmptyState({ title, detail, icon = 'clock' }: {
  readonly title: string
  readonly detail: string
  readonly icon?: IconName
}): ReactNode {
  return (
    <div className="empty-state" role="status">
      <span className="empty-icon"><Icon name={icon} size={23} /></span>
      <strong>{title}</strong>
      <p>{detail}</p>
    </div>
  )
}

export function PacketStateChip({ state }: { readonly state: PacketState }): ReactNode {
  return <span className={`status-chip status-${state}`}>{PACKET_STATE_LABELS[state]}</span>
}

export function PacketSummary({ packet, compact = false }: { readonly packet: WorkPacket; readonly compact?: boolean }): ReactNode {
  return (
    <div className={`packet-summary${compact ? ' compact' : ''}`}>
      <div className="packet-summary-top">
        <strong>{packetTitle(packet)}</strong>
        <PacketStateChip state={packet.state} />
      </div>
      <div className="packet-summary-meta">
        <span>{roleLabel(packet.role)}</span>
        <span>{shortPacketId(packet.id)}</span>
        <span>尝试 {packet.attempts}</span>
      </div>
    </div>
  )
}

export function ErrorNotice({ message }: { readonly message: string }): ReactNode {
  return <div className="error-notice" role="alert"><Icon name="warning" size={19} /><span>{message}</span></div>
}

export function WarningNotice({ message }: { readonly message: string }): ReactNode {
  return <div className="warning-notice" role="status"><Icon name="warning" size={19} /><span>{message}</span></div>
}
