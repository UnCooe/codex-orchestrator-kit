# Research Notes

This kit stays intentionally small. The goal is to borrow practical orchestration patterns without becoming a full multi-agent platform.

## References

- BMAD-METHOD: https://github.com/bmad-code-org/BMAD-METHOD
- BMAD named agents: https://docs.bmad-method.org/explanation/named-agents/
- BMAD modules: https://docs.bmad-method.org/reference/modules/
- agentsys: https://github.com/agent-sh/agentsys
- agentsys docs: https://agent-sh.github.io/agentsys/
- metaswarm: https://github.com/dsifry/metaswarm
- metaswarm docs: https://dsifry.github.io/metaswarm/

## Ideas to Adopt

- Stable named roles with short, explicit input/output contracts.
- Phase gates: intake, delegation decision, implementation, validation/review, close.
- Layered configuration: base kit defaults, repo overrides, user overrides.
- Slot hygiene: keep a live-agent budget, collect concise output, close completed subagents.
- Quality gates for heavy work: independent test design, review, and destructive critique.
- Compaction discipline: return conclusions, evidence, risks, alternatives, and recommended action instead of raw logs.

## Ideas to Avoid

- Large always-on persona catalogs.
- External issue databases or GitHub workflows as core runtime dependencies.
- Cross-model review as a hard requirement.
- Recursive orchestration by default.
- Marketplace/plugin complexity before the plain portable kit is stable.

## Current Design Choice

Codex Desktop portability is handled as a plain kit first:

- hooks and custom agents install under `~/.codex`
- global skills install under `~/.agents`
- hook commands are rendered from `templates/hooks.json.tpl`
- plugin packaging is deferred until install, uninstall, smoke tests, and documentation are stable
