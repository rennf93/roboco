# Security Policy

RoboCo is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0). This policy explains which versions receive security fixes and how to report a vulnerability responsibly.

## Supported Versions

RoboCo is pre-1.0 and ships as a Docker image / GitHub release rather than a versioned library. Security fixes are applied to the latest release and the `master` branch only.

| Version            | Supported          |
| ------------------ | ------------------ |
| `master` (latest)  | :white_check_mark: |
| Latest release tag | :white_check_mark: |
| Older releases     | :x:                |

Always run the most recent images — from GHCR (`ghcr.io/rennf93/roboco-*`) or Docker Hub (`renzof93/roboco-*`), tag `latest` — or build from the latest `master`.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report privately through one of:

1. **GitHub Security Advisories (preferred).** Go to the repository's **Security** tab → **Report a vulnerability**, which opens a private advisory visible only to you and the maintainer.
2. **Email.** Contact the maintainer at **rennf93@gmail.com**. Use a subject line beginning with `[RoboCo Security]`.

Please include, where possible:

- A description of the vulnerability and its impact.
- Steps to reproduce, or a proof-of-concept.
- The affected subsystem (api, services, gateway, orchestrator, enforcement, db, agents, mcp, panel) and version / image tag.
- Any suggested remediation.

Particularly relevant to RoboCo's design: issues that could let an agent container exfiltrate a project git token, escape the gateway verb surface, or escalate task-lifecycle permissions are treated as high severity.

## Response Expectations

This is a maintainer-led open-source project, so timelines are best-effort:

- **Acknowledgement:** within 5 business days of your report.
- **Initial assessment:** within 10 business days, confirming whether the issue is accepted and its rough severity.
- **Fix & disclosure:** coordinated with you. We aim to ship a fix and publish an advisory promptly, crediting you unless you prefer to remain anonymous.

Please give us a reasonable window to remediate before any public disclosure. Thank you for helping keep RoboCo and its users safe.
