# Releasing jvmind-ce to PyPI

> **Audience**: maintainers releasing new versions. AI assistants should read this before executing a release.

This document describes the **canonical release flow** as of jvmind-ce 0.1.6.
Earlier versions (≤ 0.1.3) used a manual `twine upload` flow with a personal API
token; that is **deprecated** and should no longer be used. All releases now go
through GitHub Actions using PyPI Trusted Publishing (OIDC), so no PyPI token
is required on the maintainer's machine.

---

## Overview

```
┌──────────────────┐   git push origin master    ┌────────────────────────┐
│  Edit code       │ ─────────────────────────▶  │  GitHub: master branch │
│  Bump version    │                             │  (commit N)            │
│  Local tests     │                             └────────────┬───────────┘
└──────────────────┘                                          │
                                                                │ git push origin v0.x.y
                                                                ▼
                                            ┌────────────────────────────────┐
                                            │  GitHub: tag v0.x.y            │
                                            └────────────┬───────────────────┘
                                                         │ triggers
                                                         ▼
                                            ┌────────────────────────────────┐
                                            │ .github/workflows/release.yml  │
                                            │   build job  ── frontend + py  │
                                            │   publish job ── OIDC ── PyPI  │
                                            └────────────┬───────────────────┘
                                                         │
                                                         ▼
                                            ┌────────────────────────────────┐
                                            │ https://pypi.org/project/      │
                                            │       jvmind-ce/0.x.y/         │
                                            └────────────────────────────────┘
```

---

## Prerequisites (one-time)

1. **GitHub SSH key** on your local machine. The repo's `origin` URL is
   `git@github.com:jvmind/jvmind-ce.git`. Verify:

   ```bash
   git remote -v
   # origin  git@github.com:jvmind/jvmind-ce.git (fetch)
   # origin  git@github.com:jvmind/jvmind-ce.git (push)
   ssh -T git@github.com   # should print: "Hi <user>! You've successfully authenticated..."
   ```

2. **PyPI Trusted Publisher** registered. Settings live on PyPI under the
   project's "Publishing" tab:
   `https://pypi.org/manage/projects/jvmind-ce/publishing/`.
   Existing registration:
   - Owner: `jvmind`
   - Repository: `jvmind-ce`
   - Workflow filename: `release.yml`
   - Environment name: *(empty)*
   No GitHub Actions secrets are required.

3. **Local environment** ready. From the repo root:

   ```bash
   source .venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   ```

   Verify:

   ```bash
   python -m pytest _tests --no-cov    # expect: 148 passed
   cd frontend && npm test -- --run && cd ..   # expect: 277 passed
   ```

---

## Release procedure

### Step 1 — Edit code, run tests locally

```bash
# Backend tests
source .venv/bin/activate
python -m pytest _tests --no-cov

# Frontend tests
cd frontend && npm test -- --run && cd ..
```

### Step 2 — Spot-check the frontend build

The CI builds the frontend on Ubuntu; you can pre-build locally to catch
asset/syntax errors:

```bash
cd frontend && npm run build && cd ..
ls -lh frontend/dist/assets/main-*.js   # confirm new hash
```

(The frontend → wheel copy step is done by CI; you don't need to do it
locally unless inspecting the wheel payload.)

### Step 3 — Bump the version

The version lives in `pyproject.toml` only (no `__version__` in source).

```bash
sed -i 's/version = "0.1.6"/version = "0.1.7"/' pyproject.toml
grep '^version' pyproject.toml   # confirm
```

**Versioning policy** (informal — adjust as needed):

| Change | Bump |
|---|---|
| Bug fix, internal refactor, doc update | patch (0.1.6 → 0.1.7) |
| New feature, behavior change | minor (0.1.6 → 0.2.0) |
| Breaking API/config change | minor, plus a `BREAKING:` note in commit message |

### Step 4 — Commit + push

```bash
git add pyproject.toml                # plus any code changes
git commit -m "release: 0.1.7 — <short summary>"
git push origin master
```

If you forgot to commit something, just amend or add a follow-up commit
before tagging.

### Step 5 — Tag + push tag (this triggers CI)

The tag pattern `v*` triggers `.github/workflows/release.yml`.

```bash
git tag v0.1.7
git push origin v0.1.7
```

The push prints something like:

```
To github.com:jvmind/jvmind-ce.git
 * [new tag]         v0.1.7 -> v0.1.7
```

### Step 6 — Watch the workflow

Open the Actions tab:
`https://github.com/jvmind/jvmind-ce/actions/workflows/release.yml`

A new run named `release: 0.1.7 — ...` appears within ~10 seconds.

The `build` job:

