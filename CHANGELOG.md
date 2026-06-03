# Changelog

All notable changes to RoboCo are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-03

### Added

- Initial public release of **RoboCo** — an open-source AI agent "company": a
  virtual organization of 18 AI agents and 1 human CEO that plans, builds,
  reviews, documents, and ships software.
- Organizational hierarchy: Board (Product Owner, Head of Marketing, Auditor),
  Main PM, and Backend / Frontend / UX-UI cells.
- Agent gateway (`roboco-flow`, `roboco-do`) backed by the server-side
  Choreographer; intent-verb tool surface per role.
- Task lifecycle state machine with role-based transitions and git workflow
  (PR-before-QA, CEO approval for major work).
- A2A protocol, journals, channels/notifications, kanban, and RAG (piragi +
  pgvector) knowledge base.
- Next.js control panel (`panel/`) behind a single nginx entry point.
- Multi-agent workspace management with per-project encrypted git tokens.

[Unreleased]: https://github.com/rennf93/roboco/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rennf93/roboco/releases/tag/v0.1.0
