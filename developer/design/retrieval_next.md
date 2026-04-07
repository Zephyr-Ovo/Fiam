# Retrieval Architecture — Next Steps

Status: design brainstorm — nothing here is committed.

---

## 1. MMR Integration ✅ DONE

**Problem**: `diversify()` ran as a post-hoc filter after scoring. Result: the top-scored-but-redundant events were first selected, then thinned — wasting slots.

**Fix**: Greedy MMR integrated into `joint.py`:

```
mmr(c) = λ · norm_score(c) − (1−λ) · max_cos(c, selected)
```

- `λ = 0.7` (tuneable via `_MMR_LAMBDA`)
- Scores normalised to [0, 1] before trade-off
- Candidates without embeddings get `max_sim = 0` (always eligible)
- `diversity.py` is now orphan code — can be removed after validation

---

## 2. Scene Identification (Adaptive Weights)

**Goal**: Detect the "type" of a conversation so retrieval weights can shift. A debugging session wants precise technical memory; an emotional check-in wants mood-congruent recall.

### Approach: Lightweight Text Signals

No classifier training. Use cheap heuristics on the first ~500 tokens of the conversation:

| Signal | Detection | Cheap? |
|--------|-----------|--------|
| **Code density** | Ratio of backtick/code-fence blocks to total chars | ✓ |
| **Question density** | Count of `?` per sentence | ✓ |
| **Emotional language** | Arousal of the conversation text via existing classifier | ✓ (already loaded) |
| **Directive language** | Imperative verbs / imperative sentence patterns | ✓ |
| **Language** | `_is_chinese()` already exists | ✓ |

### Scene → Weight Set

```python
SCENE_PROFILES = {
    "technical":  {"semantic": 0.65, "recency": 0.25, "temporal": 0.10, "mmr_lambda": 0.6},
    "emotional":  {"semantic": 0.40, "recency": 0.40, "temporal": 0.20, "mmr_lambda": 0.8},
    "casual":     {"semantic": 0.50, "recency": 0.35, "temporal": 0.15, "mmr_lambda": 0.7},
}
```

- `technical`: heavy semantic — "I saw this exact bug before"
- `emotional`: heavy recency + temporal — "remember how I felt yesterday"
- `casual`: balanced default

### Implementation Sketch

```
src/fiam/retriever/scene.py
  detect_scene(conversation_text: str, classifier) -> str
    → returns "technical" | "emotional" | "casual"

joint.py search():
  scene = detect_scene(conversation_text, classifier)
  w_sem, w_rec, w_link = SCENE_PROFILES[scene]
```

Classifier is optional — if emotion_provider is "api", skip emotional language signal and rely on code_density + question_density only.

### Open Questions

- Do we want more than 3 scenes? (creative, planning, ...)
- Should the profile come from fiam.toml so users can customise?
- Fallback when conversation_text is empty → "casual"

---

## 3. `fiam rem` — Memory Consolidation

**Metaphor**: REM sleep. The brain replays, clusters, and compresses the day's episodic traces into long-term memory.

### What It Does

```
fiam rem [--dry-run] [--window 7d]
```

1. **Load** all events within the consolidation window (default: since last rem, or 7 days)
2. **Cluster** by embedding similarity (agglomerative, threshold ≈ 0.82)
3. **Summarise** each cluster → one "consolidated event" per cluster
   - Text: merged narrative (LLM if available, else longest event)
   - Embedding: centroid of cluster
   - Emotion: weighted average V-A
   - Strength: max(cluster strengths)  — consolidated memories are stronger
   - Time: earliest event in cluster
   - Metadata: `consolidated: true`, `source_events: [...]`
4. **Retire** original events: move to `store/archive/` (not deleted — just out of retriever path)
5. **Update** recall.md with consolidated view

### Clustering Strategy

```python
from scipy.cluster.hierarchy import fcluster, linkage

vecs = np.stack([load_vec(e) for e in events if e.embedding])
Z = linkage(vecs, method='average', metric='cosine')
labels = fcluster(Z, t=0.18, criterion='distance')  # 1 - 0.82 = 0.18
```

Events without embeddings → singleton cluster → kept as-is or summarised individually.

### Summarisation Options

| Mode | How | Quality | Cost |
|------|-----|---------|------|
| **Local** | Pick longest event text, prepend "consolidated from N events" | Low | Free |
| **LLM** | Send cluster texts to narrative_llm, ask for summary | High | API call |

If `narrative_llm_enabled`, use LLM. Otherwise, local mode.

### Lifecycle

```
Day 1-7: events accumulate as raw episodic traces
fiam rem: clusters → consolidated events appear, originals archived
Day 8+: retriever only sees consolidated + recent raw events
```

### Impact on Other Components

- `joint.py`: no change — consolidated events are normal EventRecords with embeddings
- `decay.py`: consolidated events start with high strength → decay slowly
- `store/formats.py`: add `consolidated: bool = False`, `source_events: list[str] = []`
- `store/home.py`: add `archive_events()` method
- `scripts/fiam.py`: new CLI command `fiam rem`

### Open Questions

- Trigger: manual only? Or auto after N sessions / N events?
- `--dry-run` shows what would be clustered without writing
- Should the consolidated event keep all source V-A values for emotion fingerprint (future)?
- Archive format: same JSON, just in `store/archive/events/`?

---

## 4. Two API Entry Points (Clarification)

### Entry Point A: LLM Event Extraction

`emotion_provider = "api"` replaces the **entire** local extraction classifier pipeline with an LLM call. The LLM receives raw turns and produces structured events. Trade-off: embedding similarity is computed on LLM-rewritten text, not raw data → potential quality divergence. User accepts this.

Already implemented via `ApiEmotionClassifier`.

### Entry Point B: LLM Recall Rewriting

Default ON. This is a post-processing plugin:

- Events are **stored as raw data** (principle preserved)
- When writing `recall.md`, the synthesizer optionally calls LLM to rewrite raw fragments into more natural prose
- Off = raw event fragments go directly into the hook
- Maps to existing `narrative_llm_enabled` in synthesizer

**Non-negotiable**: embedding/similarity is always local models. No API embedding.

---

## 5. `fiam.py` Restructuring

Current state: 1263 lines, 12 commands, animation code, hook templates, recall writing, graph generation all in one file.

### Recommended Split

```
scripts/
  fiam.py          ← thin CLI dispatcher (argparse + command routing, ~100 lines)
  fiam_lib/
    __init__.py
    core.py        ← config loading, daemon loop, signal handlers
    session.py     ← cmd_start, cmd_stop, cmd_status, cmd_pre, cmd_post
    storage.py     ← cmd_clean, cmd_reindex, cmd_scan
    init_wizard.py ← cmd_init (231 lines — largest single command)
    export.py      ← cmd_graph, recall writing
    hooks.py       ← hook template generation, injection
    ui.py          ← console, palette, animation frames, progress bars
    commands.py    ← cmd_find_sessions, cmd_rem (future)
```

### Priority

Medium. Not blocking any feature work yet, but each new command (fiam rem, fiam rem --dry-run, future fiam scene) makes it worse. Suggest splitting **before** adding fiam rem.

### Migration Strategy

1. Create `fiam_lib/` package with thin modules
2. Move functions one command at a time, keeping imports working
3. `fiam.py` becomes a ~100-line dispatcher that imports from `fiam_lib.*`
4. No user-facing changes — all CLI commands work identically
