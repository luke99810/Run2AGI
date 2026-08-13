import { contextBridge, ipcRenderer } from 'electron'
import type { DesktopBridge, StateSnapshot } from '../../shared/protocol'
import { IPC_CHANNELS } from '../../shared/ipc'

const bridge: DesktopBridge = {
  listSources: () => ipcRenderer.invoke(IPC_CHANNELS.listSources),
  readSnapshot: (sourceId) => ipcRenderer.invoke(IPC_CHANNELS.readSnapshot, sourceId),
  selectProject: (kind) => ipcRenderer.invoke(IPC_CHANNELS.selectProject, kind),
  selectDraftFiles: (scopeId) => ipcRenderer.invoke(IPC_CHANNELS.selectDraftFiles, scopeId),
  selectDraftFolders: (scopeId) => ipcRenderer.invoke(IPC_CHANNELS.selectDraftFolders, scopeId),
  loadRequirementDraft: (scopeId) => ipcRenderer.invoke(IPC_CHANNELS.loadRequirementDraft, scopeId),
  saveRequirementDraft: (scopeId, draft) => ipcRenderer.invoke(IPC_CHANNELS.saveRequirementDraft, scopeId, draft),
  moveRequirementDraft: (sourceScopeId, targetScopeId) => ipcRenderer.invoke(IPC_CHANNELS.moveRequirementDraft, sourceScopeId, targetScopeId),
  discardDraftAttachment: (scopeId, attachmentId) => ipcRenderer.invoke(IPC_CHANNELS.discardDraftAttachment, scopeId, attachmentId),
  exportTaskRecord: (suggestedName, markdown) => ipcRenderer.invoke(IPC_CHANNELS.exportTaskRecord, suggestedName, markdown),
  listManagedResources: (kind) => ipcRenderer.invoke(IPC_CHANNELS.listManagedResources, kind),
  selectManagedResources: (kind, sourceKind) => ipcRenderer.invoke(IPC_CHANNELS.selectManagedResources, kind, sourceKind),
  addManagedResourceUrl: (kind, url) => ipcRenderer.invoke(IPC_CHANNELS.addManagedResourceUrl, kind, url),
  updateManagedResource: (id, patch) => ipcRenderer.invoke(IPC_CHANNELS.updateManagedResource, id, patch),
  removeManagedResource: (id) => ipcRenderer.invoke(IPC_CHANNELS.removeManagedResource, id),
  listConnectors: () => ipcRenderer.invoke(IPC_CHANNELS.listConnectors),
  saveConnector: (input) => ipcRenderer.invoke(IPC_CHANNELS.saveConnector, input),
  removeConnector: (id) => ipcRenderer.invoke(IPC_CHANNELS.removeConnector, id),
  listAgentConfigurations: () => ipcRenderer.invoke(IPC_CHANNELS.listAgentConfigurations),
  saveAgentConfiguration: (roleId, patch) => ipcRenderer.invoke(IPC_CHANNELS.saveAgentConfiguration, roleId, patch),
  removeAgentConfiguration: (roleId) => ipcRenderer.invoke(IPC_CHANNELS.removeAgentConfiguration, roleId),
  selectAgentSystemDocument: (roleId) => ipcRenderer.invoke(IPC_CHANNELS.selectAgentSystemDocument, roleId),
  clearAgentSystemDocument: (roleId) => ipcRenderer.invoke(IPC_CHANNELS.clearAgentSystemDocument, roleId),
  listMcpConfigurations: () => ipcRenderer.invoke(IPC_CHANNELS.listMcpConfigurations),
  saveMcpConfiguration: (input) => ipcRenderer.invoke(IPC_CHANNELS.saveMcpConfiguration, input),
  removeMcpConfiguration: (id) => ipcRenderer.invoke(IPC_CHANNELS.removeMcpConfiguration, id),
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
