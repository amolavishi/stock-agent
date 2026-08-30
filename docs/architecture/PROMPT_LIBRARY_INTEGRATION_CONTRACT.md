# Prompt Library v2.2 Integration Contract

The final package ZIP was unpacked and its manifest, frontmatter, schema
registry, runtime contracts and validation files were inspected. Its own
architecture explicitly states that SQLite, WorkItem lease, Python Gate and
authoritative FinalAction are outside the Prompt Library.

## Runtime lifecycle

```text
WorkItem + typed input bundle
 -> Prompt Registry/Resolver
 -> composition (exactly one leaf output owner)
 -> ContextManifest (required/included/content/token hashes)
 -> ModelRouter/LLMProvider
 -> strict JSON extraction
 -> canonical JSON Schema validation
 -> one deterministic syntax repair + one structured repair call
 -> Python semantic validation
 -> dependency/lease freshness fence
 -> advisory result persisted
```

Every PromptExecution stores run/work IDs, prompt ID/version/content hash,
schema, rule-set hash, context hash, evidence IDs, dependency hash, provider,
model, attempt, response hash and cost lineage. A MIXIN such as
`system.analysis_grounding` is not an output owner.

## Authority rules

Prompt results may propose `DiscoveryDecision`, research dimensions, failure
paths, audit issues and a final recommendation. They cannot write WorkItem
state, GateResult, price, StageGate eligibility, position size or FinalAction.
Final Synthesis is advisory; FinalAllocationGate is Python-only.

## Required prompt inputs

The compiler resolves frontmatter `required_inputs`, allowed run modes,
`compose_with`, required rule packs and schema IDs. Missing input is a contract
error, not an empty/default context. Context content and token estimates are
hashed and persisted; the model is never allowed to invent authoritative
market/SEC facts.

## Integration gap

Current `stock_agent/prompt_runtime.py` validates schemas and composition, and
`runtime.py` invokes it through WorkItems. It does not yet persist the full
PromptExecution/ModelCall lineage or consume real normalized Market/SEC
bundles. This is a PATCH after provider/evidence contracts are introduced.
