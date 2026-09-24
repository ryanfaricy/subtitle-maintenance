# Contributing

Use Python 3.10+ on macOS or Linux. Windows is not currently supported because
run locks and process-group handling use POSIX APIs. Core tests need no media,
MLX model, Docker daemon, or provider account.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
python -m unittest discover -s subtitle_maintenance/tests -v
python -m build
```

Run `ruff format .` and `ruff check . --fix` when editing. Review automated fixes.
Keep commits focused and use Conventional Commits (`fix:`, `feat:`, `docs:`,
`refactor:`, `test:`). Describe the problem, resulting behavior, regression test,
and migration considerations in a pull request.

## Testing a change

- Add a test that would fail before the fix. Use temporary directories and fake
  provider responses. Never run tests against a real library or account.
- Test both preview and apply for changes to mutation behavior. Apply fixtures
  must be temporary files owned by the test.
- Preserve backup receipts, rollback behavior, cache invalidation and verification
  gates. A passing container check does not prove subtitle timing or correctness.
- Changes to native MLX, OCR or real provider interoperability need a separately
  authorized integration pilot. Unit tests do not establish those integrations.
- Keep imports inert. CLI parsing, credential reads, processes and network calls
  belong inside invoked functions, not module initialization.

See [architecture](docs/architecture.md), [the design walkthrough](docs/design-walkthrough.md),
and [security](SECURITY.md). Contributions are under the MIT license.
