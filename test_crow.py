import time
from rich.console import Console
from rich.live import Live
from scripts.fiam_lib.ui import _flow

c = Console()
_CROW = [
    "      z    ▄████▄ \n       z  ████████ \n    ▄▄▄███▀▀██▀██\n    ▀▀▀██████████\n        ████████ \n         ██  ██  ",
    "     z     ▄████▄ \n      z   ████████ \n    ▄▄▄███▀▀██▀██\n    ▀▀▀██████████\n        ████████ \n         ██  ██  ",
    "    Z      ▄████▄ \n     z    ████████ \n    ▄▄▄███▀▀██▀██\n    ▀▀▀██████████\n        ████████ \n         ██  ██  "
]

with Live(refresh_per_second=10, console=c) as live:
    for i in range(20):
        ts = time.strftime("%H:%M")
        frame = _CROW[i % len(_CROW)]
        text = ""
        for line in frame.split("\n"):
            text += "  " + line + "\n"
        text = text.rstrip() + f"     {ts}"
        live.update(_flow(text, offset=i))
        time.sleep(0.15)
