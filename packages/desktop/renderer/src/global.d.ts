import type { DesktopBridge } from '../../shared/protocol'

declare global {
  interface Window {
    readonly codentum?: DesktopBridge
  }
}

export {}
