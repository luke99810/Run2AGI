import { useCallback, useEffect, useMemo, useState } from 'react'
import type {
  CommandReceipt,
  AgentConfiguration,
  AgentConfigurationPatch,
  ConnectorConfiguration,
  ConnectorConfigurationInput,
  DraftAttachment,
  EngineHandshake,
  ManagedResource,
  ManagedResourceKind,
  ManagedResourcePatch,
  McpConfiguration,
  McpConfigurationInput,
  OperatorCommand,
  ProjectSelectionKind,
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
  readonly selectProject: (kind: ProjectSelectionKind) => Promise<void>
  readonly selectDraftFiles: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly selectDraftFolders: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly loadRequirementDraft: (scopeId: string) => Promise<RequirementDraftSnapshot>
  readonly saveRequirementDraft: (scopeId: string, draft: RequirementDraftSnapshot) => Promise<void>
  readonly moveRequirementDraft: (sourceScopeId: string, targetScopeId: string) => Promise<RequirementDraftSnapshot>
  readonly discardDraftAttachment: (scopeId: string, attachmentId: DraftAttachment['id']) => Promise<RequirementDraftSnapshot>
  readonly exportTaskRecord: (suggestedName: string, markdown: string) => Promise<boolean>
  readonly listManagedResources: (kind?: ManagedResourceKind) => Promise<readonly ManagedResource[]>
  readonly selectManagedResources: (kind: ManagedResourceKind, sourceKind: 'file' | 'folder') => Promise<readonly ManagedResource[]>
  readonly addManagedResourceUrl: (kind: ManagedResourceKind, url: string) => Promise<ManagedResource>
  readonly updateManagedResource: (id: string, patch: ManagedResourcePatch) => Promise<ManagedResource>
  readonly removeManagedResource: (id: string) => Promise<boolean>
  readonly listConnectors: () => Promise<readonly ConnectorConfiguration[]>
  readonly saveConnector: (input: ConnectorConfigurationInput) => Promise<ConnectorConfiguration>
  readonly removeConnector: (id: string) => Promise<boolean>
  readonly listAgentConfigurations: () => Promise<readonly AgentConfiguration[]>
  readonly saveAgentConfiguration: (roleId: string, patch: AgentConfigurationPatch) => Promise<AgentConfiguration>
  readonly removeAgentConfiguration: (roleId: string) => Promise<boolean>
  readonly selectAgentSystemDocument: (roleId: string) => Promise<AgentConfiguration>
  readonly clearAgentSystemDocument: (roleId: string) => Promise<AgentConfiguration>
  readonly listMcpConfigurations: () => Promise<readonly McpConfiguration[]>
  readonly saveMcpConfiguration: (input: McpConfigurationInput) => Promise<McpConfiguration>
  readonly removeMcpConfiguration: (id: string) => Promise<boolean>
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

  const selectProject = useCallback(async (kind: ProjectSelectionKind) => {
    if (bridge === undefined) return
    setError(null)
    try {
      const source = await bridge.selectProject(kind)
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

  const exportTaskRecord = useCallback(async (suggestedName: string, markdown: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      return await bridge.exportTaskRecord(suggestedName, markdown)
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

  const listManagedResources = useCallback(async (kind?: ManagedResourceKind) => {
    if (bridge === undefined) return []
    try {
      return await bridge.listManagedResources(kind)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const selectManagedResources = useCallback(async (kind: ManagedResourceKind, sourceKind: 'file' | 'folder') => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      return await bridge.selectManagedResources(kind, sourceKind)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const addManagedResourceUrl = useCallback(async (kind: ManagedResourceKind, url: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      return await bridge.addManagedResourceUrl(kind, url)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const updateManagedResource = useCallback(async (id: string, patch: ManagedResourcePatch) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      return await bridge.updateManagedResource(id, patch)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const removeManagedResource = useCallback(async (id: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    try {
      return await bridge.removeManagedResource(id)
    } catch (reason) {
      throw new Error(errorMessage(reason))
    }
  }, [bridge])

  const listConnectors = useCallback(async () => bridge?.listConnectors() ?? [], [bridge])
  const saveConnector = useCallback(async (input: ConnectorConfigurationInput) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    return bridge.saveConnector(input)
  }, [bridge])
  const removeConnector = useCallback(async (id: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    return bridge.removeConnector(id)
  }, [bridge])
  const listAgentConfigurations = useCallback(async () => bridge?.listAgentConfigurations() ?? [], [bridge])
  const saveAgentConfiguration = useCallback(async (roleId: string, patch: AgentConfigurationPatch) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    return bridge.saveAgentConfiguration(roleId, patch)
  }, [bridge])
  const removeAgentConfiguration = useCallback(async (roleId: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    return bridge.removeAgentConfiguration(roleId)
  }, [bridge])
  const selectAgentSystemDocument = useCallback(async (roleId: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    return bridge.selectAgentSystemDocument(roleId)
  }, [bridge])
  const clearAgentSystemDocument = useCallback(async (roleId: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    return bridge.clearAgentSystemDocument(roleId)
  }, [bridge])
  const listMcpConfigurations = useCallback(async () => bridge?.listMcpConfigurations() ?? [], [bridge])
  const saveMcpConfiguration = useCallback(async (input: McpConfigurationInput) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    return bridge.saveMcpConfiguration(input)
  }, [bridge])
  const removeMcpConfiguration = useCallback(async (id: string) => {
    if (bridge === undefined) throw new Error('桌面桥接未加载')
    return bridge.removeMcpConfiguration(id)
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
    exportTaskRecord,
    listManagedResources,
    selectManagedResources,
    addManagedResourceUrl,
    updateManagedResource,
    removeManagedResource,
    listConnectors,
    saveConnector,
    removeConnector,
    listAgentConfigurations,
    saveAgentConfiguration,
    removeAgentConfiguration,
    selectAgentSystemDocument,
    clearAgentSystemDocument,
    listMcpConfigurations,
    saveMcpConfiguration,
    removeMcpConfiguration,
    refresh,
    sendCommand
  }), [bridge, sources, selectedSourceId, snapshot, handshake, loading, error, selectSource, selectProject, selectDraftFiles, selectDraftFolders, loadRequirementDraft, saveRequirementDraft, moveRequirementDraft, discardDraftAttachment, exportTaskRecord, listManagedResources, selectManagedResources, addManagedResourceUrl, updateManagedResource, removeManagedResource, listConnectors, saveConnector, removeConnector, listAgentConfigurations, saveAgentConfiguration, removeAgentConfiguration, selectAgentSystemDocument, clearAgentSystemDocument, listMcpConfigurations, saveMcpConfiguration, removeMcpConfiguration, refresh, sendCommand])
}
