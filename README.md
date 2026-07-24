# AI Coach & Keyholder

Osobní lokální AI systém kombinující dlouhodobého kouče a systém
konzistence/zodpovědnosti. Viz [`philosophy.md`](philosophy.md) pro
principy, kterým musí odpovídat každé rozhodnutí a každá budoucí úprava.

## Stav projektu

**Fáze 0 — základ.** Hotovo:

- adresářová struktura (`core/`, `ai/`, `database/`, `bot/`, `integrations/`, `observations/`)
- `database/models.py` — dataclass kontrakty (ContextSnapshot, CoachAssessment,
  KeyholderAssessment, DecisionResult, ObservationRecord, Rule, ConsentRecord, ...)
- `database/migrations/001_initial_schema.sql` — SQLite schéma (hybridní:
  normalizovaná pole + JSON, verzování pravidel, consent_log, observations)
- `database/database.py` — přístupová vrstva (migrace + save/get pro
  všechny entity)
- `core/config.py` — načítání konfigurace a secrets z `.env`
- `database/backup.py` — zálohování přes SQLite online backup API, max.
  1 automatická denní záloha, záloha vždy před aplikací migrace (pokud DB
  už dřív měla nějakou verzi schématu), jednoduchá rotace (default: 14
  posledních záloh, `BACKUP_RETENTION_COUNT` v `.env`)
- `database/migrations/README.md` — závazná politika: migrace nikdy nesmí
  být destruktivní vůči uživatelským datům
- `bot/discord_bot.py` — základní Discord bot: připojení, logování zpráv
  do krátkodobé paměti, zatím **bez AI logiky** (přijde ve Fázi 1)

Vše výše bylo otestováno end-to-end (round-trip uložení/načtení pro
každou entitu, import a inicializace bota, zálohovací scénáře včetně
denního limitu, pre-migration zálohy a rotace).

**Zásady dat a záloh (ověřeno):**
- Uživatelská data (`data/coach_keyholder.db`, `data/backups/`) jsou mimo
  git (`.gitignore`) — update programu (`git pull`) se jich nedotkne.
- Migrace jsou výhradně aditivní (viz `database/migrations/README.md`).
- Runtime (bot, budoucí core enginy) nikdy nečte z `observations/` ani
  z audit exportů — jde o čistě write-only vrstvu z pohledu runtime.

## Instalace

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

Vyžaduje Python 3.13 (kód používá `enum.StrEnum` a moderní typing — funguje
i na 3.11+, ale cíleno na 3.13 dle domluvy).

## Konfigurace

```bash
copy .env.example .env
```

Doplň do `.env` alespoň `DISCORD_TOKEN` (Discord Developer Portal → tvá
aplikace → Bot → Token). Bota je potřeba pozvat na server s oprávněním
číst a psát zprávy, a v Developer Portal zapnout **Message Content Intent**
(bez něj bot neuvidí obsah zpráv — `discord_bot.py` na to spoléhá).

## Spuštění

```bash
python -m bot.discord_bot
```

Při prvním spuštění se automaticky vytvoří `data/coach_keyholder.db` a
aplikují se migrace. Bot zatím jen loguje zprávy a odpovídá potvrzovací
hláškou — ověřuje se tím komunikační vrstva, ne AI logika.

## Struktura

```
core/            # coach_engine, keyholder_engine, decision_engine, config (business logika — Fáze 1+)
ai/              # ollama_client, personality, analysis (Fáze 1+)
database/        # models.py, database.py, migrations/
bot/             # discord_bot.py, approval_flow.py (Fáze 6)
integrations/    # chaster.py, apple_health.py (Fáze 7)
observations/    # audit export (write-only z pohledu runtime — Fáze 3+)
philosophy.md    # referenční principy projektu — čti první
```

## Další kroky (Fáze 1)

- `ai/ollama_client.py` — wrapper nad Ollama REST API
- `core/context_engine.py` — první reálný `ContextSnapshot` z dat
- Napojení Discord bota na skutečnou konverzační paměť a jednoduchou
  syntézu odpovědi (zatím bez Coach/Keyholder rozdělení)
