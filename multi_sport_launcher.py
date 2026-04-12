"""
Multi-Sport Bot Launcher
========================
Felügyeli és újraindítja az összes multi-sport botot:
  – multi_sport_bot.py   (jégkorong, kézilabda, röplabda O/U)
  – nba_rest_bot.py      (kosárlabda rest-advantage UNDER)

Mindkét bot a MULTI_SPORT_BOT_TOKEN / MULTI_SPORT_CHAT_ID csatornát használja.
"""
from __future__ import annotations

import subprocess
import sys
import time
import logging
import signal
import os

logging.basicConfig(
    format="%(asctime)s LAUNCHER %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("launcher")

BOTS = [
    "multi_sport_bot.py",
    "nba_rest_bot.py",
]

RESTART_DELAY = 5   # másodperc újraindítás előtt
CHECK_INTERVAL = 8  # másodperc a felügyeleti ciklus között

running: dict[str, subprocess.Popen] = {}


def start_bot(bot: str) -> subprocess.Popen:
    p = subprocess.Popen(
        [sys.executable, bot],
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    logger.info(f"✅ Elindítva: {bot} (PID: {p.pid})")
    return p


def shutdown(signum, frame):
    logger.info("SIGTERM érkezett — leállítás...")
    for bot, proc in running.items():
        proc.terminate()
        logger.info(f"Leállítva: {bot}")
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    for bot in BOTS:
        running[bot] = start_bot(bot)

    while True:
        time.sleep(CHECK_INTERVAL)
        for bot in BOTS:
            proc = running.get(bot)
            if proc and proc.poll() is not None:
                logger.warning(
                    f"⚠️ {bot} leállt (exit={proc.returncode}) — "
                    f"újraindítás {RESTART_DELAY}mp múlva..."
                )
                time.sleep(RESTART_DELAY)
                running[bot] = start_bot(bot)


if __name__ == "__main__":
    main()
