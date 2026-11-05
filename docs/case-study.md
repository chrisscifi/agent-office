# Case study: building Agent Office

**Goal:** a system of AI agents that produces genuinely useful digital products with as little of my time as possible, on free infrastructure, on an ordinary laptop, with a dashboard that's fun to watch.

**Constraints:** no budget (free AI tiers first), a Windows laptop behind a VPN, a 6 GB laptop GPU, and a hard rule that a human approves everything that gets sold.

---

## Problems I ran into, and what fixed them

### 1. "Access denied. Please check your network settings."
A free AI provider rejected every request. **Root cause:** two layers. The provider blocks traffic from VPN exit nodes, and its CDN also rejected Python's default `User-Agent` with a 403 that my code reported as "key rejected". **Fix:** VPN split tunnelling for the app's Python, a real `User-Agent` header, and separating 401 (bad key) from 403 (access refused) in error handling so the UI tells the user the actual cause.

### 2. Local model crashing the GPU driver (`0xc0000409`)
Ollama crashed loading a new model on an older NVIDIA driver. **Fix:** the router detects the crash signature and retries the same request on the CPU (`num_gpu: 0`), remembering the choice. Slower, but the office keeps working; updating the driver restores GPU speed.

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

### 9. One job at a time
The reviewer sat idle while the builder worked through a queue. **Fix:** two lanes in separate threads (build / everything else), nearly-finished work prioritised, lane-local status so the animation stays truthful, and errors attributed to the agent that actually failed.

---

## Product decisions (the non-technical part)

- **Code vs no-code.** First the products were Python scripts, which most buyers can't run. I swung to "no code at all", which wrongly excluded Dynamo tools for Revit users and runnable projects for data learners. The final rule: **the format follows the buyer**, and products must be complete, never parts.
- **Skills-first vs demand-first.** Departments started from my CV (BIM, data science, hospitality, acting). Actor products had buyers with little money and many free alternatives; productions and professionals pay for time saved. The office now researches **proven demand first**, with my skills as a bonus.
- **What I chose not to automate.** Publishing stays manual (marketplace rules, accountability), AI-designed decorative printables are off by choice, and AI-written code is never executed on the host.

## Results and lessons

- The pipeline reliably produces finished products; quality tracks the building model closely, and the dashboard makes cost per product and quality per model visible.
- **Distribution is the bottleneck, not production.** A marketplace with built-in search (e.g. Etsy) matters more than another product.
- Robustness work (fallbacks, retries, migrations, honest error messages) took more time than the "AI" parts, and made the biggest difference to whether it actually ran unattended.
