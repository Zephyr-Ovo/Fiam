"""
fiam pipeline — orchestrates pre_session and post_session flows.

pre_session:
  retriever.joint.search() → synthesizer.stance.generate() → injector.write()

post_session:
  classifier.analyze_event() → extractor.event.segment() →
  [for each event: embedder.embed_and_save(), store.write()] →
  report.generate()
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from fiam.config import FiamConfig
from fiam.classifier.emotion import EmotionClassifier
from fiam.extractor import event as event_extractor
from fiam.extractor.signals import extract_session_signals
from fiam.injector import claude_code as injector
from fiam.injector.home_diff import detect_uncommitted
from fiam.logging.trace import Trace
from fiam.logging import report
from fiam.retriever import joint as joint_retriever
from fiam.retriever.embedder import Embedder
from fiam.retriever.temporal import link_new_events
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
) -> dict[str, Any]:
    """Run the post-session pipeline: classify → extract → embed → store → report."""
    trace = Trace(logs_root=config.logs_dir, session_id=session_id)
    store = HomeStore(config)
    classifier = EmotionClassifier(config)
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

    # Step 1: Extract events (classifier runs inside extractor per-turn)
    # Pre-load stored vectors for novelty check
    stored_vecs: list[np.ndarray] = []
    emb_dir = config.embeddings_dir
    if emb_dir.is_dir():
        for npy in emb_dir.glob("*.npy"):
            vec = np.load(npy)
            if vec.shape[0] == config.embedding_dim:
                stored_vecs.append(vec)

    with trace.step("extractor", inputs={"turn_count": len(conversation)}) as rec:
        extracted = event_extractor.segment(
            conversation, classifier,
            arousal_threshold=config.arousal_threshold,
            embedder=embedder,
            stored_vecs=stored_vecs,
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
        signals = extract_session_signals(conversation, classifier)
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
        now = datetime.now(timezone.utc)

        # Embed
        vec = embedder.embed(ext_event.text)
        emb_path = embedder.save(vec, event_id)

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
            embedding=emb_path,
            embedding_dim=vec.shape[-1],
            body=ext_event.text,
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

    # Step 4: Temporal co-occurrence linking
    all_events = store.all_events()
    if written_events:
        modified = link_new_events(written_events, all_events, config)
        # Persist link updates on both new and modified existing events
        for ev in written_events:
            store.update_metadata(ev)
        for ev in modified:
            store.update_metadata(ev)
        if config.debug_mode:
            link_count = sum(len(e.links) for e in written_events)
            print(f"[post_session] Temporal links: {link_count} links across "
                  f"{len(written_events)} new + {len(modified)} existing events")

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

    return {
        "session_id": trace.session_id,
        "events_written": len(written_events),
        "report_path": str(report_path),
        "signals": signals.to_dict(),
    }
