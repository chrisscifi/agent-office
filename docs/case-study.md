# Case study: building Agent Office

**Goal:** a system of AI agents that produces genuinely useful digital products with as little of my time as possible, on free infrastructure, on an ordinary laptop, with a dashboard that's fun to watch.

**Constraints:** no budget (free AI tiers first), a Windows laptop behind a VPN, a 6 GB laptop GPU, and a hard rule that a human approves everything that gets sold.

---

## Problems I ran into, and what fixed them

### 1. "Access denied. Please check your network settings."
A free AI provider rejected every request. **Root cause:** two layers. The provider blocks traffic from VPN exit nodes, and its CDN also rejected Python's default `User-Agent` with a 403 that my code reported as "key rejected". **Fix:** VPN split tunnelling for the app's Python, a real `User-Agent` header, and separating 401 (bad key) from 403 (access refused) in error handling so the UI tells the user the actual cause.

### 2. Local model crashing the GPU driver (`0xc0000409`)
Ollama crashed loading a new model. **Fix:** the router detects the crash signature and retries the same request on the CPU (`num_gpu: 0`), remembering the choice, with a thread cap so the laptop stays usable. **Root cause**, found later in Ollama's own log: `CUDA error: device kernel image is invalid` with `driver=12.3`. The model fitted in GPU memory (33/33 layers), but Ollama's CUDA build needed a newer NVIDIA driver than the one installed. Updating the driver fixed it, and the connection test now gives the GPU another chance automatically.

### 3. Free tiers: 429s, 503s and dropped connections
**Fix:** per-*model* cool-downs instead of per-provider (Gemini Flash can be exhausted while Flash-Lite still works), retry with back-off for transient errors (503, dropped TLS connections), honest health reporting in the dashboard, and a "nap and retry" state when every model is resting.

### 4. Finished products silently thrown away
Every Python product failed at the listing step. **Root cause:** listings contained code examples, and my JSON extractor grabbed the first fenced code block, which was the Python example, not the JSON. **Fix:** try every candidate (whole text, every fenced block, outermost braces) and accept the first that parses; a migration rescued the stuck products.

### 5. Windows path limits
A model put a description on the same line as a filename, creating a path longer than Windows' 260-character limit. **Fix:** filename sanitising (strip trailing descriptions, limit each component) with a worst-case length test.

### 6. PDFs: missing glyphs, then nested-tag crashes
The pure-Python PDF library couldn't draw `€` in code blocks and couldn't handle bold text inside table cells. **Fix:** render PDFs with the headless browser already on the machine (Edge/Chrome), a font fallback chain (text → symbol → emoji fonts) and a wait for fonts to load, to avoid "tofu" boxes; strip colour emoji; and keep the pure-Python engine as an automatic fallback. One headless flag (`scriptEnabled=false`) silently stopped PDF output; I found it by bisecting the flags.

### 7. The dashboard "didn't update"
Fixes were deployed but never showed up. **Root cause:** the browser cached the old page, and the 1.5 s refresh re-rendered the DOM, closing dropdowns and wiping inputs. **Fix:** `Cache-Control: no-store` on the app shell, and only re-rendering sections whose HTML actually changed.

### 8. A quality gate that passed everything
The reviewer gave 11 of 12 products 9/10. **Root cause:** the same model was grading its own work with a vague prompt, and it was only shown the first 14k characters (later files looked "empty"). **Fix:** a five-part rubric, a different model for review than for building, fair per-file excerpts with true sizes, and deterministic checks that cap the score.

### 9. A quality gate that let a 3/10 through
After the stricter gate, a product that failed review twice was still passed on "for the owner to decide", so a 3/10 reached the listing step. **Fix:** after the allowed revisions, anything still below 5 is shelved and logged; only borderline 5-6 products reach the owner, marked. Covered by tests.

### 10. Ideas that sounded good but weren't
The Scout was asked for ideas that "provably sell" with only a few Reddit posts as input, so the model invented convincing evidence, and the Master picked with a one-line gut call that favoured the first idea. **Fix:** evidence must cite sources and be labelled found/assumed; a free-alternative check; one job per product; free web search (Tavily) with a daily cap; and a weighted scoring rubric (evidence and buildability count double) over shuffled ideas, informed by the office's memory of review scores per format and the owner's approvals and drops.

### 11. Dynamo tools that failed in Revit 2027
Testing in Revit 2027 surfaced three problems the static checks didn't know about: Dynamo 4 ships only the PythonNet3 engine (graphs declared CPython3), `ElementId.IntegerValue` was removed from the API (use `.Value`), and a generated script passed an argument to `TransactionTaskDone()`. **Fix:** graphs now target PythonNet3, and the Dynamo checker flags all three, plus pyRevit-only code like `__revit__`. Lesson: static checks catch known mistakes; only a real test in the target software finds new ones, which is why a person tests every Dynamo tool before release.

### 12. One job at a time
The reviewer sat idle while the builder worked through a queue. **Fix:** two lanes in separate threads (build / everything else), nearly-finished work prioritised, lane-local status so the animation stays truthful, and errors attributed to the agent that actually failed.

---

## Product decisions (the non-technical part)

- **Code vs no-code.** First the products were Python scripts, which most buyers can't run. I swung to "no code at all", which wrongly excluded Dynamo tools for Revit users and runnable projects for data learners. The final rule: **the format follows the buyer**, and products must be complete, never parts.
- **Skills-first vs demand-first.** Departments started from my CV (BIM, data science, hospitality, acting). Actor products had buyers with little money and many free alternatives; productions and professionals pay for time saved. The office now researches **proven demand first**, with my skills as a bonus.
- **Competing with software the buyer already has.** Operations checklists for hotels sounded useful, but property-management systems already cover most of them. Lesson: check what the buyer's existing tools do before building.
- **Dynamo-led BIM.** Working Dynamo Player tools save BIM coordinators real hours and are hard to make yourself, but the office can't run Revit, so every tool is tested by a person in Revit before release. An early tool that relied on a pyRevit-only variable is the reason that rule exists.
- **What I chose not to automate.** Publishing stays manual (marketplace rules, accountability), AI-designed decorative printables are off by choice, and AI-written code is never executed on the host.

## Results and lessons

- The pipeline reliably produces finished products; quality tracks the building model closely, and the dashboard makes cost per product and quality per model visible.
- **Distribution is the bottleneck, not production.** A marketplace with built-in search (e.g. Etsy) matters more than another product.
- Robustness work (fallbacks, retries, migrations, honest error messages) took more time than the "AI" parts, and made the biggest difference to whether it actually ran unattended.
