import type { CodentumDesktopBridge } from "@desktop/data/desktop-bridge";

declare global {
  interface Window {
    readonly codentum: CodentumDesktopBridge;
  }
}

export {};
