# CLAUDE.md

Working notes for AI agents in this repo (the Python Scout engine/plugin).

## Fixtures must be anonymized — this repo and its two siblings are public

Scout runs against a real person's vault, so anything lifted from it into a
fixture or an inline test string **must be scrubbed before it lands**. All three
Scout repos — this one, `Scout` (desktop), and `scout-iOS-app` — are public.

- **No real identifiers.** Strip company/product names, real coworker names, real
  Linear IDs, GitHub repos, and Slack workspaces/channels. Use the shared
  stand-ins so fixtures stay internally consistent:
  - People: `Alex` / `Priya` / `Sam`; comment author `alex`.
  - Linear: `PROJ-1234` (neutral prefixes like `OPS-`, `DESK-` for variety) —
    never the real team prefixes (`AI-`, `KAI-`, `ST-`, …).
  - GitHub: `example-org/<repo>`.
  - Slack: `acme-co.slack.com/archives/C0123456789/p1700000000000000`.
  - Vendors/products: a generic noun, not the brand.
- **Anonymize content, not structure.** Keep the tokens the parser is tested on
  (synthetic `[#TAG]` prefixes, `**bold**`, `_(italic)_`, `[[wikilinks]]`,
  ` — ` separators, `` `code` ``). Only swap the words around them.
- **Don't hardcode personal config in source.** The Linear deep-link workspace is
  read from `SCOUT_LINEAR_WORKSPACE` (default `your-workspace`), not baked in;
  keep new connector/workspace specifics config- or env-driven the same way.
- **Preserve legitimate attribution** — NOT leaks, leave them: `pyproject` authors,
  `.claude-plugin/marketplace.json` / `plugin.json` owner, `LICENSE`, and the
  project's own `github.com/<org>/…` URLs (including the self-update URL).

### `parser-corpus.json` is ONE byte-identical file living in three repos

`engine/tests/fixtures/contract/parser-corpus.json` is the **canonical** copy;
`Scout` (desktop) and `scout-iOS-app` vendor byte-identical copies, checksum-guarded
on both the Python and Swift sides — so you cannot edit just one copy. On any change
(anonymizing counts):

1. Edit the corpus here; keep every `expected` field consistent with the parser
   (`pytest tests/unit/test_parser_contract.py` is the judge).
2. Copy it byte-for-byte into the sibling checkouts (cloned alongside this repo):
   - `../Scout/ScoutTests/Fixtures/parser-corpus.json`
   - `../scout-ios/ScoutMobileTests/Fixtures/parser-corpus.json`
3. Update BOTH checksum guards to the new `shasum -a 256` of the file:
   - `EXPECTED_SHA256` in `engine/tests/unit/test_parser_corpus_checksum.py`
   - `canonicalSHA256` in `../Scout/ScoutTests/ActionItems/ParserContractTests.swift`
4. Verify all three: `pytest tests/unit/test_parser_contract.py
   tests/unit/test_parser_corpus_checksum.py`, plus each Swift repo's
   `ParserContractTests`.
