Delete this file after the next session starts and the handoff is no longer needed.

Current state:

- GitHub Actions CI is now wired and passing on `main`.
- The workflow runs the fast suite on `push`, `pull_request`, and manual dispatch:
  - creates `.venv`
  - installs Python dependencies
  - installs `espeak-ng` and `portaudio19-dev`
  - runs `./scripts/test.sh`
- The workflow file is `.github/workflows/ci.yml`.
- The repo is clean locally as of this handoff commit.

CI work completed this session:

1. Added GitHub Actions workflow:
   - `.github/workflows/ci.yml`
2. Fixed CI environment issues discovered on the first runs:
   - missing PortAudio headers for `PyAudio`
   - non-executable `scripts/test.sh` on GitHub runner checkout
3. Fixed a real logic bug exposed by CI:
   - `ContainerManager._verify_docker()` could leave `docker_available=True`
     after a later permission-denied or failure path
4. Verified the final GitHub Actions run passed:
   - workflow run `23967351473`
   - commit `4d486a2`

Important commits from this session:

- `7b991ec` `Add GitHub Actions CI workflow`
- `6d45914` `Install PortAudio headers in CI`
- `5aeb67d` `Ensure CI test runner is executable`
- `4d486a2` `Fix Docker state handling for CI tests`

Architecture assessment from this session:

- The current architecture is fundamentally sound for the project vision so far.
- The separation between bootstrap, orchestrator, tool loop, skill loading, and
  container/native execution is good enough to keep building without redesigning.
- It appears durable for:
  - single-device use
  - one active conversation loop
  - a growing skill catalog
  - voice-installed custom skills
- It is not yet designed for:
  - multi-user concurrency
  - parallel tool execution
  - distributed or hosted deployment

Main architectural caveats noted:

1. Some runtime wiring still relies on post-construction mutation:
   - `orchestrator.container_manager._orchestrator = orchestrator`
   - `orchestrator.container_manager._meta_skill_executor = ...`
2. Native skills are accumulating inside `core/container_manager.py`.
3. Skill routing quality will increasingly depend on prompt discipline and
   skill-definition quality as the catalog grows.

Useful commands next session:

- Local fast suite:
  - `./scripts/test.sh`
- Include scripted voice harness:
  - `./scripts/test.sh --voice`
- Include real install integration:
  - `./scripts/test.sh --install`
- Run all local tests:
  - `./scripts/test.sh --all`
- Check GitHub Actions runs:
  - `gh run list --workflow ci.yml --limit 5`
  - `gh run watch <run-id>`

Good next-session improvements:

1. Harden CI a bit further:
   - pin GitHub Actions to commit SHAs
   - consider locking Python dependencies for CI
2. Reduce startup/install weight in CI if desired:
   - split optional audio-heavy/runtime-heavy deps from the fast test path
3. Clean up dependency wiring:
   - replace post-construction field injection with explicit constructor wiring
4. Consider extracting native skill registration from `container_manager.py`
   if more native skills are added
5. Optionally add a second workflow for heavier checks:
   - manual or scheduled `--voice`
   - manual or scheduled `--all`

Non-blocking note:

- GitHub emitted a deprecation warning that `actions/checkout@v4` and
  `actions/setup-python@v5` are still on Node.js 20. CI is passing now, but
  those actions should be updated when Node 24-compatible releases are available.
