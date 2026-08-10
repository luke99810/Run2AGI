import { useCallback, useEffect, useMemo, useState } from 'react'
import type {
  CommandReceipt,
  DraftAttachment,
  EngineHandshake,
  OperatorCommand,
  RequirementDraftSnapshot,
  SnapshotSourceDescriptor,
  StateSnapshot
} from '../../shared/protocol'
import { EMPTY_CAPABILITIES } from '../../shared/protocol'

const NO_ENGINE: EngineHandshake = {
  connected: false,
  protocolVersion: 1,
  engineVersion: 'unavailable',
  stateRevision: 0,
  capabilities: EMPTY_CAPABILITIES,
  unavailableReason: '正在连接本地引擎'
}

export interface DesktopState {
  readonly bridgeAvailable: boolean
  readonly sources: readonly SnapshotSourceDescriptor[]
  readonly selectedSourceId: string | null
  readonly snapshot: StateSnapshot | null
  readonly handshake: EngineHandshake
  readonly loading: boolean
  readonly error: string | null
  readonly selectSource: (sourceId: string) => void
  readonly selectProject: () => Promise<void>
  readonly selectDraftFiles: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly selectDraftFolders: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly loadRequirementDraft: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly saveRequirementDraft: (scopeId: string, draft: RequirementDraftSnapshot) => Promise<void>
  readonly moveRequirementDraft: (sourceScopeId: string, targetScopeId: string) => Promise<RequirementDraftSnapshot>
  readonly discardDraftAttachment: (scopeId: string, attachmentId: DraftAttachment['id']) => Promise<RequirementDraftSnapshot>
  readonly exportChatRecord: (suggestedName: string, markdown: string) => Promise<boolean>
  readonly refresh: () => Promise<void>
  readonly sendCommand: (command: OperatorCommand) => Promise<CommandReceipt>
}

function errorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error)
  return message.replace(/^Error invoking remote method '[^']+':\s*(?:[A-Za-z]+Error:\s*)?/u, '')
}

