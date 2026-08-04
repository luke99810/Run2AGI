# Electron ↔ Python P1 transport probe

This directory contains the smallest useful validation of ADR-0003's sidecar
transport decision. It proves that an Electron main process can start Python
3.11+ and exchange request/response envelopes over JSON Lines on stdio.

## Pieces

- `python_engine_probe.py`: dependency-free Python development probe.
- `packages/desktop/shell/main/python-engine/PythonEngineClient.ts`: Electron
  main-process client with Python 3.11+ discovery, per-request timeouts, bounded
  stderr capture, structured remote errors, and process cleanup.
- `PythonEngineClient.test.ts`: runs both a Node integration check and a real
  Electron main-process check. The probe is copied under a path containing
  spaces so Windows argument handling is exercised without shell quoting.
- `packages/desktop/scripts/python-package-smoke.cjs`: builds the probe as a
  PyInstaller one-file sidecar in an isolated temporary environment, then
  launches that executable from Electron with Python removed from `PATH`.

The normal desktop startup does not launch this development probe yet. The
recommended integration point is `packages/desktop/shell/main/index.ts` after
`app.whenReady()`. Keep one client for the application lifetime and call
`client.close()` before quitting. That wiring should be added when the real
control-plane entrypoint and its lifecycle policy are agreed.

## Run

From `packages/desktop`:

```powershell
npm run test:python-engine
npm run test:python-package
npm run typecheck
npm run build
npm run verify
```

The Electron test prints one machine-readable line beginning with
`CODENTUM_ELECTRON_PYTHON_PROBE=`. A passing record includes the Electron,
Node, and Python versions plus timeout, stderr, clean-exit, and spaced-path
results.

## Scope

The package smoke test proves the probe can be built as a PyInstaller one-file
executable and launched by Electron without a Python interpreter on `PATH`.
It does **not** prove that the real engine and its third-party dependencies have
been bundled into an Electron installer or cold-started on a clean machine.
Those remain delivery tasks.
