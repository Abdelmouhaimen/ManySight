# Contributing to ManySight

Thank you for improving ManySight. Discuss large contract or architecture changes in an
issue before implementing them.

## Set up the repository

Use Python 3.11+ and Node.js 20+:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-test.txt
npm install --prefix dashboard
npm run build --prefix dashboard
```

On macOS or Linux, activate with `source .venv/bin/activate`.

## Make a change

- Create a focused branch from the current default branch.
- Preserve the `detection` / `measurement` / `state` observation contract and the
  observe-locally, derive-centrally boundary.
- Add or update tests for behavior changes.
- Keep source URLs, credentials, recordings, databases, generated presentations,
  build output, and local logs out of commits.
- Use concise conventional commit messages such as `fix(api): ...` or `docs: ...`.

Run before opening a pull request:

```powershell
python -m pytest -q
npm test --prefix dashboard
npm run build --prefix dashboard
```

The dashboard build is a hard prerequisite for the Python suite: `server/app.py` mounts
`dashboard/dist` at import time.

No formatter, linter, or type-check command is configured. Keep changes consistent with
surrounding Python and React code.

## Bug reports and pull requests

Bug reports should include reproducible steps, expected and actual behavior, ManySight
version or commit, operating system, and relevant sanitized logs. Never post source
credentials or identifiable camera footage in a public issue.

Pull requests should explain the user-visible effect, contract or migration impact,
validation performed, and any known limitations. Keep unrelated refactors separate.

Security vulnerabilities require private handling; see [SECURITY.md](SECURITY.md).

## License

ManySight is licensed under the [Apache License 2.0](LICENSE). Contributions are accepted
under the same license.
