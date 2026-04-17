# Thon — Agentic Offensive Security Operator

Thon is a doctrine-driven agentic system architecture for autonomous offensive security operations. It defines a complete framework for AI-assisted penetration testing, vulnerability research, and red-team engagements — built on explicit authority chains, ethical constraints, and operational doctrine.

This is the architecture and doctrine only. Engagement data, target-specific artifacts, and operational outputs are excluded.

## Architecture

The system is built on three pillars:

### 1. Doctrine (`doctrine/`)

A 14-layer operational taxonomy organized across two axes:

**Vertical Authority Chain (Layers 01–05) — "Why" and "Who"**
- Sovereignty, moral order, legal legitimacy, command authority, organizational hierarchy
- Defines *who* authorizes operations, *under what law*, and *with what constraints*

**Horizontal Operational Chain (Layers 06–12) — "How"**
- Agent identity, inviolable principles, strategy, operational art, tactics, techniques, procedures
- Defines *how* the agent thinks, decides, and acts — mapped to MITRE ATT&CK where applicable

Layer 05/06 is the **hinge point** where authority becomes behavior.

### 2. Agent Architecture (`agents/`)

YAML-defined runtime components that implement the doctrine:

| Component | File | Purpose |
|-----------|------|---------|
| **Thon Core** | `agents/thon/thon.yaml` | Agent identity, symbiosis model, analytical lenses |
| **Cognitive Engine** | `agents/thon/cognitive_engine.yaml` | Six-stage loop: Perceive → Attend → Hypothesize → Act → Evaluate → Remember |
| **Supervisor (ARBITER)** | `agents/engine/supervisor.yaml` | Mission governance, scope enforcement, lens selection |
| **Doctrine Registry** | `agents/doctrine/registry.yaml` | Formal binding between doctrine layers and agent components |
| **Shared Memory** | `agents/schemas/shared_memory.yaml` | Semantic, episodic, and procedural memory schema |
| **Lenses** | `agents/thon/lenses.yaml` | Five analytical perspectives (see below) |
| **Engagement** | `agents/thon/engagement.yaml` | Engagement lifecycle management |
| **Autonomy** | `agents/thon/autonomy.yaml` | Autonomy tier definitions and escalation rules |

### 3. Tasking Orders (`tasking-orders/`)

A tiered mission authorization framework:

| Tier | Autonomy Level | Example |
|------|---------------|---------|
| Tier 1 | Full autonomy | Passive OSINT, network discovery |
| Tier 2 | Guided autonomy | Vulnerability assessment, credential audit |
| Tier 3 | Supervised autonomy | Active exploitation, lateral movement |
| Tier 4 | Direct command | Scope-edge operations, high-impact actions |
| Tier 5 | Classified | Agent-vs-agent, active defense |
| Tier 6 | God layer | Sovereignty invocation |

## Key Concepts

### Eddie/Thon Symbiosis
- **Eddie** (Human): moral authority, mission direction, legitimacy source
- **Thon** (Agent): technical capability, cognitive processing, tool operation
- Neither is complete alone. Eddie decides WHAT. Thon decides HOW.

### Five Analytical Lenses

Not restrictions — perspectives. The agent can view any problem through any lens:

| Lens | Codename | Governing Question |
|------|----------|--------------------|
| Discovery | DRIFTER | What unknown behavior exists? |
| Offense | VANGUARD | How would this be exploited? |
| Defense | BASTION | Can we detect and survive this? |
| Adversary | SHADOW | What would a real adversary do? |
| Governance | WARDEN | Is this authorized and ethical? |

### Skills (`skills/`)

Modular agent capabilities that extend Thon's operational toolkit:

- **skeleton_key** — Web application token extraction and API reverse-engineering
- **pineapple_recon** — Automated wireless reconnaissance via Hak5 WiFi Pineapple Mark 7

### Hardware Integration (`hak5/`)

Engagement templates for Hak5 hardware arsenal integration. The template structure (`hak5/_templates/`) defines a standardized engagement lifecycle: scope, recon, captures, evidence, logs, and reporting.

## Reading Orders

- **Sequential**: 00 → 01 → 02 → ... → 13 (full understanding)
- **Authority First**: 01–05 → 06–12 → 13 (understand legitimacy before operations)
- **Operations First**: 06–12 → 01–05 → 13 (understand behavior before authority)
- **Agent Onboarding**: 00 → 06 → 07 → 07c → 13 → 01 → 04 (minimum viable context)

## What This Is

This is a research project exploring how to build doctrine-governed autonomous agents for offensive security. The architecture enforces:

- **Explicit authority chains** — every action traces back to human authorization
- **Legal constraint awareness** — CFAA, CMA, GDPR, and rules of engagement are architectural, not aspirational
- **Ethical boundaries as code** — the moral order is embedded in the agent's decision framework, not bolted on
- **Scope enforcement** — the ARBITER supervisor validates every operation against authorized scope before execution
- **Tiered autonomy** — the agent's freedom scales with the risk level of the operation

## What This Is Not

- This is not a turnkey hacking tool
- This does not contain exploit code, target data, or engagement outputs
- This does not bypass any security controls — it defines the framework within which an AI agent operates responsibly

## License

This project is provided for research and educational purposes. The doctrine, architecture, and operational frameworks are original work.
