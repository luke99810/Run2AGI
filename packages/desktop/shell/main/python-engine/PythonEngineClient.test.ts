import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { PythonEngineClient } from './PythonEngineClient'

describe('PythonEngineClient', () => {
  it('exchanges UTF-8 JSONL without a shell', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'codentum-sidecar-client-'))
    const script = join(dir, 'engine.mjs')
    await writeFile(
      script,
      [
        "import readline from 'node:readline'",
        "const rl = readline.createInterface({ input: process.stdin })",
        "rl.on('line', (line) => {",
        "  const request = JSON.parse(line)",
        "  if (request.method === 'shutdown') {",
        "    process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { stopped: true } }) + '\\n')",
        "    setImmediate(() => process.exit(0))",
        "    return",
        "  }",
        "  process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: request.params }) + '\\n')",
        "})"
      ].join('\n'),
      'utf8'
    )

    const client = PythonEngineClient.launch({ executable: process.execPath, args: [script] })
    await expect(client.request('echo', { text: '中文模块' })).resolves.toEqual({ text: '中文模块' })
    await client.close()
    expect(client.isRunning).toBe(false)
  })

  it('times out instead of leaving a pending fake action', async () => {
    const client = PythonEngineClient.launch({
      executable: process.execPath,
      args: ['-e', "process.stdin.resume()"]
    })
    await expect(client.request('never', {}, 50)).rejects.toMatchObject({ code: 'ENGINE_TIMEOUT' })
    await client.close(50)
  })
})
