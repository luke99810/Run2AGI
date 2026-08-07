import type { CommandReceipt, OperatorAction } from '../../shared/protocol'

export interface CommandRequest {
  readonly action: OperatorAction
  readonly agentId: string
  readonly packetId?: string
  readonly moduleId?: string
  readonly payload?: Readonly<Record<string, unknown>>
}

export type CommandDispatcher = (request: CommandRequest) => Promise<CommandReceipt>
