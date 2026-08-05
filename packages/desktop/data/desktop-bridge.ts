import type { StateSource } from "./state-source";

export interface DesktopRuntimeInfo {
  readonly platform: NodeJS.Platform;
  readonly versions: {
    readonly chrome: string;
    readonly electron: string;
    readonly node: string;
  };
}

/** preload 暴露给渲染进程的全部能力。state 只满足查询接口。 */
export interface CodentumDesktopBridge extends DesktopRuntimeInfo {
  readonly state: StateSource;
}
