import { contextBridge, ipcRenderer } from "electron";
import type { CodentumDesktopBridge } from "@desktop/data/desktop-bridge";
import {
  STATE_IPC_CHANNELS,
  type StateSnapshot,
  type StateSourceDescriptor,
} from "@desktop/data/state-source";

const desktopBridge = Object.freeze({
  platform: process.platform,
  versions: Object.freeze({
    chrome: process.versions.chrome,
    electron: process.versions.electron,
    node: process.versions.node,
  }),
  state: Object.freeze({
    list: async (): Promise<readonly StateSourceDescriptor[]> =>
      ipcRenderer.invoke(STATE_IPC_CHANNELS.list) as Promise<readonly StateSourceDescriptor[]>,
    read: async (sourceId: string): Promise<StateSnapshot> =>
      ipcRenderer.invoke(STATE_IPC_CHANNELS.read, sourceId) as Promise<StateSnapshot>,
  }),
}) satisfies CodentumDesktopBridge;

contextBridge.exposeInMainWorld("codentum", desktopBridge);
