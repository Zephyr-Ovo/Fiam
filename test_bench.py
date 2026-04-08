"""Quick pipeline benchmark with per-stage timing."""
import time, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "scripts"))

t0 = time.time()

from fiam_lib.core import _build_config
from fiam.config import FiamConfig
from fiam.classifier.emotion import get_classifier
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

# Stage 1: classifier init + first model load
t1 = time.time()
classifier = get_classifier(cfg)
print(f"[{time.time()-t1:.1f}s] Classifier created")

# Stage 2: test single analyze
t2 = time.time()
r = classifier.analyze("Hello world")
print(f"[{time.time()-t2:.1f}s] Single analyze (cold model load)")

# Stage 3: batch analyze (pipeline Step 0 — classify once, reuse everywhere)
user_texts = [t["text"][:512] for t in conversation if t["role"] == "user"]
asst_texts = [t["text"][:512] for t in conversation if t["role"] == "assistant"]
all_texts = user_texts + asst_texts
t3 = time.time()
all_emotions = classifier.analyze_batch(all_texts)
user_emotions = all_emotions[:len(user_texts)]
asst_emotions = all_emotions[len(user_texts):]
print(f"[{time.time()-t3:.1f}s] Batch analyze {len(all_texts)} texts (user={len(user_texts)}, asst={len(asst_texts)})")

precomputed_arousals = {
    "user": [e.arousal for e in user_emotions],
    "asst": [e.arousal for e in asst_emotions],
}

# Stage 4: embedder init + first embed
t4 = time.time()
embedder = Embedder(cfg)
vec = embedder.embed("test")
print(f"[{time.time()-t4:.1f}s] Embedder cold load + single embed")

# Stage 5: batch embed
t5 = time.time()
embed_texts = [t["text"][:512] for t in conversation]
vecs = embedder.embed_batch(embed_texts)
print(f"[{time.time()-t5:.1f}s] Batch embed {len(embed_texts)} texts")

# Stage 6: full segment (with precomputed emotions — no re-classification)
stored_vecs = []
t6 = time.time()
extracted = event_extractor.segment(
    conversation, classifier,
    embedder=embedder,
    stored_vecs=stored_vecs,
    precomputed_user_emotions=user_emotions,
    precomputed_asst_emotions=asst_emotions,
    debug=True,
)
print(f"[{time.time()-t6:.1f}s] segment() → {len(extracted)} events (precomputed emotions)")

# Stage 7: signals (with precomputed arousals)
t7 = time.time()
signals = extract_session_signals(conversation, classifier, precomputed_arousals=precomputed_arousals)
print(f"[{time.time()-t7:.1f}s] extract_session_signals() (precomputed arousals)")

print(f"\nTOTAL: {time.time()-t0:.1f}s")
