# Trading Bot — Projektsammanfattning

## Vad vi bygger

Ett automatiserat handelssystem som använder **dual momentum** för att handla amerikanska aktier med eget kapital. Systemet rankar aktier efter relativ styrka, skyddar sig automatiskt i dåliga marknader och kräver ingen manuell inblandning i det dagliga arbetet.

---

## Beslut som redan är fattade

| Område | Beslut | Motivering |
|--------|--------|------------|
| Handelsstil | Månadsvis rebalansering, hållperiod typiskt 1–6 månader | Dual momentum är en månadsbaserad strategi — akademisk evidens bygger på månadsdata. Låg omsättning ger låga transaktionskostnader (~0.8%/år vs ~10%+ för daglig handel) |
| Strategi | Dual momentum (absolut + relativt) | Beprövad akademisk grund, tydlig ekonomisk logik |
| Marknad | Amerikanska aktier via Alpaca API | Bäst API-stöd och datatillgång |
| Universe | ~50 S&P 100-aktier + SPY + BIL | Likvida, pålitlig data, hanterbart antal |
| Marknadsfilter | VIX + SPY glidande medelvärde | Skyddar mot breda nedgångar automatiskt |
| Positioner | 3–5 samtidigt (avgörs av backtesting) | Balans mellan diversifiering och signal-styrka |
| Blankning | Fas 2 — inte i fas 1 | Minskar komplexitet, blankningskostnader svåra att modellera |
| Startfas | Paper trading minst 3–6 månader | Noll finansiell risk under validering |
| Hosting | Hetzner VPS, Linux, ~5 EUR/mån | Billigt, enkelt, tillräckligt för swing trading |
| Språk | Python 3.11+ | Bäst ekosystem för finansiell data och ML |

---

## Strategi i detalj

### Dual momentum — hur det fungerar

1. **Beräkna sammansatt momentum** för varje aktie i universe:
   - Genomsnitt av 3-, 6- och 12-månaders avkastning (viktat lika)
   - Mer robust än ett enskilt fönster

2. **Absolut momentum-filter** (Antonaccis originalidé):
   - Om en aktie har negativt absolut momentum (sämre än cash/BIL) — köp inte, oavsett ranking
   - Skyddar mot att köpa relativt starka aktier i en generellt fallande marknad

3. **Relativt momentum — ranking**:
   - Rankar alla aktier som passerat absolut momentum-filter
   - Köper de X högst rankade (antal avgörs av backtesting)

4. **Marknadsfilter (regime filter)**:
   - Om SPY < sitt 200-dagars glidande medelvärde → gå till cash (BIL)
   - Om VIX > 30 → reducera positionsstorlek med 50%
   - Förhindrar handel i kaotiska marknader

5. **Rebalansering**:
   - En gång per månad, sista handelsdagen
   - Sälj positioner som fallit ur topp-X eller brutit absolut momentum
   - Köp nya toppkandidater

### Riskhantering

| Parameter | Värde | Notering |
|-----------|-------|----------|
| Max per position | 20% av portfölj | Minskas om VIX > 30 |
| Stop-loss | 8% under inköpspris | Hårdkodad regel, åsidosätter signal |
| Max daglig förlust | 3% av portfölj | Stänger all handel den dagen |
| Max drawdown-gräns | 20% | Pausar systemet, kräver manuell återstart |
| Rebalansfrekvens | Månadsvis | Minimerar transaktionskostnader |

---

## Byggplan — sex steg

### ✅ Steg 1: Datapipeline — KLAR
**Fil:** `data_pipeline.py`

- Hämtar daglig OHLCV-data via yfinance
- Sparar i lokal SQLite-databas (`trading_data.db`)
- Inkrementella uppdateringar — hämtar bara ny data
- Validering av datakvalitet (extremvärden, täckning)
- Hanterar SPY, BIL, VIX + 50 aktier i universe

**Kör:** `python data_pipeline.py`

---

### ✅ Steg 2: Momentumrankning — KLAR
**Fil:** `momentum.py`

- Beräkna 3-, 6-, 12-månaders avkastning per aktie
- Vägt genomsnitt → sammansatt momentum-score
- Absolut momentum-filter mot BIL
- Returnera rankad lista med datum som index

---

### ✅ Steg 3: Marknadsfilter — KLAR
**Fil:** `market_filter.py`

