# Contributing to WLED Holiday Lights

[![Validate](https://github.com/drock3/wled-holiday-lights/actions/workflows/validate.yml/badge.svg)](https://github.com/drock3/wled-holiday-lights/actions/workflows/validate.yml)

This guide covers development, validation, repository maintenance, and releases.
For installation and everyday use, see the [README](README.md).

The repository slug is `wled-holiday-lights`; the Home Assistant integration domain
is `wled_holiday_lights`.

## Local validation

Run these commands from the repository root to check linting, formatting, syntax,
and the dependency-free regression suite:

```shell
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m compileall -q custom_components
python -m unittest discover -s tests -v
```

The regression tests use stubbed Home Assistant interfaces. The separate
[Home Assistant tests](tests/ha) use real flows, registries, services, platform
setup, and unload handling, with network sockets disabled and no WLED hardware.
Install their dependencies in a separate Linux environment with a Python version
supported by the target Home Assistant release:

```shell
python -m pip install "homeassistant==2026.1.0" "pytest-homeassistant-custom-component==0.13.305"
python -m pytest tests/ha -v
```

For the latest stable target, install `-r requirements-test.txt` alongside the
current stable Home Assistant version instead of the historical test-plugin pin.
Do not combine that requirements file with the minimum target's plugin pin:
dependency updates can raise its lower bound above the historical version.
CI resolves the stable version automatically and constrains Home Assistant so a
newer test plugin cannot inadvertently select a beta.

Run `actionlint` locally if installed; CI also validates workflows. Automated tests
do not replace a smoke test in a running Home Assistant instance with real WLED
hardware.

## Continuous integration

The [validation workflow](.github/workflows/validate.yml) runs on pull requests,
pushes to `main`, version tags (`v*`), weekly, and on demand. It checks:

- HACS repository requirements and Home Assistant hassfest.
- Ruff linting and formatting, plus actionlint for workflow correctness.
- Python syntax and dependency-free regression tests.
- Real Home Assistant compatibility against the minimum version declared in
  [hacs.json](hacs.json) and the latest stable release from PyPI, using the Python
  runtime required by each release. The minimum target uses the matching
  historical test plugin; the latest target resolves a compatible plugin
  automatically from [requirements-test.txt](requirements-test.txt). The minimum
  target does not install that file, keeping its historical plugin independent of
  development dependency updates.

When raising the minimum supported version, update [hacs.json](hacs.json), the
README requirements, and the minimum target's matching test-plugin pin in the
workflow.

Jobs have timeouts and read-only permissions, except the release job on `main`
and version-tag pushes.
New pushes cancel superseded pull-request runs. Actions are pinned to commit
SHAs; [Dependabot](.github/dependabot.yml) proposes weekly action and development
dependency updates.

## Repository settings

HACS checks GitHub repository settings, so keep the repository **public**, enable
**Issues**, set a short description, and add relevant topics such as
`home-assistant`, `hacs`, `wled`, and `custom-integration`.

In GitHub **Settings > Branches**, protect `main`, require pull requests and
up-to-date branches, and require these status checks after their first run:

- `HACS`
- `Home Assistant hassfest`
- `Code and workflow quality`
- `Python syntax and tests`
- `Resolve Home Assistant versions`
- `Home Assistant (minimum)`
- `Home Assistant (latest)`

Do not require `Publish release`: it intentionally skips pull-request runs. Branch
protection is a GitHub repository setting, not something workflow YAML enables.
When using merge queues, add a `merge_group` trigger before enabling the queue.

For an approval-to-release workflow, also require approving reviews on `main`,
enable **Settings > General > Pull Requests > Allow auto-merge**, and enable
auto-merge on each PR before its final approval. GitHub then merges when the
required reviews and checks are satisfied. Approval alone does not merge a PR.
These settings must be configured on GitHub; the workflow does not change them.

The release job uses the built-in `GITHUB_TOKEN` with `contents: write`; no
personal access token or separate HACS credential is needed. Repository or
organization policies must permit that token to create tags and releases.

## Publishing a release

### What each file and GitHub object does

| Item | Purpose | Change for every release? |
| --- | --- | --- |
| [manifest.json](custom_components/wled_holiday_lights/manifest.json) | Home Assistant integration metadata and installed integration version. Its `version` is the release source of truth. | Yes: bump `version` in the PR. |
| [hacs.json](hacs.json) | HACS display/install settings and minimum supported Home Assistant version. It does not contain the integration release version. | No: only when those settings change. |
| Git tag, e.g. `v1.0.1` | Marks the exact commit containing manifest version `1.0.1`. | Created automatically. |
| GitHub release | Publishes that tag with generated release notes so HACS can discover it. A tag alone is not enough. | Created automatically. |
| HACS update | HACS discovers the GitHub release and offers its integration files to users. There is no separate HACS upload or release object to create. | Discovered automatically, not instantly. |

### Normal release path

1. Update `version` in the integration's
   [manifest](custom_components/wled_holiday_lights/manifest.json) to a stable
   semantic version (`MAJOR.MINOR.PATCH`) newer than the latest release. Use a
   patch bump for fixes, a minor bump for compatible features, and a major bump
   for breaking changes. Include any migration guidance in the PR description.
2. Enable auto-merge on the PR, then review and approve it. GitHub merges it once
   the required checks and approvals pass. Manual merging works too.
3. Validation runs on the merged `main` commit. Only after all checks pass does
   the workflow create the matching tag and publish a stable GitHub release
   with generated notes. No manual tag push or second release PR is required.
4. HACS discovers the release during its normal refresh. Users download the
   update and restart Home Assistant. Edit the GitHub release notes afterward
   if additional guidance is needed.

For example: merge a PR with manifest version `1.0.1` and the workflow publishes
`v1.0.1`. A subsequent PR that leaves the version at `1.0.1` does not publish
another release; its changes wait for the next version bump. Documentation-only
and maintenance PRs normally do not need a bump. Reviewers should check that any
PR intended for immediate distribution includes one.

**Bootstrap:** if no release exists for the current manifest version, the next
successful push to `main` publishes it, including the PR that enables this
workflow. The release tag is `v` followed by the manifest version.

Release jobs are serialized. An already-published stable version is a no-op,
so rerunning validation does not duplicate it. A new version must be newer than
the latest release, and an existing unreleased tag must point to the commit
being released. Draft/prerelease collisions and API failures fail explicitly.
If publication fails, resolve the error and rerun the failed job; do not move a
published tag or reuse a published version.

Manual `v*` tag pushes remain supported and run all checks before publishing;
the tag must match the manifest version. Automatically created tags use
`GITHUB_TOKEN` and do not start another tag-triggered workflow run; the merged
commit has already passed validation in the publishing run.

No release is published directly by approval, a pull-request validation run, a
non-`main` branch push, a weekly run, or a manual validation dispatch. Merges and
other pushes to `main` follow the same release rules, so protect that branch.

### HACS distribution

No custom ZIP asset is needed: HACS installs the integration directly from the
repository. Without GitHub releases, HACS uses the default branch. Users must
restart Home Assistant after downloading an update.

Installing as a custom repository is separate from inclusion in the default
HACS catalog; catalog inclusion requires a separate submission to
[hacs/default](https://github.com/hacs/default).
