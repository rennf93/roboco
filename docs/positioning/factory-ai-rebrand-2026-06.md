# RoboCo vs. Factory.ai's "Software Factory" Rebrand

On June 15, 2026, Factory.ai rebranded from "coding agents" to "software factories," describing an end-to-end signals → triage → build/test/review/secure/ship → monitor loop built on three pillars: model independence, sovereign intelligence, and continual self-improvement. The rebrand is backed by named production customers — NVIDIA, EY, Adobe, Palo Alto Networks, Adyen, Blackstone, Wipro, and Comarch — the commercial-traction half of this signal.

Source: [Factory.ai — Software Factory](https://factory.ai/news/software-factory)

**Response decision:** no product change is warranted. RoboCo's shipped architecture already holds parity on the pillars that matter — AGPL self-hosting gives the same sovereign-intelligence parity Factory names as a pillar, and multi-provider LLM routing gives the same model-independence parity — plus a governance differentiator (a CEO-approval gate on every merge to master) that Factory's rebrand description does not claim to have. This note records RoboCo's verified posture against that framing, checked directly against this codebase rather than assumed.

## Verified facts

1. **License / sovereign-intelligence parity.** This repository's `LICENSE` file is GNU AGPLv3 — RoboCo is self-hosted, giving the same sovereign-intelligence parity Factory names as one of its three pillars.
2. **Model-independence parity.** `roboco/llm/providers/`, `roboco/services/llm.py`, and `roboco/models/llm_catalog.py` implement multi-provider LLM routing in this codebase — parity with Factory's model-independence pillar.
3. **Governance differentiator.** RoboCo's own root-task lifecycle requires CEO approval before any root PR reaches master (`escalate_to_ceo` / `awaiting_ceo_approval`) — a human-in-the-loop governance control that Factory's rebrand description does not claim to have.
4. **Continual-self-improvement parity.** `roboco/services/self_heal_engine.py` exists in this checkout and gives RoboCo the same continual-self-improvement capability Factory's third pillar claims: `self_heal_enabled` and `self_heal_originate_enabled` both default to `False` in `roboco/config.py` (lines 879 and 907), so the loop ships dormant — it does not run and does not originate tasks until an operator turns both flags on, at which point it watches this repository's own CI and, on a detected regression, opens a fix task that rides the normal delivery flow (held for CEO approval, never auto-merged).
