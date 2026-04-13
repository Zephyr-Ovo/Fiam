"""
fiam pipeline — orchestrates pre_session and post_session flows.

pre_session:
  retriever.joint.search() → synthesizer.stance.generate() → injector.write()

post_session:
  classifier.analyze_event() → extractor.event.segment() →
  [for each event: embedder.embed_and_save(), store.write()] →
  report.generate() → HOLD detection → WAKE extraction
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

from fiam.config import FiamConfig
from fiam.classifier.emotion import get_classifier
from fiam.extractor import event as event_extractor
from fiam.extractor.signals import extract_session_signals
from fiam.injector import claude_code as injector
from fiam.injector.home_diff import detect_uncommitted
from fiam.logging.trace import Trace
from fiam.logging import report
from fiam.retriever import joint as joint_retriever
from fiam.retriever.embedder import Embedder
from fiam.retriever.temporal import link_new_events
from fiam.retriever.semantic_link import link_semantic
from fiam.store.formats import EventRecord
from fiam.personality.reader import read_personality
from fiam.store.home import HomeStore
from fiam.synthesizer.stance import StanceSynthesizer


def pre_session(config: FiamConfig) -> dict[str, Any]:
    """Run the pre-session pipeline: retrieve → synthesize → inject."""
    trace = Trace(logs_root=config.logs_dir)
    store = HomeStore(config)
    synth = StanceSynthesizer(config)

    # Step 1: Retrieve relevant events
    with trace.step("retriever_joint", inputs={"mode": "pre_session"}) as rec:
        events = joint_retriever.search("", store, config)
        rec["outputs"] = {"event_count": len(events)}

    if config.debug_mode:
        print(f"[pre_session] Retrieved {len(events)} events from home")

    # Step 2: Read personality (AI's self-description)
    personality = read_personality(config)
    if config.debug_mode:
        if personality:
            print(f"[pre_session] Personality: {personality[:80]}...")
        else:
            print("[pre_session] No personality.md yet (Day 1)")

    # Step 3: Synthesize background
    with trace.step("synthesizer", inputs={"event_count": len(events)}) as rec:
        synthesis_text = synth.generate(
            retrieved_events=events,
            personality=personality,
            session_id=trace.session_id,
        )
        rec["outputs"] = {"synthesis_text": synthesis_text}

    if config.debug_mode:
        print(f"[pre_session] Synthesis: {synthesis_text}")

    # Step 3: Detect uncommitted home changes (user's actions)
    with trace.step("home_diff", inputs={}) as rec:
        diff_text = detect_uncommitted(config)
        rec["outputs"] = {"diff_text": diff_text}

    if diff_text:
        synthesis_text = synthesis_text.rstrip() + "\n\n---\n" + diff_text
        if config.debug_mode:
            print(f"[pre_session] Home diff: {diff_text}")

    # Step 3b: Build awareness context (time, inbox, schedule, env map)
    try:
        import sys
        sys.path.insert(0, str(config.code_path / "scripts"))
        from fiam_lib.awareness import build_awareness
        awareness_text = build_awareness(config)
        synthesis_text = awareness_text + "\n\n---\n\n" + synthesis_text
        if config.debug_mode:
            print(f"[pre_session] Awareness injected")
    except Exception as e:
        if config.debug_mode:
            print(f"[pre_session] Awareness build failed: {e}")

    # Step 4: Inject into home
    with trace.step("injector", inputs={"synthesis_length": len(synthesis_text)}) as rec:
        inject_result = injector.write(config, synthesis_text)
        rec["outputs"] = inject_result

    if config.debug_mode:
        print(f"[pre_session] Injected: {inject_result}")

    return {
        "session_id": trace.session_id,
        "event_count": len(events),
        "synthesis_text": synthesis_text,
        "inject_result": inject_result,
    }


def post_session(
    config: FiamConfig,
    conversation: list[dict[str, str]],
    session_id: str | None = None,
    session_time: datetime | None = None,
) -> dict[str, Any]:
    """Run the post-session pipeline: classify → extract → embed → store → report.

    If *session_time* is given, events are timestamped to that moment
    (e.g. JSONL file mtime during scan) instead of ``now()``.
    """
    trace = Trace(logs_root=config.logs_dir, session_id=session_id)
    store = HomeStore(config)
    classifier = get_classifier(config)
    embedder = Embedder(config)

    # Build full text for debug save
    full_text = "\n\n".join(
        f"[{t.get('role', 'unknown')}]\n{t['text']}" for t in conversation
    )

    if config.debug_mode:
        # Save raw conversation
        conv_path = trace.session_dir / "conversation.txt"
        conv_path.write_text(full_text, encoding="utf-8")
        print(f"[post_session] Saved conversation to {conv_path}")

    # Step 0: Pre-classify ALL turns once — shared by extractor + signals
    #
    # This is the biggest single optimisation: batch classification is
    # 30-100× faster than per-turn, and we avoid classifying the same
    # turns twice (once in segment(), once in extract_session_signals()).
    user_texts = [t["text"] for t in conversation if t.get("role") == "user"]
    asst_texts = [t["text"] for t in conversation if t.get("role") == "assistant"]
    all_classify_texts = user_texts + asst_texts

    if all_classify_texts:
        all_emotions = classifier.analyze_batch(all_classify_texts)
        user_emotions = all_emotions[:len(user_texts)]
        asst_emotions = all_emotions[len(user_texts):]
    else:
        user_emotions = []
        asst_emotions = []

    # Build arousal cache for signals (avoids second classification pass)
    precomputed_arousals = {
        "user": [e.arousal for e in user_emotions],
        "asst": [e.arousal for e in asst_emotions],
    }

    # Step 1: Extract events (classifier runs inside extractor per-turn)
    # Pre-load stored vectors for novelty check — as a single stack operation
    stored_vecs: list[np.ndarray] = []
    emb_dir = config.embeddings_dir
    if emb_dir.is_dir():
        npy_files = sorted(emb_dir.glob("*.npy"))
        if npy_files:
            # Memory-map + filter in one pass — avoids loading all into RAM
            for npy in npy_files:
                vec = np.load(npy)
                if vec.shape[0] == config.embedding_dim:
                    stored_vecs.append(vec)

    with trace.step("extractor", inputs={"turn_count": len(conversation)}) as rec:
        extracted = event_extractor.segment(
            conversation, classifier,
            arousal_threshold=config.arousal_threshold,
            embedder=embedder,
            stored_vecs=stored_vecs,
            precomputed_user_emotions=user_emotions,
            precomputed_asst_emotions=asst_emotions,
            debug=config.debug_mode,
        )
        rec["outputs"] = {"event_count": len(extracted)}

    if config.debug_mode:
        if extracted:
            print(f"[post_session] Extracted {len(extracted)} events")
            for i, ev in enumerate(extracted):
                hint = f" ({ev.topic_hint})" if ev.topic_hint else ""
                merged = f" (merged {ev.pair_count} pairs)" if ev.pair_count > 1 else ""
                sig = ""
                if ev.significance:
                    s = ev.significance
                    sig = f" [emo={s.emotional:.2f} nov={s.novelty:.2f} elab={s.elaboration:.2f}]"
                print(f"  event {i+1}: v={ev.emotion.valence:.2f} a={ev.emotion.arousal:.2f}"
                      f"{sig}{hint}{merged}")
        else:
            print("[post_session] 0 events extracted (no significant emotional moments)")

    # Collect overall emotion results for the report
    emotion_results = [
        {
            "valence": ev.emotion.valence,
            "arousal": ev.emotion.arousal,
            "confidence": ev.emotion.confidence,
        }
        for ev in extracted
    ]

    # Step 2: Extract session side-channel signals
    with trace.step("signals", inputs={"turn_count": len(conversation)}) as rec:
        signals = extract_session_signals(
            conversation, classifier,
            precomputed_arousals=precomputed_arousals,
        )
        rec["outputs"] = signals.to_dict()

    if config.debug_mode:
        print(f"[post_session] Signals: vol={signals.volatility:.2f} "
              f"len_d={signals.length_delta:.2f} dens={signals.density:.2f} "
              f"temp_gap={signals.temperature_gap:.2f}"
              + (" *** FLAGGED" if signals.any_flagged() else ""))

    # Step 3: For each event — embed, save, write to store
    written_events: list[EventRecord] = []
    all_embedding_stats: list[dict[str, Any]] = []

    for ext_event in extracted:
        event_id = store.new_event_id()
        now = session_time if session_time is not None else datetime.now(timezone.utc)

        # Embed (text only — thinking is excluded from retrieval)
        vec = embedder.embed(ext_event.text)
        emb_path = embedder.save(vec, event_id)

        # Event body: conversation text + thinking chain (if any)
        body = ext_event.text
        if ext_event.thinking:
            body += "\n\n--- thinking ---\n\n" + ext_event.thinking

        # Build stats
        flat = vec.flatten().astype(float)
        stats = {
            "event_id": event_id,
            "shape": list(vec.shape),
            "max": float(np.max(flat)),
            "min": float(np.min(flat)),
            "mean": float(np.mean(flat)),
            "l2_norm": float(np.linalg.norm(flat)),
            "first_32": [round(float(x), 6) for x in flat[:32]],
        }
        all_embedding_stats.append(stats)

        # Create EventRecord
        record = EventRecord(
            filename=event_id,
            time=now,
            valence=ext_event.emotion.valence,
            arousal=ext_event.emotion.arousal,
            confidence=ext_event.emotion.confidence,
            dominant_label=ext_event.emotion.dominant_label,
            embedding=emb_path,
            embedding_dim=vec.shape[-1],
            body=body,
        )

        # Write to store
        written_path = store.write_event(record)
        written_events.append(record)

        # Trace with embedding stats
        trace.record_store_write(
            event_id=event_id,
            event_path=str(written_path),
            embedding_path=emb_path,
            embedding_vec=vec,
            emotion=ext_event.emotion,
            body_preview=ext_event.text[:200],
        )

        if config.debug_mode:
            print(f"[post_session] Wrote event {event_id} → {written_path}")
            print(f"  embedding: shape={stats['shape']} L2={stats['l2_norm']:.6f}")

    # Step 4: Temporal co-occurrence linking → graph.jsonl
    all_events = store.all_events()
    if written_events:
        from fiam.store.graph_store import GraphStore
        graph_store = GraphStore(config.graph_jsonl_path)

        temporal_edges = link_new_events(written_events, all_events, config)
        graph_store.append(temporal_edges)
        if config.debug_mode:
            print(f"[post_session] Temporal edges: {len(temporal_edges)} written to graph.jsonl")

    # Step 4b: Semantic similarity linking → graph.jsonl
    if written_events:
        semantic_edges = link_semantic(written_events, all_events, config)
        graph_store.append(semantic_edges)
        if config.debug_mode:
            print(f"[post_session] Semantic edges: {len(semantic_edges)} written to graph.jsonl")

    # Step 4c: LLM edge typing + event naming (optional, requires [graph] config)
    if written_events and config.graph_edge_provider:
        from fiam.retriever.edge_typer import type_edges_and_name
        # Include recent events as context for richer edge discovery
        context = [e for e in all_events if e.event_id not in
                   {w.event_id for w in written_events}][-6:]
        with trace.step("edge_typer", inputs={"new": len(written_events),
                                               "context": len(context)}) as rec:
            try:
                llm_edges, name_map = type_edges_and_name(
                    written_events, config, context_events=context)
                if llm_edges:
                    graph_store.append(llm_edges)
                if name_map:
                    _rename_events(store, written_events, name_map, config)
                rec["outputs"] = {"llm_edges": len(llm_edges),
                                  "renames": len(name_map)}
                if config.debug_mode:
                    print(f"[post_session] LLM edges: {len(llm_edges)}, "
                          f"renames: {name_map}")
            except Exception as e:
                rec["outputs"] = {"error": str(e)}
                if config.debug_mode:
                    print(f"[post_session] Edge typer failed: {e}")

    # Step 5: Generate report

    with trace.step("report") as rec:
        report_path = report.generate(
            config,
            trace.session_id,
            conversation=conversation,
            emotion_results=emotion_results,
            events=written_events,
            embedding_stats=all_embedding_stats,
            all_events=all_events,
            signals=signals,
        )
        rec["outputs"] = {"report_path": str(report_path)}

    if config.debug_mode:
        print(f"\n[post_session] Report: {report_path}")
        report_text = report_path.read_text(encoding="utf-8")
        print(report_text)

    # Step 6: HOLD detection — AI self-censorship for continued thinking
    hold_count = _detect_and_schedule_holds(config, conversation)

    return {
        "session_id": trace.session_id,
        "events_written": len(written_events),
        "report_path": str(report_path),
        "signals": signals.to_dict(),
        "wake_tags": _extract_and_schedule_wakes(config, conversation),
        "hold_count": hold_count,
    }


# ------------------------------------------------------------------
# Event renaming (LLM-suggested names)
# ------------------------------------------------------------------

def _rename_events(
    store: HomeStore,
    events: list[EventRecord],
    name_map: dict[str, str],
    config: FiamConfig,
) -> None:
    """Rename event files + embeddings based on LLM-suggested names.

    Also updates graph.jsonl references from old IDs to new names.
    """
    from fiam.store.graph_store import GraphStore

    renames: dict[str, str] = {}  # old_id → new_id

    for ev in events:
        new_name = name_map.get(ev.event_id)
        if not new_name or new_name == ev.event_id:
            continue

        old_event_path = config.events_dir / f"{ev.event_id}.md"
        new_event_path = config.events_dir / f"{new_name}.md"

        # Skip if target already exists (collision)
        if new_event_path.exists():
            if config.debug_mode:
                print(f"[rename] Skip {ev.event_id} → {new_name}: target exists")
            continue

        # Rename event file
        if old_event_path.exists():
            old_event_path.rename(new_event_path)

        # Rename embedding file
        old_emb = config.embeddings_dir / f"{ev.event_id}.npy"
        new_emb = config.embeddings_dir / f"{new_name}.npy"
        if old_emb.exists():
            old_emb.rename(new_emb)
            ev.embedding = f"embeddings/{new_name}.npy"

        # Update in-memory record
        renames[ev.event_id] = new_name
        ev.filename = new_name

        # Re-write with new filename
        store.write_event(ev)

    # Update graph.jsonl references
    if renames:
        gs = GraphStore(config.graph_jsonl_path)
        all_edges = gs.load_all()
        changed = False
        for edge in all_edges:
            if edge.src in renames:
                edge.src = renames[edge.src]
                changed = True
            if edge.dst in renames:
                edge.dst = renames[edge.dst]
                changed = True
        if changed:
            gs.rewrite(all_edges)


# ------------------------------------------------------------------
# HOLD detection
# ------------------------------------------------------------------

_HOLD_RE = re.compile(r"<<HOLD:(?P<reason>[^>]+)>>", re.IGNORECASE)


def _detect_and_schedule_holds(
    config: FiamConfig, conversation: list[dict[str, str]],
) -> int:
    """Detect <<HOLD:reason>> tags in assistant output.

    For each HOLD:
      1. Save the draft to home/self/drafts/
      2. Schedule a private WAKE 2 minutes later for re-entry
    """
    asst_text = "\n".join(
        t["text"] for t in conversation if t.get("role") == "assistant"
    )
    holds = list(_HOLD_RE.finditer(asst_text))
    if not holds:
        return 0

    drafts_dir = config.self_dir / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    count = 0

    for m in holds:
        reason = m.group("reason").strip()
        draft_id = now.strftime("%m%d_%H%M%S")
        draft_path = drafts_dir / f"hold_{draft_id}.md"
        draft_path.write_text(
            f"---\nreason: {reason}\ntime: {now.isoformat()}\n---\n\n{asst_text}",
            encoding="utf-8",
        )

        # Schedule a private WAKE to re-enter after a short pause
        wake_time = now + timedelta(minutes=2)
        try:
            import sys
            sys.path.insert(0, str(config.code_path / "scripts"))
            from fiam_lib.scheduler import append_to_schedule
            append_to_schedule([{
                "wake_at": wake_time.isoformat(),
                "type": "private",
                "reason": f"HOLD continuation: {reason}",
                "created": now.isoformat(),
            }], config)
        except Exception:
            pass
        count += 1

    if config.debug_mode:
        print(f"[post_session] HOLD: {count} hold(s) detected, drafts saved")

    return count


def _extract_and_schedule_wakes(
    config: FiamConfig, conversation: list[dict[str, str]],
) -> int:
    """Extract WAKE tags from assistant turns, append to schedule."""
    try:
        import sys
        sys.path.insert(0, str(config.code_path / "scripts"))
        from fiam_lib.scheduler import extract_wake_tags, append_to_schedule
        asst_text = "\n".join(
            t["text"] for t in conversation if t.get("role") == "assistant"
        )
        tags = extract_wake_tags(asst_text)
        if tags:
            count = append_to_schedule(tags, config)
            if config.debug_mode:
                print(f"[post_session] Scheduled {count} WAKE tags")
            return count
    except Exception as e:
        if config.debug_mode:
            print(f"[post_session] WAKE extraction failed: {e}")
    return 0
