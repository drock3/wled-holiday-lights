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
python -m pip install -r requirements-test.txt "homeassistant==2026.1.0" "pytest-homeassistant-custom-component==0.13.305"
python -m pytest tests/ha -v
```

For the latest stable target, replace the Home Assistant version with the current
stable release and omit the test-plugin constraint. CI resolves the stable version
automatically and constrains Home Assistant so a newer test plugin cannot
inadvertently select a beta.

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
  automatically.

When raising the minimum supported version, update [hacs.json](hacs.json), the
README requirements, and the minimum target's matching test-plugin pin in the
workflow.

Jobs have timeouts and read-only permissions, except the tag-only release job.
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

Do not require `Publish release`: it intentionally skips non-tag runs. Branch
protection is a GitHub repository setting, not something workflow YAML enables.
When using merge queues, add a `merge_group` trigger before enabling the queue.

## Publishing a release

1. Update `version` in the integration's
   [manifest](custom_components/wled_holiday_lights/manifest.json) to a stable
   semantic version (`MAJOR.MINOR.PATCH`).
2. Merge the integration and repository files to `main`, with validation passing.
3. Push a matching tag, starting with `v1.0.0` for manifest version `1.0.0`.
   The tag runs all checks again. Only after all checks pass and the tag matches
   the manifest does the workflow publish a GitHub release with generated notes.
   Edit the release notes afterward if additional guidance is needed.

No release is published by a pull request, branch push, weekly run, or manual
validation. A failed or mismatched tag does not publish a release.

### HACS distribution

No custom ZIP asset is needed: HACS installs the integration directly from the
repository. Without GitHub releases, HACS uses the default branch. Users must
restart Home Assistant after downloading an update.

Installing as a custom repository is separate from inclusion in the default
HACS catalog; catalog inclusion requires a separate submission to
[hacs/default](https://github.com/hacs/default).
