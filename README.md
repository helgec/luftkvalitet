# luftkvalitet

Python-script som sjekker Miljødirektoratets API for luftmålinger og sender varsel til en
Slack-kanal når en målestasjon i Norge viser **«Svært høy»** forurensning (lilla, indeks 4).

- Datakilde: <https://api-luftmalinger.miljodirektoratet.no/swagger/index.html> (`/public/aq/utd`)
- Du får ett varsel når en stasjon går opp til lilla, og én melding når nivået faller igjen.
  Tilstanden lagres i `luftvarsel_state.json`.
- Scriptet bruker bare standardbiblioteket i Python 3.8+, så du trenger ikke installere noen pakker.

## Oppsett på Raspberry Pi

```bash
cd ~
git clone https://github.com/helgec/luftkvalitet.git
cd luftkvalitet

cp luftvarsel.env.example luftvarsel.env
nano luftvarsel.env            # lim inn Slack-webhooken
chmod 600 luftvarsel.env

# Test uten å sende noe:
python3 luftvarsel.py --once --dry-run --min-index 3
```

### Cron: én gang i timen

`crontab -e` og legg til:

```cron
# Henter siste versjon fra GitHub og sjekker luftkvaliteten kl. xx:15 hver time
15 * * * * cd $HOME/luftkvalitet && git pull -q --ff-only; /usr/bin/python3 $HOME/luftkvalitet/luftvarsel.py --once >> $HOME/luftkvalitet/luftvarsel.log 2>&1
```

Målingene gjelder hele timer og publiseres litt etter at timen er slutt. Derfor kjører jobben
et kvarter over hver hel time. Ønsker du ikke automatisk oppdatering fra GitHub, fjerner du
`git pull`-delen.

Se loggen med `tail -f ~/luftkvalitet/luftvarsel.log`.

## Valg

| Flagg | Betydning |
|---|---|
| `--once` | Kjør én sjekk og avslutt (brukes med cron) |
| `--interval N` | Uten `--once`: sjekk hvert N. sekund i en løkke (standard 900) |
| `--min-index N` | Laveste indeks det varsles om: 4 = svært høy (standard), 3 = høy |
| `--dry-run` | Skriv ut Slack-meldingen i stedet for å sende den |
| `--no-cleared` | Ikke send melding når nivået faller igjen |
| `--webhook URL` | Overstyrer `SLACK_WEBHOOK_URL` |
| `--state-file STI` | Annen plassering for tilstandsfilen |

Webhooken leses i denne rekkefølgen: `--webhook`, miljøvariabelen `SLACK_WEBHOOK_URL`,
og til slutt `luftvarsel.env` ved siden av scriptet.