export function useDesktop(): DesktopState {
  const bridge = typeof window === 'undefined' ? undefined : window.codentum
  const [sources, setSources] = useState<readonly SnapshotSourceDescriptor[]>([])
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null)
  const [snapshot, setSnapshot] = useState<StateSnapshot | null>(null)
  const [handshake, setHandshake] = useState<EngineHandshake>(NO_ENGINE)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (bridge === undefined) {
      setLoading(false)
      setError('桌面桥接未加载。请通过 Electron 启动 Codentum。')
      return
    }
    let alive = true
    void Promise.all([bridge.listSources(), bridge.getEngineHandshake()])
      .then(([nextSources, nextHandshake]) => {
        if (!alive) return
        setSources(nextSources)
        setHandshake(nextHandshake)
        const preferred = nextSources.find((source) => source.kind === 'project')
          ?? nextSources.find((source) => source.id.includes('mid-flight'))
          ?? nextSources[0]
        setSelectedSourceId((current) => current ?? preferred?.id ?? null)
        setLoading(preferred !== undefined)
      })
      .catch((reason: unknown) => {
        if (!alive) return
        setLoading(false)
        setError(errorMessage(reason))
      })
    return () => {
      alive = false
    }
  }, [bridge])

  useEffect(() => {
    if (bridge === undefined || selectedSourceId === null) return
    let alive = true
    setLoading(true)
    setError(null)
    const unsubscribe = bridge.onSnapshot((nextSnapshot) => {
      if (alive && nextSnapshot.source.id === selectedSourceId) {
        setSnapshot(nextSnapshot)
        setLoading(false)
        void bridge.getEngineHandshake()
          .then((nextHandshake) => {
            if (alive) setHandshake(nextHandshake)
          })
          .catch((reason: unknown) => {
            if (alive) setError(errorMessage(reason))
          })
      }
    })
    void bridge.watchSource(selectedSourceId)
      .then(() => Promise.all([
        bridge.readSnapshot(selectedSourceId),
        bridge.getEngineHandshake()
      ]))
      .then(([nextSnapshot, nextHandshake]) => {
        if (!alive) return
        setSnapshot(nextSnapshot)
        setHandshake(nextHandshake)
        setLoading(false)
      })
      .catch((reason: unknown) => {
        if (!alive) return
        setLoading(false)
        setError(errorMessage(reason))
      })
    return () => {
      alive = false
      unsubscribe()
    }
  }, [bridge, selectedSourceId])

  const selectProject = useCallback(async () => {
    if (bridge === undefined) return
    setError(null)
    try {
      const source = await bridge.selectProject()
      if (source === null) return
      setSources((current) => [
        ...current.filter((item) => item.kind !== 'project'),
        source
      ])
      setSnapshot(null)
      setSelectedSourceId(source.id)
    } catch (reason) {
      setError(errorMessage(reason))
    }
  }, [bridge])

  const selectSource = useCallback((sourceId: string) => {
    if (selectedSourceId !== sourceId) setSnapshot(null)
    setSelectedSourceId(sourceId)
  }, [selectedSourceId])

  const selectDraftFiles = useCallback(async (scopeId: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      return await bridge.selectDraftFiles(scopeId)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const exportChatRecord = useCallback(async (suggestedName: string, markdown: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      return await bridge.exportChatRecord(suggestedName, markdown)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const selectDraftFolders = useCallback(async (scopeId: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      return await bridge.selectDraftFolders(scopeId)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const loadRequirementDraft = useCallback(async (scopeId: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      return await bridge.loadRequirementDraft(scopeId)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const saveRequirementDraft = useCallback(async (scopeId: string, draft: RequirementDraftSnapshot) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      await bridge.saveRequirementDraft(scopeId, draft)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const moveRequirementDraft = useCallback(async (sourceScopeId: string, targetScopeId: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      return await bridge.moveRequirementDraft(sourceScopeId, targetScopeId)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const discardDraftAttachment = useCallback(async (scopeId: string, attachmentId: DraftAttachment['id']) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      return await bridge.discardDraftAttachment(scopeId, attachmentId)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const refresh = useCallback(async () => {
    if (bridge === undefined) return
    setError(null)
    setLoading(true)
    try {
      const [nextHandshake, nextSnapshot] = await Promise.all([
        bridge.getEngineHandshake(),
        selectedSourceId === null ? Promise.resolve(null) : bridge.readSnapshot(selectedSourceId)
      ])
      setHandshake(nextHandshake)
      if (nextSnapshot !== null) setSnapshot(nextSnapshot)
    } catch (reason) {
      setError(errorMessage(reason))
    } finally {
      setLoading(false)
    }
  }, [bridge, selectedSourceId])

  const sendCommand = useCallback(async (command: OperatorCommand) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    const receipt = await bridge.sendCommand(command)
    setHandshake((current) => ({ ...current, stateRevision: Math.max(current.stateRevision, receipt.stateRevision) }))
    void bridge.getEngineHandshake()
      .then(setHandshake)
      .catch(() => undefined)
    return receipt
  }, [bridge])

  return useMemo(() => ({
    bridgeAvailable: bridge !== undefined,
    sources,
    selectedSourceId,
    snapshot,
    handshake,
    loading,
    error,
    selectSource,
    selectProject,
    selectDraftFiles,
    selectDraftFolders,
    loadRequirementDraft,
    saveRequirementDraft,
    moveRequirementDraft,
    discardDraftAttachment,
    exportChatRecord,
    refresh,
    sendCommand
  }), [bridge, sources, selectedSourceId, snapshot, handshake, loading, error, selectSource, selectProject, selectDraftFiles, selectDraftFolders, loadRequirementDraft, saveRequirementDraft, moveRequirementDraft, discardDraftAttachment, exportChatRecord, refresh, sendCommand])
}
