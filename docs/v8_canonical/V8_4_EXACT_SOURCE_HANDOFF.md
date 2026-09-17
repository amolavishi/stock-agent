# V8.4 Exact Source Handoff

The canonical MAIN Discovery source package is vendored under `prompts/v8_4/`.

Source identity is owned by `docs/v8_canonical/V8_4_DISCOVERY_SOURCE_MANIFEST.json` and is verified as SHA-256 over raw bytes with no newline normalization. The package contains:

- `DISCOVERY_COMMON_CONTRACT.md`
- `CANONICAL_US_UNIVERSE_RULES.md`
- scanner profiles `02` through `14`

`.gitattributes` marks `prompts/v8_4/*.md` as `-text` so checkout filters cannot change the locked bytes.

The production source-fidelity and source-gate layers must report every core source and scanner as `PASS` before MAIN Discovery can execute. Missing, byte-count-mismatched, hash-mismatched, or undecodable input must remain `NOT_EVALUABLE_INPUT_INTEGRITY`; no paraphrase, reconstruction, or model-generated substitute is allowed.
