"""Quick speed test for the embed endpoint (mean-pool vs short texts)."""
import requests, time

URL = "http://127.0.0.1:8819/embed"

# Short text (no chunking needed)
short = "This is a quick test of the embedding endpoint."
t0 = time.time()
r = requests.post(URL, json={"texts": [short]})
t1 = time.time()
print(f"Short ({len(short)} chars): {r.status_code} | {t1-t0:.2f}s | dims={len(r.json()['vectors'][0])}")

# Medium text (~800 chars, 2 chunks)
medium = "Today we had a long conversation about AI memory systems. " * 15
t0 = time.time()
r = requests.post(URL, json={"texts": [medium]})
t1 = time.time()
print(f"Medium ({len(medium)} chars): {r.status_code} | {t1-t0:.2f}s | dims={len(r.json()['vectors'][0])}")

# Long text (~2500 chars, 5 chunks)
long_text = "The distributed identity system needs to handle multiple nodes. " * 40
t0 = time.time()
r = requests.post(URL, json={"texts": [long_text]})
t1 = time.time()
print(f"Long ({len(long_text)} chars): {r.status_code} | {t1-t0:.2f}s | dims={len(r.json()['vectors'][0])}")

# Batch: 4 medium texts at once
batch = [medium] * 4
t0 = time.time()
r = requests.post(URL, json={"texts": batch})
t1 = time.time()
print(f"Batch 4x medium: {r.status_code} | {t1-t0:.2f}s | vecs={len(r.json()['vectors'])}")
