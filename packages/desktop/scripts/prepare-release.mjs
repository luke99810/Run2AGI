import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { readFile, readdir, stat, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const desktopRoot = resolve(scriptDirectory, '..')
const releaseDirectory = join(desktopRoot, 'release')
const installerPath = join(releaseDirectory, 'Codentum-Setup.exe')
const lockPath = join(desktopRoot, 'package-lock.json')

async function firstLicenseFile(directory) {
  if (!existsSync(directory)) return undefined
  const entries = await readdir(directory, { withFileTypes: true })
  const candidate = entries
    .filter((entry) => entry.isFile() && /^(license|copying|notice)(\.|$)/iu.test(entry.name))
    .sort((left, right) => left.name.localeCompare(right.name))[0]
  return candidate === undefined ? undefined : join(directory, candidate.name)
}

function packageName(lockKey) {
  return lockKey.slice(lockKey.lastIndexOf('node_modules/') + 'node_modules/'.length)
}

async function npmLicenseSections() {
  const lock = JSON.parse(await readFile(lockPath, 'utf8'))
  const packages = Object.entries(lock.packages)
    .filter(([key, value]) => key.startsWith('node_modules/') && value.dev !== true)
  const electron = lock.packages['node_modules/electron']
  if (electron !== undefined) packages.push(['node_modules/electron', electron])

  const seen = new Set()
  const sections = []
  for (const [key, metadata] of packages.sort(([left], [right]) => left.localeCompare(right))) {
    const name = packageName(key)
    const identity = `${name}@${metadata.version}`
    if (seen.has(identity)) continue
    seen.add(identity)
    const directory = join(desktopRoot, key)
    const licensePath = await firstLicenseFile(directory)
    const licenseText = licensePath === undefined
      ? 'No standalone license file was present in the installed package; see the SPDX identifier above.'
      : (await readFile(licensePath, 'utf8')).trim()
    sections.push([
      `===== ${identity} =====`,
      `SPDX/license metadata: ${metadata.license ?? 'not declared'}`,
      `Source: ${metadata.resolved ?? 'package-lock.json'}`,
      '',
      licenseText
    ].join('\n'))
  }
  return sections
}

async function pythonLicenseSections() {
  const probe = spawnSync('python', ['-c', [
    'import json, pathlib, sys, PyInstaller',
    'print(json.dumps({',
    '  "pythonVersion": sys.version.split()[0],',
    '  "basePrefix": sys.base_prefix,',
    '  "pyinstallerVersion": PyInstaller.__version__,',
    '  "sitePackages": str(pathlib.Path(PyInstaller.__file__).resolve().parent.parent)',
    '}))'
  ].join('\n')], { encoding: 'utf8', windowsHide: true })
  if (probe.status !== 0 || probe.stdout.trim() === '') {
    throw new Error(`Could not inspect the Python build environment: ${probe.stderr.trim()}`)
  }
  const metadata = JSON.parse(probe.stdout)
  const pythonLicense = join(metadata.basePrefix, 'LICENSE_PYTHON.txt')
  if (!existsSync(pythonLicense)) throw new Error(`Python license is missing: ${pythonLicense}`)

  const siteEntries = await readdir(metadata.sitePackages, { withFileTypes: true })
  const pyInstallerInfo = siteEntries.find((entry) =>
    entry.isDirectory() && entry.name.toLowerCase() === `pyinstaller-${metadata.pyinstallerVersion}.dist-info`
  )
  if (pyInstallerInfo === undefined) throw new Error('PyInstaller distribution metadata is missing')
  const pyInstallerLicense = join(metadata.sitePackages, pyInstallerInfo.name, 'licenses', 'COPYING.txt')
  if (!existsSync(pyInstallerLicense)) throw new Error(`PyInstaller license is missing: ${pyInstallerLicense}`)

  return [
    [
      `===== CPython ${metadata.pythonVersion} =====`,
      'Bundled by the PyInstaller onedir sidecar.',
      '',
      (await readFile(pythonLicense, 'utf8')).trim()
    ].join('\n'),
    [
      `===== PyInstaller ${metadata.pyinstallerVersion} bootloader =====`,
      'Build tool and bundled bootloader license.',
      '',
      (await readFile(pyInstallerLicense, 'utf8')).trim()
    ].join('\n')
  ]
}

async function main() {
  const installer = await readFile(installerPath)
  if (installer.length === 0) throw new Error(`Installer is empty: ${installerPath}`)
  const hash = createHash('sha256').update(installer).digest('hex')
  await writeFile(`${installerPath}.sha256`, `${hash}  Codentum-Setup.exe\r\n`, 'ascii')

  const sections = [
    'Codentum third-party notices',
    'Generated from the locked production dependency graph and the Python sidecar build environment.',
    'Electron also installs LICENSE.electron.txt and LICENSES.chromium.html beside Codentum.exe.',
    '',
    ...await npmLicenseSections(),
    ...await pythonLicenseSections()
  ]
  await writeFile(join(releaseDirectory, 'THIRD_PARTY_LICENSES.txt'), `${sections.join('\n\n')}\n`, 'utf8')

  const packageJson = JSON.parse(await readFile(join(desktopRoot, 'package.json'), 'utf8'))
  const notes = `# Codentum ${packageJson.version} integration preview

Built: ${new Date().toISOString()}
SHA-256: \`${hash}\`

## Included

- Electron desktop shell with real local \`.codentum\` state projection.
- Capability-gated operator commands through the bundled Python JSONL sidecar.
- Worker, module, packet, dependency, cost, role, and integration views backed by local state files and observed events.

## Release gate status

- Desktop tests, renderer screenshot smoke, sidecar self-test, and installer shell cold-start pass.
- The real A/B engine cold-start does not pass because no packageable A/B engine has been supplied.
- This build must not be marked as a production release or uploaded as a complete A/B integration.
- The Windows binaries are not code-signed; SmartScreen may warn during local testing.
`
  await writeFile(join(releaseDirectory, 'RELEASE_NOTES.md'), notes, 'utf8')

  const licenseSize = (await stat(join(releaseDirectory, 'THIRD_PARTY_LICENSES.txt'))).size
  process.stdout.write(`release metadata generated\nSHA-256: ${hash}\nlicense bytes: ${licenseSize}\n`)
}

await main()
