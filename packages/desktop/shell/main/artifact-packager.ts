import { Buffer } from 'node:buffer'
import { createHash } from 'node:crypto'
import { constants } from 'node:fs'
import { lstat, mkdir, mkdtemp, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import { gunzipSync, gzipSync } from 'node:zlib'
import type { ArtifactPackageResult } from '../../shared/protocol'

const MAX_FILES = 20_000
const MAX_SOURCE_BYTES = 256 * 1024 * 1024
const MAX_FILE_BYTES = 64 * 1024 * 1024
const EXCLUDED_DIRECTORIES = new Set(['.git', '.codentum', 'node_modules'])

interface SourceEntry {
  readonly path: string
  readonly content: Buffer
  readonly sha256: string
}

interface DeliveryManifest {
  readonly schema: 'codentum.source-delivery.v1'
  readonly createdAt: string
  readonly packetId?: string
  readonly files: readonly { readonly path: string; readonly sizeBytes: number; readonly sha256: string }[]
  readonly excluded: readonly string[]
}

export async function packageProjectArtifact(
  sourceRoot: string,
  destination: string,
  packetId?: string,
  now = new Date()
): Promise<ArtifactPackageResult> {
  const root = await realpath(sourceRoot)
  const log: string[] = ['开始扫描已绑定项目。']
  const excluded: string[] = []
  const entries = await collectEntries(root, excluded)
  const createdAt = now.toISOString()
  const manifest: DeliveryManifest = {
    schema: 'codentum.source-delivery.v1',
    createdAt,
    ...(packetId === undefined ? {} : { packetId }),
    files: entries.map((entry) => ({ path: entry.path, sizeBytes: entry.content.byteLength, sha256: entry.sha256 })),
    excluded
  }
  const manifestBuffer = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
  const tar = createTar([
    { path: 'CODENTUM-DELIVERY.json', content: manifestBuffer, sha256: sha256(manifestBuffer) },
    ...entries.map((entry) => ({ ...entry, path: `project/${entry.path}` }))
  ], now)
  const archive = gzipSync(tar, { level: 9 })
  await mkdir(dirname(destination), { recursive: true })
  await writeFile(destination, archive, { flag: 'w', mode: 0o600 })
  log.push(`已打包 ${entries.length} 个文件，共 ${entries.reduce((sum, entry) => sum + entry.content.byteLength, 0)} bytes。`)
  log.push(`已写入清单 CODENTUM-DELIVERY.json；排除 ${excluded.length} 项。`)
  await verifyArtifactArchive(destination)
  log.push('隔离解包验证通过：路径、文件数量、大小和逐文件 SHA-256 全部一致。')
  return {
    fileName: basename(destination),
    sha256: sha256(archive),
    fileCount: entries.length,
    sourceBytes: entries.reduce((sum, entry) => sum + entry.content.byteLength, 0),
    archiveBytes: archive.byteLength,
    ...(packetId === undefined ? {} : { packetId }),
    verified: true,
    createdAt,
    log
  }
}

export async function verifyArtifactArchive(archivePath: string): Promise<void> {
  const archive = await readFile(archivePath)
  const unpacked = parseTar(gunzipSync(archive))
  const manifestBuffer = unpacked.get('CODENTUM-DELIVERY.json')
  if (manifestBuffer === undefined) throw new Error('交付包缺少 CODENTUM-DELIVERY.json')
  const manifest: unknown = JSON.parse(manifestBuffer.toString('utf8'))
  if (!isDeliveryManifest(manifest)) throw new Error('交付清单格式无效')
  const temporary = await mkdtemp(join(tmpdir(), 'codentum-delivery-verify-'))
  try {
    for (const [path, content] of unpacked) {
      assertArchivePath(path)
      const output = resolve(temporary, ...path.split('/'))
      if (!isWithin(temporary, output)) throw new Error(`交付包路径越界：${path}`)
      await mkdir(dirname(output), { recursive: true })
      await writeFile(output, content, { flag: 'wx', mode: 0o600 })
    }
    if (manifest.files.length !== unpacked.size - 1) throw new Error('交付包文件数量与清单不一致')
    for (const file of manifest.files) {
      assertArchivePath(file.path)
      const unpackedPath = resolve(temporary, 'project', ...file.path.split('/'))
      if (!isWithin(resolve(temporary, 'project'), unpackedPath)) throw new Error(`清单路径越界：${file.path}`)
      const content = await readFile(unpackedPath)
      if (content.byteLength !== file.sizeBytes || sha256(content) !== file.sha256) {
        throw new Error(`交付包校验失败：${file.path}`)
      }
    }
  } finally {
    await rm(temporary, { recursive: true, force: true })
  }
}

async function collectEntries(root: string, excluded: string[]): Promise<SourceEntry[]> {
  const entries: SourceEntry[] = []
  let sourceBytes = 0
  async function visit(directory: string): Promise<void> {
    const children = await readdir(directory, { withFileTypes: true })
    children.sort((left, right) => left.name.localeCompare(right.name))
    for (const child of children) {
      const absolute = join(directory, child.name)
      const rel = relative(root, absolute).split(sep).join('/')
      if (child.isSymbolicLink()) {
        excluded.push(`${rel} (symbolic link)`)
        continue
      }
      if (child.isDirectory()) {
        if (EXCLUDED_DIRECTORIES.has(child.name)) {
          excluded.push(`${rel}/`)
          continue
        }
        await visit(absolute)
        continue
      }
      if (!child.isFile()) {
        excluded.push(`${rel} (non-regular file)`)
        continue
      }
      if (isSensitiveName(child.name)) {
        excluded.push(`${rel} (sensitive file)`)
        continue
      }
      const stat = await lstat(absolute)
      if (stat.size > MAX_FILE_BYTES) throw new Error(`文件超过单文件交付上限：${rel}`)
      const resolved = await realpath(absolute)
      if (!isWithin(root, resolved)) throw new Error(`拒绝打包项目外文件：${rel}`)
      const content = await readFile(resolved, { flag: constants.O_RDONLY })
      sourceBytes += content.byteLength
      if (sourceBytes > MAX_SOURCE_BYTES) throw new Error('项目源码超过 256 MiB 交付上限')
      entries.push({ path: rel, content, sha256: sha256(content) })
      if (entries.length > MAX_FILES) throw new Error(`项目文件数量超过 ${MAX_FILES} 项交付上限`)
    }
  }
  await visit(root)
  return entries
}

function isSensitiveName(name: string): boolean {
  const lower = name.toLowerCase()
  return (
    (lower === '.env' || (lower.startsWith('.env.') && lower !== '.env.example')) ||
    lower === 'id_rsa' || lower === 'id_ed25519' || lower.endsWith('.pem') || lower.endsWith('.key')
  )
}

function createTar(entries: readonly SourceEntry[], now: Date): Buffer {
  const blocks: Buffer[] = []
  for (const entry of entries) {
    const header = tarHeader(entry.path, entry.content.byteLength, Math.floor(now.getTime() / 1000))
    blocks.push(header, entry.content)
    const padding = (512 - (entry.content.byteLength % 512)) % 512
    if (padding > 0) blocks.push(Buffer.alloc(padding))
  }
  blocks.push(Buffer.alloc(1024))
  return Buffer.concat(blocks)
}

function tarHeader(path: string, size: number, mtime: number): Buffer {
  assertArchivePath(path)
  const encoded = Buffer.from(path, 'utf8')
  if (encoded.byteLength > 100) throw new Error(`交付包路径过长：${path}`)
  const header = Buffer.alloc(512)
  encoded.copy(header, 0)
  writeOctal(header, 100, 8, 0o644)
  writeOctal(header, 108, 8, 0)
  writeOctal(header, 116, 8, 0)
  writeOctal(header, 124, 12, size)
  writeOctal(header, 136, 12, mtime)
  header.fill(0x20, 148, 156)
  header[156] = '0'.charCodeAt(0)
  Buffer.from('ustar\0', 'ascii').copy(header, 257)
  Buffer.from('00', 'ascii').copy(header, 263)
  const checksum = header.reduce((sum, value) => sum + value, 0)
  writeOctal(header, 148, 8, checksum)
  return header
}

function writeOctal(buffer: Buffer, offset: number, length: number, value: number): void {
  const text = value.toString(8).padStart(length - 1, '0')
  if (text.length > length - 1) throw new Error('交付包元数据超出 TAR 范围')
  buffer.write(`${text}\0`, offset, length, 'ascii')
}

function parseTar(tar: Buffer): Map<string, Buffer> {
  const entries = new Map<string, Buffer>()
  let offset = 0
  while (offset + 512 <= tar.byteLength) {
    const header = tar.subarray(offset, offset + 512)
    if (header.every((value) => value === 0)) break
    const path = nullTerminated(header.subarray(0, 100))
    assertArchivePath(path)
    const sizeText = nullTerminated(header.subarray(124, 136)).trim()
    const size = Number.parseInt(sizeText || '0', 8)
    if (!Number.isSafeInteger(size) || size < 0) throw new Error(`交付包文件大小无效：${path}`)
    const start = offset + 512
    const end = start + size
    if (end > tar.byteLength) throw new Error(`交付包内容不完整：${path}`)
    if (entries.has(path)) throw new Error(`交付包包含重复路径：${path}`)
    entries.set(path, Buffer.from(tar.subarray(start, end)))
    offset = start + Math.ceil(size / 512) * 512
  }
  return entries
}

function nullTerminated(value: Buffer): string {
  const end = value.indexOf(0)
  return value.subarray(0, end === -1 ? value.length : end).toString('utf8')
}

function assertArchivePath(path: string): void {
  if (path === '' || isAbsolute(path) || path.includes('\\') || path.split('/').some((part) => part === '' || part === '.' || part === '..')) {
    throw new Error(`交付包路径无效：${path}`)
  }
}

function isDeliveryManifest(value: unknown): value is DeliveryManifest {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return record['schema'] === 'codentum.source-delivery.v1' && typeof record['createdAt'] === 'string' && Array.isArray(record['files']) && record['files'].every((file) => {
    if (typeof file !== 'object' || file === null || Array.isArray(file)) return false
    const item = file as Record<string, unknown>
    return typeof item['path'] === 'string' && typeof item['sizeBytes'] === 'number' && typeof item['sha256'] === 'string'
  }) && Array.isArray(record['excluded']) && record['excluded'].every((item) => typeof item === 'string')
}

function isWithin(root: string, target: string): boolean {
  const rel = relative(root, target)
  return rel === '' || (!rel.startsWith(`..${sep}`) && rel !== '..' && !isAbsolute(rel))
}

function sha256(content: Buffer): string {
  return createHash('sha256').update(content).digest('hex')
}
