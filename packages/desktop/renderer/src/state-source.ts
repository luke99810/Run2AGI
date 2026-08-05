import type { StateSource } from "@desktop/data/state-source";

/** React 页面只依赖这个接口，不感知 Electron IPC 或文件系统。 */
export const stateSource: StateSource = Object.freeze({
  list: async () => window.codentum.state.list(),
  read: async (sourceId: string) => window.codentum.state.read(sourceId),
});
