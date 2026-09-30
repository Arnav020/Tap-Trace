# 5-minute demo video script (limit: 5:00)

Record the screen at 1080p. Before recording, start the stack with `docker compose up --build` (or `uvicorn taptrace.api:app`) and open http://localhost:8000.

| Time | Screen | Say (short) | Criterion hit |
| :--- | :--- | :--- | :--- |
| 0:00-0:20 | Title slide | "TapTrace, Theme 02. It turns a vague device complaint into a grounded, one-tap, verified fix plan. Arnav Joshi, Thapar Institute." | Presentation |
| 0:20-0:55 | Slide 3 (gaps) | "The data is adversarial. Catalog labels mislead, on/off twins look identical, 21 TV and appliance screens are mixed in, and 4 of the 20 knowledge articles are the wrong document. We built around those facts." | Innovation |
| 0:55-1:30 | Slide 4 (architecture) | "Fast path: a contrastive cache with no LLM. Cold path: relevance gate, evidence-locked steps, and two LLM stages that can select and name but can never write a step. Then an exact screen resolver and a hard output gate using the organisers' own schema." | Technical depth |
| 1:30-2:30 | Demo, line 19 (touch lag) | Tick "bypass cache" and press Troubleshoot. Point at: the ~2-3 s cold latency and cost chip; highlighted evidence sentences; the Touch-sensitivity action with two options (ON with a screen protector, OFF without). Tap One-tap, and the validation probe turns into a check mark. Point out that critical steps come last and backup comes before reset. | Working prototype |
| 2:30-3:05 | Demo, "Try paraphrase" | "An unseen wording with no SIIS text, served in milliseconds from cache. Similarity is above this entry's own threshold." | Fast path / latency |
| 3:05-3:30 | Demo, "Try hard negative" | "'Screen won't turn OFF' looks almost identical to the embedder, but the facet guard refuses the cached plan instead of giving the wrong fix instantly." | Innovation |
| 3:30-3:55 | Demo, line 7 (small screen + TV article) | "Wrong document: we return contexts: [] with no_match, which is what the rules require, instead of TV-mirroring steps." | Theme relevance |
| 3:55-4:35 | metrics.md / slide 8 / slide 10 | Read the headline numbers: schema, leaks, step accuracy, 37/37 deeplinks versus the baselines, cache correct-hit and false-hit rates, cold P95. | Results |
| 4:35-5:00 | Slide 9 | "Next: learn from the validation probes, an on-device variant, live SIIS retrieval, and multilingual complaints. Everything is reproducible with one docker command. Thank you." | Worklet potential |

Tips: keep the terminal hidden and zoom the browser to 110%. Pre-run line 19 once so the demo shows a warm process, then use "Troubleshoot" with SIIS for the cold path.
