# AI Coach & Keyholder

Osobní lokální AI systém kombinující dlouhodobého kouče a systém
konzistence/zodpovědnosti. Viz [`philosophy.md`](philosophy.md) pro
principy, kterým musí odpovídat každé rozhodnutí a každá budoucí úprava,
a [`docs/architecture/`](docs/architecture/) pro celou architektonickou
baseline (devět technických návrhů + integrační audit +
implementační konvence — všechny se statusem *Architecture baseline —
approved for implementation*).

**Oprava (Fáze 1.3):** `philosophy.md` v repozitáři byl dlouho zastaralá
kopie z Fáze 0 (česká, před anglickým překladem, bez sekcí 2.9–2.11).
Nahrazeno finální schválenou verzí (v1.12.1). Zbylých devět
architektonických dokumentů (`system_state_machine.md`, sedm doménových
návrhů, `implementation_conventions.md`) nikdy nebylo fyzicky součástí
repozitáře vůbec — jen existovaly jako výstupy návrhové konverzace.
Teď žijí v `docs/architecture/`, protože traceability disciplína
(`implementation_conventions.md` Section 16 — "každý PR musí být
odůvodnitelný odkazem na konkrétní sekci") vyžaduje, aby ten dokument
byl skutečně součástí repozitáře, ne externí artefakt.

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

**Fáze 1.1 — Clock.** Hotovo:

- `infrastructure/clock.py` — `Clock` (Protocol), `SystemClock`
  (produkční implementace), `FrozenClock` (testovací implementace se
  `advance()`/`set()`)
- `tests/infrastructure/test_clock.py` — 20 testů, včetně strážního
  testu, který mechanicky kontroluje, že žádný produkční kód mimo
  `infrastructure/clock.py` nevolá `datetime.now()`/`datetime.utcnow()`
  přímo
- `infrastructure/README.md` — proč `Clock` existuje, jak souvisí s
  architektonickou baseline, a co (vědomě) zatím chybí

Poznámka ke stylu: `infrastructure/` a jeho testy jsou psané anglicky
(komentáře i docstringy), na rozdíl od zbytku Fáze 0 — reflektuje to
rozhodnutí "English jako kanonický jazyk projektu" přijaté během
návrhové fáze (viz `philosophy.md`). Fáze 0 se zpětně nepřepisuje, nový
kód od Fáze 1 dál už anglicky je.

**Known follow-up:** *(vyřešeno ve Fázi 1.2, viz níže)*

**Fáze 1.2 — Database wrapper a transakční hranice.** Hotovo:

- `infrastructure/database.py` — `Database` (sdílený transakční core:
  connection management, pragmy, `transaction()`), `Transaction` (jen
  `execute/executemany/fetch_one/fetch_all` — žádné doménové metody),
  `NestedTransactionError` (nesting je explicitně zakázán, ne nejasný),
  `apply_transition()` (load→validate→write→events→commit; `events`
  slot připravený na budoucí outbox beze změny volajících míst),
  `raw_connection()` (zdokumentovaná výjimka pro migrace přes
  `executescript()`, které má jiné commit chování)
- `database/database.py` — přestavěno na kompozici nad
  `infrastructure.database.Database` místo vlastního connection
  managementu; nová atomická metoda `record_rule_change_with_consent()`
  (Rule + ConsentRecord v jedné transakci, `philosophy.md` 2.5) jako
  reálná ukázka `apply_transition()` přes dvě různé tabulky
- `database/models.py` — `utc_now()` odstraněno, `created_at` je teď
  povinný `kw_only` konstruktorový parametr na všech 8 dataclassech s
  timestampem — model už nikdy sám negeneruje svůj vznik
- `database/backup.py` — `now: datetime` explicitní parametr všude
  místo přímého `datetime.now()`
- `bot/discord_bot.py` — injektuje `SystemClock`, `created_at` se
  dodává explicitně při konstrukci `ConversationMessage`
- `tests/infrastructure/test_database.py`, `tests/database/` (nové) —
  42 + 56 testů; `KNOWN_PRE_CLOCK_VIOLATIONS` je teď prázdná množina —
  **nula produkčních výjimek** ze strážního testu

Důležité vyjasnění (architektonické review): `Clock` neřeší "čas do
odemčení" — o odemčení rozhoduje stav domény, Chaster je až následná
technická integrace. `Clock` je jednotný zdroj času jen pro interní
události systému (vznik incidentu, Penalty Window, cooldowny, platnost
autorizace, historie, pořadí událostí).

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

Pro vývoj a spouštění testů:

```bash
pip install -r requirements-dev.txt
pytest
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
infrastructure/  # sdílená cross-cutting vrstva (Clock, Database, Outbox;
                 # consumer framework, startup orchestrator přijdou dál)
trust_manager/   # první doménový modul (Slice 1+2 — viz trust_manager/README.md)
penalty_engine/  # druhý doménový modul (Slice 1 + Extension — viz penalty_engine/README.md)
recovery_plan/   # třetí doménový modul (viz recovery_plan/README.md)
system/          # composition layer: startup orchestrator, cross-module wiring (viz system/README.md)
docs/architecture/  # architektonická baseline: system_state_machine.md,
                     # sedm doménových návrhů, implementation_conventions.md,
                     # domain_events_catalog.md
bot/             # discord_bot.py, approval_flow.py (Fáze 6)
integrations/    # chaster.py, apple_health.py (Fáze 7)
observations/    # audit export (write-only z pohledu runtime — Fáze 3+)
tests/           # pytest, struktura zrcadlí balíčky (tests/infrastructure/, tests/trust_manager/, ...)
philosophy.md    # referenční principy projektu — čti první
```

## Další kroky (Fáze 1 — Infrastructure)

Podle `implementation_conventions.md` (Architecture Baseline) a
`system_state_machine.md` Section 7:

1. ~~`infrastructure/clock.py` — `Clock`, `SystemClock`, `FrozenClock`~~ **hotovo**
2. ~~Database wrapper (transakční `apply_transition` helper)~~ **hotovo**
3. ~~`domain_events` schéma + transactional outbox (claim/publish)~~ **hotovo (Fáze 1.4)**
4. ~~Consumer framework (dispatch podle `event_type`)~~ **hotovo (Fáze 2.4)**
5. ~~Startup orchestrator (`on_system_startup()`, `system_startup_lease`)~~ **hotovo (Fáze 2.4)**
6. ~~Trust Manager Slice 1 (Domain Registry, Incident, Confirmation, Severity)~~ **hotovo (Fáze 2.1)**
7. ~~Trust Manager Slice 2 (score recalculation pipeline)~~ **hotovo (Fáze 2.2)**
8. ~~Penalty Engine Slice 1 (state machine, freeze-as-set-of-reasons, natural completion)~~ **hotovo (Fáze 2.3)**
9. ~~Extension (`should_extend()`, sjednocená konzumační cesta)~~ **hotovo (Fáze 2.5)**
10. ~~Recovery Plan (lifecycle jako reakce na Penalty Window eventy)~~ **hotovo (Fáze 2.6)**

Projekt teď sleduje konzistentní pořadí: Philosophy → Infrastructure →
Trust Manager → Penalty Engine → Extension → System Composition Layer →
Recovery Plan. Tři doménové moduly (Trust Manager, Penalty Engine,
Recovery Plan) a jedna kompoziční vrstva (`system/`) jsou hotové a
navzájem propojené přes reálné, otestované eventy. Další v pořadí:
**Recovery Credit integrace** do Penalty Engine (spotřebuje
`recovery_plan.task_completed`, dokončí `record_recovery_credit_from_task_completion()`
z `penalty_window_technical_design.md` Section 3.4), nebo **Goal
Manager**, který je na zbytku systému nezávislý.

**Fáze 2.5 — Extension.** Hotovo (viz `penalty_engine/README.md`):

- `database/migrations/007_extension.sql` — `extension_decisions` tabulka
  + `incident_consumption.rule_group_id` sloupec (aditivní migrace)
- `penalty_engine/extension.py` — čisté funkce `should_extend()` (4 fáze:
  Eligibility → Base Magnitude → Mitigation → Capacity Cap), s explicitně
  flagovanými vlastními defaulty pro všechny 4 TBD parametry z dokumentu
- **Sjednocená konzumační cesta** – `start_window_if_eligible()` a
  event-driven konzument teď volají stejnou
  `_consume_confirmed_incident_in_transaction()`: první nespotřebovaný
  Incident založí okno (pokud žádné neběží), každý další (včetně toho
  prvního) prochází `should_extend()` – konzumace je bezpodmínečná
  (`philosophy.md` 3.8), Extension samotná je podmíněná
- **`incident.confirmation_changed` payload rozšířen** o `rule_group_id`,
  `intrinsic_severity`, `cooperation_*` – stejná lekce z Fáze 2.4
  (žádný zpětný dotaz na Trust Manager zevnitř transakce konzumenta)
- **Reálný nález**: s výchozí (nízkou) cooperation je i izolovaný MINOR
  incident způsobilý pro Extension – to není bug testu, je to přesně to,
  co `philosophy.md` 2.1/3.8 předpovídá (spolupráce se musí prokázat,
  ne předpokládat)
- 26 nových testů (20 čistých funkcí podle ET1–ET16 z dokumentu + 6
  integračních, včetně capacity cap v reálné DB)

**Fáze 2.6 — Recovery Plan.** Hotovo (viz `recovery_plan/README.md`):

- `database/migrations/008_recovery_plan.sql` — `recovery_plans`,
  `recovery_tasks`, `recovery_task_completions`
- `recovery_plan/models.py`, `recovery_plan/repository.py` –
  `RecoveryPlanManager`: celý lifecycle jako čistá reakce na Penalty
  Window eventy (started→create, frozen/resumed/completed→mirror,
  target_duration_changed→regenerate s expirací starých PROPOSED/ACCEPTED
  tasků, COMPLETED tasky a jejich completions nedotčené – RP-4), plus
  Coach-facing task management (propose/accept/complete/withdraw)
- **Druhá nezávislá cross-module vazba** (Penalty Engine → Recovery
  Plan) – stejná disciplína jako Trust Manager → Penalty Engine, ale
  **beze změny payloadu** – `penalty_window.*` eventy už měly vše
  potřebné, což je samo o sobě důkaz, že se disciplína ustálila
- **Reálný nález**: `process_pending_events()` zpracovávala jen jednu
  dávku eventů existující před začátkem dispatch – pokud handler sám
  publikoval nový event (Penalty Engine reagující na Trust Manager),
  ten čekal na *další* volání `on_system_startup()`. Bez běžící
  publisher smyčky (ta je pořád odložená) by to znamenalo čekat na
  další restart procesu. Opraveno: `process_pending_events()` teď
  drénuje celou kaskádu v rámci jednoho volání (`max_cascade_rounds`
  jako bezpečnostní limit)
- 21 nových testů Recovery Plan + rozšíření `tests/system/test_startup.py`
  o end-to-end řetězec Incident → Penalty Window → Recovery Plan,
  ověřený po **jednom** volání `on_system_startup()`, ne dvou

**Fáze 2.4 — Consumer Framework + Startup Orchestrator.** Hotovo (viz
`system/README.md` pro klíčové architektonické zjištění):

- `infrastructure/consumer_registry.py` — `ConsumerRegistry` (mapování
  `event_type` → handler) + `process_pending_events()` (claim → dispatch
  → mark published, nad existujícími primitivy z `infrastructure/outbox.py`)
- `infrastructure/startup_lease.py` — restart-safe DB lease (LEASE-1),
  `system_startup_lease` tabulka (migrace 006)
- `system/startup.py` — `on_system_startup()` (Trust Manager recovery →
  Penalty Engine recovery → outbox publisher, přesně podle
  `system_state_machine.md` Section 7; kroky 3–6 vynechány, protože
  příslušné moduly ještě neexistují — žádné placeholdery)
- **Skutečné, reálně fungující propojení** Trust Manager →
  Penalty Engine přes `incident.confirmation_changed` – `PenaltyWindow`
  teď vzniká čistě přes event, ne přímým voláním
- **Reálný architektonický nález při implementaci**: `TrustManager` a
  `PenaltyEngine` sdílející stejné `core` sdílí i jeho
  single-transaction guard – handler uvnitř `consume_event()` nemůže
  volat druhý modul, který by otevřel vlastní transakci
  (`NestedTransactionError`). Řešení: payload eventu musí nést vše,
  co konzument potřebuje (přidáno `trust_domain`), a konzument nikdy
  nevolá veřejné API jiného modulu zevnitř své transakce – nová metoda
  `_consume_confirmed_incident_in_transaction()` pracuje čistě
  nad dodanou transakcí (přejmenováno a rozšířeno ve Fázi 2.5, kdy
  Extension sjednotila start i extend do jedné konzumační cesty)
- 26 nových testů (5 lease + 8 registry + 8 end-to-end integrace +
  regresní oprava), včetně testu dokazujícího, že `PenaltyWindow` vzniká
  čistě přes reálné event wiring, ne přímým voláním

**Fáze 2.2 — Trust Manager, Slice 2 (recalculation pipeline).** Hotovo:

- `database/migrations/004_trust_recalculation.sql` — `trust_recalculations`
  + `trust_recalculation_evidence` (TI4: `UNIQUE(evidence_id)` — evidence
  spotřebována nejvýš jednou, navždy)
- `trust_manager/recalculation.py` — čisté funkce bez DB závislosti:
  `effective_weight()` (3.3/TI9, capped), `apply_recalculation()`
  (3.5/TI19 — jedna position nikdy nepohne skóre o víc než
  `MAX_ABSOLUTE_DELTA_PER_RECALCULATION`, ani pro CRITICAL Incident),
  `compute_confidence()` (3.6, diminishing returns podle objemu evidence
  v rolling window)
- `trust_manager/repository.py` — `TrustDomainState.score`/`confidence`
  se teď skutečně mění; `confirm_incident()` spouští 'incident' trigger
  přepočtu ve **stejné transakci** (evidence a její spotřeba vznikají ze
  stejného volání, žádná mezera pro crash recovery navíc není potřeba)
- **Dvě konstanty, které dokument nezadává přesným číslem** (`MAX_ABS_EFFECTIVE_WEIGHT`,
  `CONFIDENCE_K`) – explicitně označeny jako vlastní rozumný default, ne
  tiše předstírané jako už rozhodnuté architekturou (viz `trust_manager/README.md`)
- 21 nových testů (14 čistých funkcí + 7 integračních) – včetně ověření,
  že opakovaný přepočet nikdy znovu nespotřebuje stejnou evidenci

Trust Manager má teď uzavřený celý životní cyklus důvěry (evidence →
přepočet → nové skóre), s výjimkou dvou triggerů (`window_completion`,
`scheduled_review`), které čekají na moduly, jež ještě neexistují. Další
krok: **Penalty Engine**, který už může stavět na stabilním
`get_incident_assessment()`/`get_confirmed_incidents_since()` API, aniž
by řešil, jak se důvěra počítá.

**Fáze 2.3 — Penalty Engine, Slice 1.** Hotovo (viz `penalty_engine/README.md`
pro přesné vymezení rozsahu vs. co je odloženo):

- `database/migrations/005_penalty_engine.sql` — `penalty_windows`,
  `freeze_periods`, `incident_consumption` (jen tabulky skutečně nové
  tomuto modulu — `domain_events`/`trust_domains` už existují z
  předchozích fází)
- `penalty_engine/window.py` — čisté funkce: `target_active_hours()`
  (I5: `min(base+extensions, 336)`), `active_hours_elapsed()` (I6:
  zamrzlý čas se nikdy nepočítá; downtime se počítá pro ACTIVE okno –
   obojí "zadarmo" jen z porovnání dvou absolutních časových razítek),
  `is_complete()`
- `penalty_engine/repository.py` — `PenaltyEngine` třída. `freeze()`/
  `resume()` implementují "freeze jako množina souběžných důvodů"
  (2.3/I22) – druhý souběžný freeze nemění stav ani čas, resume
  reaktivuje jen když poslední důvod zmizí. `emergency_freeze()` jako
  samostatná minimální funkce (2.4/I16 – žádná závislost na
  coach_engine/keyholder_engine, ověřeno strukturálně). `ensure_current_state()`
  řeší 4.4+4.5 najednou (expirace freeze + natural completion, obojí
  proti stejnému `now`)
- Veřejné read API (2.5, 2.6): `get_authorization_freeze_state()`,
  `get_penalty_window_relevant_domains()`
- **Odloženo** (žádný placeholder): `extend()`/`should_extend()`
  (patří Extension modulu), Recovery Credit integrace (patří Recovery
  Plan modulu), `terminate()` (odloženo už samotným architektonickým
  dokumentem, ne jen touhle fází), skuteční volající pro
  `partnered_intimacy_authorization`/`temporary_wear_exemption` důvody
  (mechanismus existuje a je otestovaný, ale Activity Authorization a
  Coach engine, které by ho volaly, ještě neexistují)
- 50 nových testů (13 čistých funkcí + 37 integračních, včetně reálné
  vazby na Trust Manager, ne mock)

**Fáze 1.4 — Transactional Outbox.** Hotovo:

- `database/migrations/002_domain_events.sql` — `domain_events` +
  `domain_event_consumers` schéma (schéma odděleno od chování, stejný
  vzor jako `infrastructure/database.py`)
- `infrastructure/outbox.py` — `DomainEvent`, `write_event()` (zápis
  uvnitř existující transakce), `claim_pending_events()`/`mark_published()`
  (claim/publish s ochranou proti souběžnému claimu dvou publisherů),
  `has_been_processed()`/`mark_processed()` (consumer dedup),
  `consume_event()` (postaveno přímo na `apply_transition()`, ne
  paralelní implementace)
- `database/database.py` — `record_rule_change_with_consent()` teď
  reálně používá `events=` slot (`consent_log.rule_change_recorded`) —
  poctivě označeno jako Fáze 0 demonstrace, ne katalogový event
- **Finding 6 uzavřen** (`domain_events_catalog.md`) — všechny eventy
  jdou přes sdílený outbox bez výjimky, jednoduchost před minimalismem
- 19 nových testů (`tests/infrastructure/test_outbox.py`) + regresní
  aktualizace (`migrate()` teď aplikuje 2 migrace)

**Fáze 2.1 — Trust Manager, Slice 1.** Hotovo (viz `trust_manager/README.md`
pro přesné vymezení rozsahu vs. co je odloženo):

- `database/migrations/003_trust_manager.sql` — Domain Registry, Domain
  State, Incident/Confirmation/Severity model, TrustEvidence
- `trust_manager/models.py` — všechny dataclassy a enumy ze sekcí
  2.1, 2.2, 2.4, 2.8, 2.10
- `trust_manager/severity.py` — deterministický `assess_severity()`
  rubric (TI5: signatura nesmí přijmout trust_score/TrustDomainState/
  CooperationAssessment), `cooperation_trust_offset()`, všechny váhy
  jako pojmenované `critical_change` konstanty
- `trust_manager/repository.py` — `TrustManager` třída, kompozičně nad
  `infrastructure.database.Database`, stejný vzor jako
  `database/database.py`. `confirm_incident()` implementuje atomickou
  opravu TI23/14.2 (ConfirmationRecord + Incident update + assess_severity +
  TrustEvidence, vše v jedné transakci). Veřejné read API (13):
  `get_incident_assessment()`, `get_confirmed_incidents_since()`.
  Crash recovery (14.3): `recover_trust_manager_state()`, idempotentní
- **Odloženo do další dávky** (žádný placeholder kód, prostě zatím
  neexistuje): TrustEvidenceDispute, score recalculation pipeline
  (`TrustDomainState.score` se zatím NEMĚNÍ na základě evidence),
  OverallTrustReport, Goal Accountability Assessment integrace,
  `should_extend()`/ExtensionContext
- 29 nových testů (`tests/trust_manager/test_repository.py`)

**Fáze 1.3 — Domain Events Catalog a náprava dokumentace.** Hotovo:

- `philosophy.md` nahrazeno finální verzí (v1.12.1); `docs/architecture/`
  založeno se všemi devíti zbylými architektonickými dokumenty
- `docs/architecture/domain_events_catalog.md` — konsolidovaný katalog
  všech eventů napříč sedmi moduly (publish/listen mapa), sestavený
  přímo z existujících "Domain Events" sekcí, ne nově navržený
- **5 reálných nesrovnalostí mezi dokumenty nalezeno a zdokumentováno**
  (ne opraveno — čeká na rozhodnutí): chybějící `incident.confirmed`
  event, sporný zdroj `activity_authorization.freeze_confirmed`,
  zastaralý název modulu u `recovery_plan.*` eventů, `recovery_engine.*`
  prefix bez existujícího modulu toho jména, `emergency_override.triggered`
  bez jediného vlastníka
- Policy Engine myšlenka (z dnešní Verification diskuze) zaregistrována
  jako poznámka pro budoucnost, záměrně nenavrhována teď

Podle plánu je dalším krokem buď vyřešení nálezů z katalogu, nebo
rovnou Fáze 1.4 (transactional outbox), až budou eventy ustálené.
