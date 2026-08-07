export const IPC_CHANNELS = {
  listSources: 'codentum:state:list-sources',
  readSnapshot: 'codentum:state:read-snapshot',
  selectProject: 'codentum:state:select-project',
  selectDraftFiles: 'codentum:draft:select-files',
  loadRequirementDraft: 'codentum:draft:load-requirement',
  saveRequirementDraft: 'codentum:draft:save-requirement',
  moveRequirementDraft: 'codentum:draft:move-requirement',
  discardDraftAttachment: 'codentum:draft:discard-attachment',
  watchSource: 'codentum:state:watch-source',
  snapshotChanged: 'codentum:state:snapshot-changed',
  engineHandshake: 'codentum:engine:handshake',
  engineCommand: 'codentum:engine:command'
} as const
