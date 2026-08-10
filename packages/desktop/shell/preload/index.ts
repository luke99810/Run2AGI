import { contextBridge, ipcRenderer } from 'electron'
import type { DesktopBridge, StateSnapshot } from '../../shared/protocol'
import { IPC_CHANNELS } from '../../shared/ipc'

const bridge: DesktopBridge = {
  listSources: () => ipcRenderer.invoke(IPC_CHANNELS.listSources),
  readSnapshot: (sourceId) => ipcRenderer.invoke(IPC_CHANNELS.readSnapshot, sourceId),
  selectProject: () => ipcRenderer.invoke(IPC_CHANNELS.selectProject),
  selectDraftFiles: (scopeId) => ipcRenderer.invoke(IPC_CHANNELS.selectDraftFiles, scopeId),
  selectDraftFolders: (scopeId) => ipcRenderer.invoke(IPC_CHANNELS.selectDraftFolders, scopeId),
  loadRequirementDraft: (scopeId) => ipcRenderer.invoke(IPC_CHANNELS.loadRequirementDraft, scopeId),
  saveRequirementDraft: (scopeId, draft) => ipcRenderer.invoke(IPC_CHANNELS.saveRequirementDraft, scopeId, draft),
  moveRequirementDraft: (sourceScopeId, targetScopeId) => ipcRenderer.invoke(IPC_CHANNELS.moveRequirementDraft, sourceScopeId, targetScopeId),
  discardDraftAttachment: (scopeId, attachmentId) => ipcRenderer.invoke(IPC_CHANNELS.discardDraftAttachment, scopeId, attachmentId),
  exportChatRecord: (suggestedName, markdown) => ipcRenderer.invoke(IPC_CHANNELS.exportChatRecord, suggestedName, markdown),
  watchSource: (sourceId) => ipcRenderer.invoke(IPC_CHANNELS.watchSource, sourceId),
  onSnapshot: (listener) => {
    const handler = (_event: Electron.IpcRendererEvent, snapshot: StateSnapshot): void => listener(snapshot)
    ipcRenderer.on(IPC_CHANNELS.snapshotChanged, handler)
    return () => ipcRenderer.removeListener(IPC_CHANNELS.snapshotChanged, handler)
  },
  getEngineHandshake: () => ipcRenderer.invoke(IPC_CHANNELS.engineHandshake),
  sendCommand: (command) => ipcRenderer.invoke(IPC_CHANNELS.engineCommand, command)
}

contextBridge.exposeInMainWorld('codentum', Object.freeze(bridge))
