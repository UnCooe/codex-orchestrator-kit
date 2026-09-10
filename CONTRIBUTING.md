# Contributing

This project is an experimental, unofficial Codex runtime harness. Changes must
keep local installation explicit, reversible, and privacy-bounded.

## Requirements

- Python 3.11 or newer
- PowerShell 7 or Windows PowerShell for the Windows smoke test
- Bash for the Unix-like smoke test

Unix-like and PowerShell installers share the same managed-file contract. The Bash
path is validated locally; PowerShell changes must also pass the Windows smoke test.

## Validation

Run the focused checks before opening a pull request:

```powershell
python -m unittest discover -s tests -p "test_*.py"
./tests/smoke-test.ps1
```

```bash
python -m unittest discover -s tests -p 'test_*.py'
bash -n install.sh uninstall.sh tests/smoke-test.sh
bash ./tests/smoke-test.sh
python tests/hygiene_check.py
```

## Installer Changes

- Installers must default to dry-run.
- Writes require explicit `-Apply` or `--apply`.
- Full apply mode must require explicit hook and skills mode values, including an
  explicit `auto` if auto-detection is intended. Skills-only mode requires only an
  explicit skills mode.
- Install and uninstall paths must print the target homes, selected modes, and
  files that will be written, backed up, or removed.
- Tests must install only into temporary homes.

## Privacy Review

Before merging, check that no private runtime data is tracked:

- `.codex/sessions/`
- `.codex/orchestrator-observability/`
- generated reports, triage state, and proposal patches
- real project context
- `.env`, private keys, certificates, or local backup files

Synthetic fixture secrets and paths are allowed only when they are clearly test
data and are used to verify redaction behavior.
