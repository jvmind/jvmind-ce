# Contributing to JVMind

Thank you for your interest in contributing to JVMind! This document covers how to set up a development environment, run tests, and submit changes.

## Development Setup

### Prerequisites

- Python 3.10 or later
- Node.js 18+ and npm (for frontend development)
- Git

### Clone and install

```bash
git clone https://github.com/jvmind/jvmind-ce.git
cd jvmind-ce

# Backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# Frontend
cd frontend
npm install
cd ..
```

### Configure environment

```bash
cp .env.example .env
# Edit .env to add your OpenAI-compatible API key
```

### Run locally

```bash
# Terminal 1: backend
python server.py

# Terminal 2: frontend (with hot reload)
cd frontend && npm run dev
```

Open <http://localhost:3000> (dev) or <http://localhost:8000> (after `npm run build`).

## Running Tests

```bash
# All tests
python -m pytest _tests --no-cov

# Single file
python -m pytest _tests/test_gc_analyzer.py -v --no-cov

# Frontend tests
cd frontend && npm run test
```

## Code Style

- Read [`CONVENTIONS.md`](./CONVENTIONS.md) for Python and frontend patterns.
- Read [`AGENTS.md`](./AGENTS.md) for architectural notes.
- Backend Python: `black` formatting, `ruff` linting (or follow existing patterns).
- Frontend: vanilla JS ES modules, no framework.

## Submitting Changes

1. Fork the repository.
2. Create a feature branch: `git checkout -b feat/short-description`.
3. Make changes with clear, atomic commits.
4. Add tests for any new functionality.
5. Run the full test suite and ensure it passes.
6. Push to your fork and open a Pull Request against `main`.
7. Fill in the PR template describing the change and motivation.

## Reporting Bugs

Use the GitHub issue tracker with the bug report template. Include:
- JVMind version (`git rev-parse HEAD`)
- Python version
- Operating system
- Steps to reproduce
- Expected vs. actual behavior
- Relevant logs (without API keys or PII)

## Feature Requests

Open an issue with the feature request template. Explain the use case and proposed solution. Large changes should be discussed before implementation.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.