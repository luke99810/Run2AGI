import type { SnapshotSourceDescriptor, StateSnapshot } from '../shared/protocol'

export type StateListener = (snapshot: StateSnapshot) => void

/**
 * A single read-only source of Codentum state.
 *
 * Deliberately absent: save/update/delete. Operator commands belong to the
 * engine bridge, never to the state projection.
 */
export interface StateSource {
  readonly descriptor: SnapshotSourceDescriptor
  read(): Promise<StateSnapshot>
  watch(listener: StateListener): () => void
  close(): void
}