- SPY vs 200-dagars SMA → bool: är marknaden i upptrend?
- VIX-nivå → positionsstorleksmultiplikator (1.0 eller 0.5)
- Returnerar dagligt regime-läge för hela backtestperioden

---

### ✅ Steg 4: Portföljlogik — KLAR
**Fil:** `portfolio.py`

- Tar input från steg 2 och 3
- Bestämmer vilka positioner som ska öppnas/stängas
- Hanterar position sizing baserat på VIX-regime
- Applicerar stop-loss och daglig förlustgräns

---

### ✅ Steg 5: Backtesting — KLAR
**Fil:** `backtest.py`

- Kör steg 2–4 på historisk data (2010–2024)
- Uppdelning: träning 2010–2018, validering 2018–2021, test 2021–2024
- Beräknar: total avkastning, Sharpe-ratio, max drawdown, antal affärer
- Jämför mot buy-and-hold SPY
- Walk-forward validation som komplement

**Krav för att gå vidare till paper trading:**
- Sharpe-ratio > 1.0 på testdata
- Max drawdown < 20%
- Positivt resultat i >60% av testade månader
- Slår SPY på riskjusterad basis

---

### ✅ Steg 6: Lokal paper trading — KLAR
**Fil:** `live.py`

- Simulerar ordrar lokalt med yfinance-priser — ingen mäklare krävs
- Kör dagligen via cron kl 23:10 CET (21:10 UTC)
- Valfria Telegram-rapporter (fungerar utan)
- Loggar positioner, trades och P&L till SQLite (`live_state.db`)

---

## Teknisk stack

```
Python 3.11+
├── yfinance          # Marknadsdata
├── pandas            # Datahantering
├── numpy             # Beräkningar
├── sqlite3           # Lokal databas (inbyggd)
└── requests          # HTTP (Telegram-notifieringar, valfritt)

Hosting: Hetzner VPS, Ubuntu 24
Schemaläggning: cron
Versionshantering: Git / GitHub
```

---

## Filstruktur

```
trading-bot/
├── data_pipeline.py     # Steg 1 — datahämtning och lagring
├── momentum.py          # Steg 2 — rankningslogik
├── market_filter.py     # Steg 3 — marknadsregim
├── portfolio.py         # Steg 4 — portföljbeslut
├── backtest.py          # Steg 5 — historisk validering
├── live.py              # Steg 6 — paper/live trading
├── trading_data.db      # Lokal databas (genereras vid körning)
├── config.py            # Konfiguration och konstanter
└── README.md            # Denna fil
```

---

## Viktiga designbeslut och varningar

**Använd alltid kronologisk datadelning** — aldrig slumpmässig. Signaler appliceras dag+1 (inte samma dag som beräkning) för att undvika look-ahead bias.

**Survivorship bias** — yfinance innehåller bara aktier som fortfarande existerar. Backtests är strukturellt för optimistiska av den anledningen. Resultaten bör tolkas konservativt — dra av ~1–2% CAGR mentalt.

**Transaktionskostnader** — systemet räknar med 0.15% per affär (köp eller sälj). Månadsvis rebalansering ger ~0.8%/år i kostnader, vilket är hanterbart. Ökar du rebalanseringsfrekvensen förstörs fördelen snabbt.

**Sharpe-ratio** — beräknas med BIL-avkastning som riskfri ränta. Under perioder med hög ränta (2022–2024: ~4–5%) är det avgörande att inte använda 0% som proxy — det överskattar Sharpe med 0.3–0.5 enheter.

**Blankning är fas 2** — lägg inte till det förrän fas 1 är validerad med live-data. Blankningskostnader (lånavgifter) är svåra att modellera korrekt i backtesting.

**Testdatan är helig** — titta på 2021–2024-perioden EN gång, efter att all parameterjustering är klar mot tränings- och valideringsdata.

---

## Valideringskriterier — sammanfattning

| Fas | Krav |
|-----|------|
| → Paper trading | Sharpe > 1.0, drawdown < 20%, >60% positiva månader på testdata |
| → Live trading | 3+ månader paper trading, 30+ affärer, resultat konsistent med backtest |
| Stoppa systemet | Drawdown > 20% live, eller resultat systematiskt sämre än backtest |