1. `actions/checkout@v4`
2. `actions/setup-python@v5` (Python 3.12)
3. `npm ci && npm run build` (frontend)
4. Sync `frontend/dist` → `app/frontend/dist` + CSS copy
5. `python -m build` (sdist + wheel)
6. Upload `dist/` artifact

The `publish` job:

1. Downloads the artifact
2. Runs `pypa/gh-action-pypi-publish@release/v1`
   - This exchanges the GitHub Actions OIDC token for a short-lived PyPI
     API token (no static secret involved).
3. PyPI accepts; build completes with ✅.

Expected runtime: ~2 minutes total.

### Step 7 — Verify on PyPI

```bash
curl -s https://pypi.org/pypi/jvmind-ce/0.1.7/json | \
    python -c "import json,sys; d=json.load(sys.stdin); print('version:', d['info']['version']); print('files:', [u['filename'] for u in d['urls']])"
```

Expected output:

```
version: 0.1.7
files: ['jvmind_ce-0.1.7-py3-none-any.whl', 'jvmind_ce-0.1.7.tar.gz']
```

Or check the project page directly:
`https://pypi.org/project/jvmind-ce/0.1.7/`.

The new version can be installed immediately:

```bash
pip install --upgrade jvmind-ce
```

---

## Troubleshooting

### "400 File already exists" from PyPI

**Cause**: the wheel name was uploaded before (same version + same hash),
typically because you forgot to bump `pyproject.toml` before tagging.

**Fix**:

```bash
# 1. Bump version
sed -i 's/version = "0.1.5"/version = "0.1.6"/' pyproject.toml

# 2. Amend or commit, then re-tag
git add pyproject.toml
git commit --amend --no-edit
git tag -d v0.1.5
git tag v0.1.6
git push origin :refs/tags/v0.1.5
git push origin master v0.1.6
```

The old failed run stays in Actions history as a record; no cleanup needed.

### Tag points at old commit

If you tagged the wrong commit:

```bash
git tag -d v0.1.7
git tag v0.1.7   # re-tag on current HEAD
git push origin :refs/tags/v0.1.7
git push origin v0.1.7
```

### "No such file or directory: app/frontend/dist"

Happens if the sync step runs before any prior build populated
`app/frontend/`. Fix already in CI:

```yaml
- name: Sync frontend dist into wheel payload
  run: |
    mkdir -p app/frontend
    rm -rf app/frontend/dist
    cp -r frontend/dist app/frontend/dist
    mkdir -p app/frontend/dist/src
    cp -r frontend/src/style.css frontend/src/css app/frontend/dist/src/
```

If you copy this into a new workflow, keep the `mkdir -p app/frontend` line.

### Wheel installs but UI is unstyled

The CSS copy was skipped. The wheel payload must include
`app/frontend/dist/src/style.css` and `app/frontend/dist/src/css/*.css`.
Verify locally:

```bash
python -m zipfile -e dist/jvmind_ce-0.1.7-py3-none-any.whl /tmp/wheel-check
ls /tmp/wheel-check/app/frontend/dist/src/style.css
ls /tmp/wheel-check/app/frontend/dist/src/css/ | head
```

If missing, rebuild locally with all four `cp` lines and re-tag.

### CI succeeds but PyPI shows nothing

Wait 30 seconds — PyPI's CDN has eventual consistency. If still missing
after 2 minutes, check the publish job logs for a mint-token error
(OIDC audience mismatch → Trusted Publisher not registered correctly).

### Trusted Publisher registration drift

If you change the workflow filename, repo name, or org, update the
PyPI Trusted Publisher config:

1. `https://pypi.org/manage/projects/jvmind-ce/publishing/`
2. Remove the old entry, add a new one with the new filename.

No code changes to `.github/workflows/release.yml` are needed unless
the filename itself changes.

---

## Why not `twine upload` locally?

- **Security**: a long-lived API token in your shell history / dotenv is a
  leak risk. OIDC tokens are short-lived and scoped.
- **Reproducibility**: CI runs in a clean Ubuntu environment; local builds
  depend on whatever's in your `.venv` and OS.
- **Audit trail**: every release is tied to a GitHub Actions run URL.

If you ever need a hotfix that bypasses CI (e.g., GitHub Actions is down),
you can fall back to a personal API token temporarily, but **rotate the
token immediately afterward**.

---

## Reference

- Trusted Publishing docs: <https://docs.pypi.org/trusted-publishers/>
- `pypa/gh-action-pypi-publish`: <https://github.com/pypa/gh-action-pypi-publish>
- PyPI filename-reuse policy: <https://pypi.org/help/#file-name-reuse>
- Workflow file: `.github/workflows/release.yml`