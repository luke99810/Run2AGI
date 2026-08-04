import { ipcMain } from "electron";
import {
  STATE_IPC_CHANNELS,
  type StateSource,
} from "@desktop/data/state-source";

export function registerStateSourceIpc(stateSource: StateSource): void {
  ipcMain.removeHandler(STATE_IPC_CHANNELS.list);
  ipcMain.removeHandler(STATE_IPC_CHANNELS.read);

  ipcMain.handle(STATE_IPC_CHANNELS.list, async () => stateSource.list());
  ipcMain.handle(STATE_IPC_CHANNELS.read, async (_event, sourceId: unknown) => {
    if (typeof sourceId !== "string" || sourceId.length === 0) {
      throw new TypeError("sourceId 必须是非空字符串");
    }
    return stateSource.read(sourceId);
  });
}
