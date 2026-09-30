# Design Decisions & Uniqueness Audit

Every idea was checked against one question: *is this what every team will build (the PDF roadmap), or a real improvement?*

| # | Idea | What most teams do | What TapTrace does | Evidence |
| :--- | :--- | :--- | :--- | :--- |
| U1 | Evidence-locked extraction | LLM writes steps, then gets asked not to hallucinate | Steps are extracted from SIIS sentences by deterministic rewrites with span ids. The LLM only selects, groups and names them, so hallucination is impossible by construction. | 0 URL leaks; provenance highlighting in demo; `trace.units[].evidence` |
| U2 | Compiled Settings ontology + polarity | nearest neighbour over `description` | 397 screens with on/off/open/set variants; misleading `message` down-weighted; TV/IoT quarantined; leaf-only exact label, fuzzy with margin, abstention | 37/37 mapping benchmark (Variant A / B / LLM baselines in metrics.md) |
| U3 | Complaint-knowledge Relevance Gate | extract from whatever SIIS arrives | facet-based fit (full / partial / none); partial keeps only generic remedies; escalation-only -> `no_match` | 7/7 non-fitting scenarios decided correctly; gate-off ablation |
| U4 | Contrastive semantic cache | single cosine threshold | per-entry tau from paraphrase and hard-negative clouds, plus a symbolic facet guard (polarity, screen part, component, context) and a SIIS fingerprint | false-hit rate on 22 hand-written hard negatives |
| U5 | Closed-loop verification | `validationDeeplink: null` | verbatim catalog validation probe on every catalog-backed step group; demo emulator runs action -> probe -> check mark | results.jsonl; demo |
| U6 | 10k-scenario reuse | ignored | step-path -> screen memo shared across scenarios; persistent SQLite cache with vectors | `resolver_memo_hits` in `/v1/metrics` |
| U7 | Conditional polarity | picks one toggle | one action = one screen with two stepGroups (condition-specific on/off deeplinks + probes) | line 19 (Touch sensitivity) |
| U8 | Honest evaluation | paraphrases from the same generator | held-out paraphrases from a different model family; gold with written rationale; hard negatives | `eval/` |

## Decisions (with reasons)

* **D1 - Gold fit verdicts.** Lines 7, 9, 11 are `no_match` (TV mirroring, camera flicker, Multi-window docs). Lines 1 and 18 are `partial` (generic remedies only). Line 6 is `no_match` by the same rule. Line 15 is `partial` (charger flicker vs dead-device doc).
* **D2 - Ordering.** auto -> manual checks -> escalation (service centre) -> critical (restart < force restart < safe mode < update < factory reset). The PDF requires critical last.
* **D3 - LLM.** Groq free tier, `openai/gpt-oss-120b` (JSON mode, $0.15 / $0.60 per 1M tokens list price). The adapter is OpenAI-compatible, so Gemini, OpenAI and OpenRouter work unchanged. With no key the engine runs deterministically.
* **D4 - Validation fields are copied verbatim.** offURL entries have no `value` in the catalog; we never invent `"False"`.
* **D5 - Unresolvable conditions** become separate stepGroups instead of a guess.
* **D6 - Deeplink scheme** follows the data (`voiceassist://`), not the PDF example (`bixby://`). Brand naming follows the data (TechCorp / Nexa).
* **D7 - `sample_output.json` is illustrative.** Its 9-11-word descriptions violate PDF 4.1, so we follow the PDF rules and keep the sample's structural conventions (deeplink fields, verbatim validation, step phrasing).
* **LLM pruning is bounded.** The LLM may drop only physical/manual candidates it judges off-topic. Settings chains, critical remedies, grounded backups and the article's escalation step ("contact support if it persists") are protected, and candidates it forgets are kept. The knowledge article, not the model, decides what helps.
* **D9 - The threshold and the guard divide the work.** Hard negatives that the facet guard already rejects do not raise the cosine threshold. A negative inside the paraphrase cloud cannot be separated by any threshold, only symbolically.
* **D10 - Evaluation v2 after error analysis (documented, not hidden).**
  * v1 measured 78.3% correct hits and 22.7% false hits on the dev set.
  * Error analysis showed two causes:
    1. The per-entry cap used the 20th percentile of the LLM's paraphrase cloud, which is much tighter than how real users reword. v2 caps at the least similar LLM paraphrase.
    2. The guard lacked "sharp" symptoms that decide the fix on their own: over-sensitive vs laggy touch, won't charge, won't sync / crash, screen protector, stays on, dim, zoomed in, missing data. It also lacked intent polarity for configuration requests ("remove the circle" vs "turn it on"), a "half screen" Fold part, and the implication that won't start means won't turn *on*.
  * Because the dev set informed these changes, a **fresh test set** (`eval/test_paraphrases.json`: 40 paraphrases + 15 negatives) was written and frozen *before* the changes. It was run once, and its numbers are the headline in metrics.md.
