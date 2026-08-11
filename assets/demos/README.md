# Demo GIF Recording Guide

This directory contains animated demonstrations of ATLAS in action. Each GIF should be **< 2MB** and **60fps** for smooth playback.

## Recording Instructions

### Tool: Asciinema + agg

```bash
# Install tools
brew install asciinema
cargo install --git https://github.com/asciinema/agg

# Record a session
asciinema rec demo.cast

# Convert to GIF (optimized)
agg --speed 1.5 --fps-cap 60 demo.cast demo.gif
```

## Required Demos

### 1. `task-execution.gif` (Target: ~10s, <2MB)
**Shows:** Full OTAR loop with live log streaming

**Commands to record:**
```bash
uv run atlas run "research the latest papers on transformers and save a summary"
```

**What to capture:**
- Task submission
- Memory retrieval (context loaded)
- Plan generation
- OTAR loop iterations (Observe → Think → Act → Reflect)
- Final result saved

---

### 2. `safety-intercept.gif` (Target: ~8s, <2MB)
**Shows:** Dangerous command → approval flow → confirmation code

**Commands to record:**
```bash
uv run atlas run "delete all files in /tmp"
```

**What to capture:**
- Safety classifier detecting Tier 3 (DANGEROUS)
- Approval request with reason
- 4-digit confirmation code prompt
- User approval
- Action execution with audit log

---

### 3. `multi-agent-dag.gif` (Target: ~12s, <2MB)
**Shows:** Complex task decomposition → parallel execution

**Commands to record:**
```bash
uv run atlas run "research quantum computing, write a blog post, and create a code example"
```

**What to capture:**
- Supervisor decomposing task
- DAG visualization (if available via CLI)
- Multiple agents running in parallel
  - 🔬 Researcher
  - ✍️ Writer  
  - 💻 Coder
- Results synthesis

---

### 4. `memory-retrieval.gif` (Target: ~7s, <2MB)
**Shows:** Memory influencing a decision

**Commands to record:**
```bash
# First, create a memory
uv run atlas run "I prefer Python over JavaScript"

# Then, query something ambiguous
uv run atlas run "write a script to fetch data from an API"
```

**What to capture:**
- Memory search triggered
- Retrieved context: "user prefers Python"
- Decision made based on memory (script in Python, not JS)

---

## Optimization Tips

1. **Reduce terminal size:** `export COLUMNS=100 LINES=30` before recording
2. **Speed up:** Use `--speed 1.5` in agg to compress time
3. **Trim idle time:** Edit `.cast` file to remove long pauses
4. **Compress:** Use `gifsicle -O3 --lossy=80 input.gif -o output.gif`

## Placeholder

Until GIFs are recorded, we use placeholder SVG animations in the README.
