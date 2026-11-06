"""
Varsler til Slack når en målestasjon i Norge viser "Svært høyt nivå" (lilla) luftforurensning.

Datakilde: Miljødirektoratets API, https://api-luftmalinger.miljodirektoratet.no/swagger/index.html
Endepunktet /public/aq/utd gir siste målte verdi og indeks for alle stasjoner.
Indeks 4 (farge 990099, lilla) = "Svært høy".

Bruk:
    Legg webhooken i luftvarsel.env (se luftvarsel.env.example), eller sett miljøvariabel:
    set SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...      (Windows cmd)
    $env:SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."   (PowerShell)

    python luftvarsel.py                 # sjekker hvert 15. minutt i en evig løkke
    python luftvarsel.py --interval 600  # sjekker hvert 10. minutt
    python luftvarsel.py --once          # én sjekk (for Oppgaveplanlegger / cron)
    python luftvarsel.py --once --dry-run --min-index 3   # test: skriv ut i stedet for å sende

Scriptet husker hvilke målinger det allerede har varslet om (i en state-fil), så du får
ett varsel når en stasjon går opp til lilla – ikke ett per sjekk – og en melding når nivået
faller igjen.

Kun standardbiblioteket brukes, ingen pakker må installeres.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

API_URL = "https://api-luftmalinger.miljodirektoratet.no/public/aq/utd"
PURPLE_INDEX = 4  # "Svært høy"
INDEX_TEXT = {1: "Lite", 2: "Moderat", 3: "Høy", 4: "Svært høy"}
INDEX_COLOR = {1: "#6ee86e", 2: "#ff9900", 3: "#ff0000", 4: "#990099"}
DEFAULT_STATE_FILE = Path(__file__).with_name("luftvarsel_state.json")
ENV_FILE = Path(__file__).with_name("luftvarsel.env")
USER_AGENT = "luftvarsel/1.0 (python-urllib)"

log = logging.getLogger("luftvarsel")


# --------------------------------------------------------------------------- konfig


def load_env_file(path: Path = ENV_FILE) -> None:
    """Leser KEY=VALUE-linjer fra luftvarsel.env (hvis den finnes) inn i os.environ.
    Variabler som allerede er satt i miljøet, vinner."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --------------------------------------------------------------------------- API


def fetch_latest(timeout: int = 30) -> list[dict]:
    req = urllib.request.Request(
        API_URL, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def find_alerts(measurements: list[dict], min_index: int) -> dict[str, dict]:
    """Returnerer gyldige målinger med indeks >= min_index, nøkkel = tidsserie-id."""
    hits = {}
    for m in measurements:
        if not m.get("isValid", True) or not m.get("isVisible", True):
            continue
        if (m.get("index") or 0) >= min_index:
            hits[str(m["id"])] = m
    return hits


# --------------------------------------------------------------------------- state


def load_state(path: Path) -> dict[str, dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Klarte ikke lese state-fil %s (%s) – starter på nytt", path, e)
        return {}


def save_state(path: Path, state: dict[str, dict]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- Slack


def _fmt_time(iso: str | None) -> str:
    if not iso:
        return "?"
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y kl. %H:%M")
    except ValueError:
        return iso


def _describe(m: dict) -> str:
    value = m.get("value")
    value_txt = f"{value:.1f}" if isinstance(value, (int, float)) else str(value)
    maps = f"https://www.google.com/maps?q={m.get('latitude')},{m.get('longitude')}"
    return (
        f"*{m.get('station')}* ({m.get('municipality') or m.get('area')}) – "
        f"<{maps}|kart>\n"
        f"{m.get('component')}: *{value_txt} {m.get('unit', '')}* "
        f"– {INDEX_TEXT.get(m.get('index'), m.get('index'))}\n"
        f"Måleperiode: {_fmt_time(m.get('fromTime'))}–{_fmt_time(m.get('toTime'))[-5:]} "
        f"· {m.get('type', '')}"
    )


def build_new_alert_payload(new: list[dict]) -> dict:
    n_stations = len({m.get("station") for m in new})
    level = INDEX_TEXT.get(max(m.get("index") or 0 for m in new), "Høyt")
    title = (
        f":rotating_light: {level}t nivå av luftforurensning på "
        f"{n_stations} stasjon{'er' if n_stations != 1 else ''}"
    )
    return {
        "text": title,  # vises i varsler/notifikasjoner
        "attachments": [
            {
                "color": INDEX_COLOR.get(m.get("index"), "#990099"),
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": _describe(m)}}
                ],
            }
            for m in new
        ],
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": f"*{title}*"}}],
    }


def build_cleared_payload(cleared: list[dict]) -> dict:
    lines = [f"• {m.get('station')} – {m.get('component')}" for m in cleared]
    text = ":white_check_mark: Ikke lenger svært høyt nivå:\n" + "\n".join(lines)
    return {"text": text}


def post_to_slack(webhook: str, payload: dict, timeout: int = 15) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
        if resp.status != 200 or body.strip() != "ok":
            raise RuntimeError(f"Slack svarte {resp.status}: {body}")


# --------------------------------------------------------------------------- logikk


def check_once(args: argparse.Namespace, webhook: str | None) -> None:
    measurements = fetch_latest()
    current = find_alerts(measurements, args.min_index)
    previous = load_state(args.state_file)

    new = [m for key, m in current.items() if key not in previous]
    cleared = [m for key, m in previous.items() if key not in current]

    log.info(
        "Hentet %d målinger: %d på/over indeks %d (%d nye, %d avsluttet)",
        len(measurements), len(current), args.min_index, len(new), len(cleared),
    )

    payloads = []
    if new:
        payloads.append(build_new_alert_payload(new))
    if cleared and not args.no_cleared:
        payloads.append(build_cleared_payload(cleared))

    for payload in payloads:
        if args.dry_run or not webhook:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            post_to_slack(webhook, payload)
            log.info("Sendt varsel til Slack")

    # Lagre state først etter vellykket sending, så vi prøver på nytt ved feil.
    if not args.dry_run:
        save_state(args.state_file, current)


def main() -> int:
    load_env_file()
    p = argparse.ArgumentParser(description="Slack-varsel ved svært høy luftforurensning")
    p.add_argument("--interval", type=int, default=900, help="sekunder mellom sjekker (default 900)")
    p.add_argument("--once", action="store_true", help="kjør én sjekk og avslutt")
    p.add_argument("--dry-run", action="store_true", help="skriv ut meldingen i stedet for å sende")
    p.add_argument("--min-index", type=int, default=PURPLE_INDEX,
                   help="laveste indeks som varsles (4 = lilla/svært høy; bruk 3 for testing)")
    p.add_argument("--no-cleared", action="store_true", help="ikke send melding når nivået faller")
    p.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    p.add_argument("--webhook", default=os.environ.get("SLACK_WEBHOOK_URL"),
                   help="Slack webhook-URL (default: SLACK_WEBHOOK_URL fra miljøet eller luftvarsel.env)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not args.webhook and not args.dry_run:
        p.error("Mangler webhook: sett SLACK_WEBHOOK_URL eller bruk --webhook (eller --dry-run)")

    while True:
        try:
            check_once(args, args.webhook)
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as e:
            log.error("Sjekk feilet: %s", e)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
