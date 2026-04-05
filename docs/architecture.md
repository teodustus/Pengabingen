# Systemarkitektur — Dual Momentum Trading Bot

## 1. Modulöversikt

```mermaid
graph TD
    CFG[config.py\nKonstanter & universe]

    DP[data_pipeline.py\nHämtar & lagrar prisdata]
    DB[(trading_data.db\nSQLite)]

    MOM[momentum.py\nRankar aktier]
    MF[market_filter.py\nMarknadsregim]
    PORT[portfolio.py\nMålvikter & riskgränser]

    BT[backtest.py\nHistorisk validering]
    LIVE[live.py\nDaglig körning]

    ALPACA[Alpaca API\nOrderexekvering]
    TG[Telegram\nNotifieringar]
    LDB[(live_state.db\nP&L & trades)]

    CFG --> DP
    CFG --> MOM
    CFG --> MF
    CFG --> PORT

    DP --> DB
    DB --> MOM
    DB --> MF

    MOM --> PORT
    MF --> PORT

    PORT --> BT
    PORT --> LIVE

    LIVE --> ALPACA
    LIVE --> TG
    LIVE --> LDB
```

---

## 2. Daglig körning (live.py)

```mermaid
flowchart TD
    START([Cron 21:10 UTC])
    VALIDATE{.env komplett?}
    ABORT([Avbryt + logga fel])

    UPDATE[Uppdatera prisdata\nyfinance]
    PRICES[Ladda prices + VIX\nfrån SQLite]
    VIXCHECK{VIX-data\n> 3 dagar gammal?}
    VIXWARN[Skicka varning\nvia Telegram]

    FETCH[Hämta live-priser\nfrån Alpaca]
    FETCHFAIL{Misslyckades?}
    FETCHABORT([Avbryt + Telegram-varning\nStop-loss EJ kontrollerat])

    STOPCHECK{Någon position\nnedanför stop-loss?}
    STOPLOSS[Stäng position\nLogga i live_state.db]

    DDCHECK{Drawdown\n> 20%?}
    DDPAUSE([Skicka varning\nVänta på manuell återstart])

    REBALDAY{Sista handelsdagen\ni månaden?}
    CALCSIG[Beräkna signaler\nmomentum + regime]
    WEIGHTS[Beräkna målvikter\nportfolio.target_weights]
    EXECUTE[Lägg ordrar\nAlpaca market orders]

    PNL[Logga P&L\nlive_state.db]
    REPORT[Skicka daglig rapport\nTelegram]
    END([Klar])

    START --> VALIDATE
    VALIDATE -- Nej --> ABORT
    VALIDATE -- Ja --> UPDATE
    UPDATE --> PRICES
    PRICES --> VIXCHECK
    VIXCHECK -- Ja --> VIXWARN --> FETCH
    VIXCHECK -- Nej --> FETCH
    FETCH --> FETCHFAIL
    FETCHFAIL -- Ja --> FETCHABORT
    FETCHFAIL -- Nej --> STOPCHECK
    STOPCHECK -- Ja --> STOPLOSS --> DDCHECK
    STOPCHECK -- Nej --> DDCHECK
    DDCHECK -- Ja --> DDPAUSE
    DDCHECK -- Nej --> REBALDAY
    REBALDAY -- Nej --> PNL
    REBALDAY -- Ja --> CALCSIG --> WEIGHTS --> EXECUTE --> PNL
    PNL --> REPORT --> END
```

---

## 3. Portföljbeslut — från signal till position

```mermaid
flowchart TD
    PRICES[Månadspriser\nload_prices]

    MOM3[3-månaders\navkastning]
    MOM6[6-månaders\navkastning]
    MOM12[12-månaders\navkastning]
    COMP[Sammansatt score\n= medel 3+6+12 mån]

    ABILFILTER{Score >\nBIL:s score?}
    EXCLUDED[Exkluderas\nNaN i ranking]
    RANK[Rangordnas\nrank 1 = bäst]

    SPYCHECK{SPY > 200d\nglidande medelvärde?}
    ALLCASH[100% BIL\nGå till cash]

    TOPN[Välj topp-5\naktier]

    VIXCHECK{VIX > 30?}
    FULLSIZE[20% per position\nVIX-mult = 1.0]
    HALFSIZE[10% per position\nVIX-mult = 0.5]

    REST[Resterande kapital\n→ BIL]
    PORTFOLIO[Portfölj\nMålvikter klara]

    PRICES --> MOM3 & MOM6 & MOM12
    MOM3 & MOM6 & MOM12 --> COMP
    COMP --> ABILFILTER
    ABILFILTER -- Nej --> EXCLUDED
    ABILFILTER -- Ja --> RANK

    RANK --> SPYCHECK
    SPYCHECK -- Nej\nNedtrend --> ALLCASH
    SPYCHECK -- Ja\nUpptrend --> TOPN

    TOPN --> VIXCHECK
    VIXCHECK -- Nej --> FULLSIZE
    VIXCHECK -- Ja --> HALFSIZE

    FULLSIZE & HALFSIZE --> REST --> PORTFOLIO
```

---

## 4. Riskhantering — dagliga gränser

```mermaid
flowchart LR
    subgraph Daglig kontroll
        A[Öppning av dag]
        B{Daglig förlust\n> 3%?}
        C[Likvidera allt\ntill BIL]
        D{Drawdown\n> 20%?}
        E[Pausa systemet\nManuell återstart krävs]
        F{Stop-loss\ntriggad?}
        G[Stäng position\ntill BIL]
        H[Normal handel]
    end

    subgraph Per position
        SL[Entry-pris satt\nvid köp]
        SLX{Pris < entry\n× 0.92?}
        SELL[Sälj direkt]
    end

    A --> B
    B -- Ja --> C
    B -- Nej --> D
    D -- Ja --> E
    D -- Nej --> F
    F -- Ja --> G
    F -- Nej --> H

    SL --> SLX
    SLX -- Ja --> SELL
```

---

## 5. Backtesting — datadelning och valideringsgates

```mermaid
timeline
    title Datadelning 2010–idag
    section Träning (2010–2017)
        Parameterjustering : Optimera top_n, VIX-tröskel
        Strategiutveckling  : Finjustera regler
    section Validering (2018–2020)
        Mellankalibrering   : Inkluderar COVID-kraschen mars 2020
        Ingen slutsats      : Bara kalibreringscheck
    section Test (2021–idag)
        Helig data          : Titta EN gång
        Slutlig dom         : Godkänd eller inte
```

**Valideringskrav för att gå vidare till paper trading:**

```mermaid
flowchart LR
    TEST[Testperiod\n2021–idag]

    S{Sharpe\n> 1.0?}
    D{Max drawdown\n> −20%?}
    M{Positiva månader\n> 60%?}
    B{Slår SPY\nriskjusterat?}

    PASS([✅ Godkänd\nGå till paper trading])
    FAIL([❌ Ej godkänd\nJustera på träningsdata])

    TEST --> S
    S -- Ja --> D
    S -- Nej --> FAIL
    D -- Ja --> M
    D -- Nej --> FAIL
    M -- Ja --> B
    M -- Nej --> FAIL
    B -- Ja --> PASS
    B -- Nej --> FAIL
```
