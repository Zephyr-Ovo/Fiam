"""scripts/bridges/ — channel ↔ MQTT bridges.

Each bridge is an independent process that translates between an
external channel (email, favilla, ...) and the MQTT bus. The daemon never
talks to external APIs directly — it only subscribes to / publishes on
fiam/receive/<source> and fiam/dispatch/<target>.
"""
