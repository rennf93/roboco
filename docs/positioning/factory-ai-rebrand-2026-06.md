# RoboCo vs. Factory.ai's "Software Factory" Rebrand

On June 15, 2026, Factory.ai rebranded from "coding agents" to "software factories," describing an end-to-end signals → triage → build/test/review/ secure/ship → monitor loop built on three pillars: model independence, sovereign intelligence, and continual self-improvement.

Source: https://factory.ai/news/software-factory

This note records RoboCo's verified posture against that framing, checked directly against this codebase rather than assumed.

## Verified facts

1. **License / sovereign-intelligence parity.** This repository's `LICENSE` file is GNU AGPLv3 — RoboCo is self-hosted, giving the same sovereign-intelligence parity Factory names as one of its three pillars.
2. **Model-independence parity.** `roboco/llm/providers/`, `roboco/services/llm.py`, and `roboco/models/llm_catalog.py` implement multi-provider LLM routing in this codebase — parity with Factory's model-independence pillar.
3. **Governance differentiator.** RoboCo's own root-task lifecycle requires CEO approval before any root PR reaches master (`escalate_to_ceo` / `awaiting_ceo_approval`) — a human-in-the-loop governance control that Factory's rebrand description does not claim to have.
