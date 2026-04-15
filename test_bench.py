"""Quick pipeline benchmark with per-stage timing."""
import time, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "scripts"))

t0 = time.time()

from fiam_lib.core import _build_config
from fiam.config import FiamConfig
from fiam.classifier.text_intensity import text_intensity
from fiam.extractor import event as event_extractor
from fiam.extractor.signals import extract_session_signals
from fiam.retriever.embedder import Embedder
import numpy as np

raw = json.loads(Path("test_vault/fixtures/conversations.json").read_text("utf-8"))
cfg = _build_config()

conv_data = None
for session in raw:
    msgs = session.get("chat_messages", [])
    if len(msgs) > 5:
        conv_data = msgs
        break

conversation = []
for msg in conv_data:
    role = msg.get("sender", "unknown")
    if role == "human":
        role = "user"
    text = msg.get("text", "")
    if text and role in ("user", "assistant"):
        conversation.append({"role": role, "text": text[:1000]})

print(f"Loaded {len(conversation)} turns in {time.time()-t0:.1f}s")

# Stage 1: text intensity (no model, pure heuristic)
t1 = time.time()
for t in conversation:
    text_intensity(t["text"])
print(f"[{time.time()-t1:.1f}s] Text intensity for {len(conversation)} turns")

# Stage 2: embedder init + first embed
t2 = time.time()
embedder = Embedder(cfg)
vec = embedder.embed("test")
print(f"[{time.time()-t2:.1f}s] Embedder cold load + single embed")

# Stage 3: batch embed
t3 = time.time()
embed_texts = [t["text"][:512] for t in conversation]
vecs = embedder.embed_batch(embed_texts)
print(f"[{time.time()-t3:.1f}s] Batch embed {len(embed_texts)} texts")

# Stage 4: full segment (no classifier — uses text_intensity)
stored_vecs = []
t4 = time.time()
extracted = event_extractor.segment(
    conversation,
    embedder=embedder,
    stored_vecs=stored_vecs,
    debug=True,
)
print(f"[{time.time()-t4:.1f}s] segment() → {len(extracted)} events")

# Stage 5: signals (no classifier)
t5 = time.time()
signals = extract_session_signals(conversation)
print(f"[{time.time()-t5:.1f}s] extract_session_signals()")

print(f"\nTOTAL: {time.time()-t0:.1f}s")
