export const IPC_CHANNELS = {
  listSources: 'codentum:state:list-sources',
  readSnapshot: 'codentum:state:read-snapshot',
  selectProject: 'codentum:state:select-project',
  selectDraftFiles: 'codentum:draft:select-files',
  selectDraftFolders: 'codentum:draft:select-folders',
  loadRequirementDraft: 'codentum:draft:load-requirement',
  saveRequirementDraft: 'codentum:draft:save-requirement',
  moveRequirementDraft: 'codentum:draft:move-requirement',
  discardDraftAttachment: 'codentum:draft:discard-attachment',
  exportTaskRecord: 'codentum:task:export-record',
  watchSource: 'codentum:state:watch-source',
  snapshotChanged: 'codentum:state:snapshot-changed',
  engineHandshake: 'codentum:engine:handshake',
  engineCommand: 'codentum:engine:command'
} as const
