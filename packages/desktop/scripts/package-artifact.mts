/**
 * 命令行打包入口 —— 把一个项目目录打成交付包。
 *
 * ★ 为什么要有它：`packageProjectArtifact` 此前只有 Electron 主进程一个调用方，
 *   也就是**只能点桌面端的按钮才打得出包**。CI、容器、以及任何无头环境
 *   都拿不到这个能力，而「交付」恰恰最需要在无头环境里可复现地跑。
 *
 * ★ 用 .mts + `node --experimental-strip-types` 直接跑，不引入编译步骤：
 *   多一个构建产物就多一处「跑的是旧版本」的可能。
 *
 * 用法：
 *   node --experimental-strip-types package-artifact.mts <项目目录> <输出文件> [packetId]
 */

import { packageProjectArtifact } from '../shell/main/artifact-packager.ts'

async function main(): Promise<number> {
  const [source, destination, packetId] = process.argv.slice(2)
  if (source === undefined || destination === undefined) {
    process.stderr.write('用法：package-artifact.mts <项目目录> <输出文件> [packetId]\n')
    return 2
  }

  try {
    const result = await packageProjectArtifact(source, destination, packetId)
    for (const line of result.log) process.stdout.write(`  ${line}\n`)
    // ★ 把摘要打到 stdout 且**结构化**：容器的调用方要能把它接走存档，
    //   而不是从人类可读的日志里正则抠。
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`)
    return 0
  } catch (error) {
    // ★ 打包失败要以非零退出，且把原因原样打出来。
    //   「路径过长」「超出交付上限」这类错误信息本身就是修法的线索，
    //   包装成一句「打包失败」会把它弄丢。
    process.stderr.write(`✗ 打包失败：${error instanceof Error ? error.message : String(error)}\n`)
    return 1
  }
}

process.exitCode = await main()
