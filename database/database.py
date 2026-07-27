"""
database/database.py

Přístupová vrstva nad SQLite. Zodpovídá za:
  - aplikaci migrací (sekvenční .sql soubory v database/migrations/),
  - převod mezi dataclass modely (database/models.py) a DB řádky,
    včetně (de)serializace *_json sloupců.

Fáze 1.2: samotné otevírání connections a transakční hranice (BEGIN/
COMMIT/ROLLBACK) už tahle třída nedělá sama — deleguje na
infrastructure.database.Database (viz infrastructure/README.md).
Tahle třída je repository nad sdílenou transakční vrstvou, ne
paralelní implementace connection managementu. Žádná metoda zde
nevolá commit()/rollback() přímo — to zůstává výhradně v
infrastructure.database.Database.transaction().

Použití:
    from database.database import Database
    from infrastructure.clock import SystemClock

    clock = SystemClock()
    db = Database("data/coach_keyholder.db")
    db.migrate(now=clock.now())

    snapshot_id = db.save_context_snapshot(snapshot)
    snapshot = db.get_context_snapshot(snapshot_id)

Vědomě NEpoužíváme ORM (viz odůvodnění v konverzaci k Fázi 0) — hybridní
schéma (normalizovaná pole + JSON) je s raw sqlite3 přímočařejší a čitelnější
pro audit, než by bylo přes abstrakci navíc.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from database import backup as backup_module
from database.models import (
    ApprovalStatus,
    CoachAssessment,
    ConsentAction,
    ConsentRecord,
    ConsentTargetType,
    ContextFactor,
    ContextSnapshot,
    ConversationMessage,
    CreatedBy,
    DecisionResult,
    FactorSource,
    ImpactScore,
    KeyholderAssessment,
    MessageRole,
    ObservationRecord,
    ObservationType,
    RelevantPattern,
    ResolutionMethod,
    RewardState,
    RiskDirection,
    Rule,
    TrustState,
    iso,
    parse_iso,
)
from infrastructure.database import Database as CoreDatabase
from infrastructure.database import Transaction, apply_transition
from infrastructure.outbox import DomainEvent, write_event

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_BACKUP_RETENTION = 14


class Database:
    def __init__(
        self,
        db_path: str | Path,
        backup_dir: str | Path | None = None,
        backup_retention: int = DEFAULT_BACKUP_RETENTION,
        *,
        core: CoreDatabase | None = None,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # `core` injectable for tests that want several Database (repository)
        # facades sharing one infrastructure.database.Database/connection
        # policy — normal production use just lets this construct its own.
        self._core = core if core is not None else CoreDatabase(self.db_path)

        # Zálohy defaultně vedle databáze, ve stejné (gitignored) data/ složce,
        # takže updaty zdrojového kódu se jich nedotknou (viz .gitignore).
        self.backup_dir = Path(backup_dir) if backup_dir else self.db_path.parent / "backups"
        self.backup_retention = backup_retention

    # -------------------------------------------------------------------
    # Migrations
    # -------------------------------------------------------------------

    def migrate(self, now: datetime) -> list[int]:
        """
        Aplikuje všechny dosud neaplikované migrace z database/migrations/
        v pořadí podle čísla v názvu souboru (001_, 002_, ...).
        Vrací seznam čísel verzí, které byly nově aplikovány.

        Pravidlo pro autory migrací: migrace smí pouze přidávat (CREATE TABLE
        IF NOT EXISTS, ALTER TABLE ADD COLUMN, nové indexy). Nikdy nesmí
        obsahovat DROP TABLE/COLUMN ani jinou destruktivní operaci nad daty
        — update programu nikdy nesmí vyžadovat ani způsobit ztrátu
        uživatelských dat (viz database/migrations/README.md).

        Pokud databázový soubor už existuje a existují nové migrace k
        aplikaci, vytvoří se nejdřív záloha (reason='pre_migration'),
        bez ohledu na to, jestli dnes už proběhla denní záloha — migrace
        je rizikový okamžik a zaslouží si vlastní zálohu navíc.

        `now` (timezone-aware UTC, z injektovaného Clocku) se použije jen
        pro případnou pre_migration zálohu — použití `raw_connection()`,
        ne `transaction()`, je záměrné: `executescript()` má vlastní
        (implicitní) commit chování neslučitelné s naším BEGIN IMMEDIATE
        (viz infrastructure/database.py, `raw_connection()` docstring).
        """
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

        with self._core.raw_connection() as conn:
            current_version = 0
            try:
                row = conn.execute(
                    "SELECT MAX(version) as v FROM schema_version"
                ).fetchone()
                if row and row["v"] is not None:
                    current_version = row["v"]
            except sqlite3.OperationalError:
                pass  # tabulka schema_version zatím neexistuje -> první migrace ji založí

            pending = [
                path for path in migration_files
                if int(path.name.split("_")[0]) > current_version
            ]

        if not pending:
            return []

        # Záloha před migrací — jen pokud DB už měla dřív aplikovanou nějakou
        # verzi schématu (current_version > 0). Na úplně prvním spuštění
        # SQLite vytvoří prázdný soubor už při připojení výše, ale zálohovat
        # prázdnou databázi bez dat nemá smysl, proto se rozlišuje podle
        # current_version, ne podle pouhé existence souboru.
        if current_version > 0:
            self.create_backup(reason="pre_migration", now=now)

        applied: list[int] = []
        with self._core.raw_connection() as conn:
            for path in pending:
                version = int(path.name.split("_")[0])
                sql = path.read_text(encoding="utf-8")
                conn.executescript(sql)
                applied.append(version)

        return applied

    # -------------------------------------------------------------------
    # Backups
    # -------------------------------------------------------------------

    def create_backup(self, reason: str, now: datetime) -> Path | None:
        """Vytvoří zálohu a rovnou aplikuje rotační politiku."""
        path = backup_module.create_backup(self.db_path, self.backup_dir, reason, now=now)
        backup_module.rotate_backups(self.backup_dir, keep=self.backup_retention)
        return path

    def ensure_daily_backup(self, now: datetime) -> Path | None:
        """
        Zaručí max. 1 automatickou zálohu za den. Volat při startu aplikace
        (nezávisle na tom, jestli proběhly migrace — pokrývá i běžný denní
        provoz bez schematických změn).
        """
        path = backup_module.ensure_daily_backup(self.db_path, self.backup_dir, now=now)
        if path is not None:
            backup_module.rotate_backups(self.backup_dir, keep=self.backup_retention)
        return path

    # -------------------------------------------------------------------
    # Context Snapshot
    # -------------------------------------------------------------------

    def save_context_snapshot(self, snap: ContextSnapshot) -> str:
        with self._core.transaction() as tx:
            tx.execute(
                """
                INSERT INTO context_snapshots
                    (id, created_at, engine_version, overall_confidence,
                     data_freshness_hours, context_factors_json, relevant_patterns_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap.id,
                    iso(snap.created_at),
                    snap.engine_version,
                    snap.overall_confidence,
                    snap.data_freshness_hours,
                    json.dumps([f.to_dict() for f in snap.context_factors]),
                    json.dumps([p.to_dict() for p in snap.relevant_patterns]),
                ),
            )
        return snap.id

    def get_context_snapshot(self, snapshot_id: str) -> ContextSnapshot | None:
        with self._core.transaction() as tx:
            row = tx.execute(
                "SELECT * FROM context_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        if row is None:
            return None
        return ContextSnapshot(
            id=row["id"],
            created_at=parse_iso(row["created_at"]),
            engine_version=row["engine_version"],
            overall_confidence=row["overall_confidence"],
            data_freshness_hours=row["data_freshness_hours"],
            context_factors=[
                ContextFactor.from_dict(d) for d in json.loads(row["context_factors_json"])
            ],
            relevant_patterns=[
                RelevantPattern.from_dict(d) for d in json.loads(row["relevant_patterns_json"])
            ],
        )

    # -------------------------------------------------------------------
    # Coach Assessment
    # -------------------------------------------------------------------

    def save_coach_assessment(self, a: CoachAssessment) -> str:
        with self._core.transaction() as tx:
            tx.execute(
                """
                INSERT INTO coach_assessments
                    (id, created_at, engine_version, context_snapshot_id,
                     recommendation, reasoning, confidence, risk_direction,
                     sustainability_score, supporting_factors_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    a.id,
                    iso(a.created_at),
                    a.engine_version,
                    a.context_snapshot_id,
                    a.recommendation,
                    a.reasoning,
                    a.confidence,
                    a.risk_direction.value,
                    a.sustainability_score,
                    json.dumps(a.supporting_factors),
                ),
            )
        return a.id

    def get_coach_assessment(self, assessment_id: str) -> CoachAssessment | None:
        with self._core.transaction() as tx:
            row = tx.execute(
                "SELECT * FROM coach_assessments WHERE id = ?", (assessment_id,)
            ).fetchone()
        if row is None:
            return None
        return CoachAssessment(
            id=row["id"],
            created_at=parse_iso(row["created_at"]),
            engine_version=row["engine_version"],
            context_snapshot_id=row["context_snapshot_id"],
            recommendation=row["recommendation"],
            reasoning=row["reasoning"],
            confidence=row["confidence"],
            risk_direction=RiskDirection(row["risk_direction"]),
            sustainability_score=row["sustainability_score"],
            supporting_factors=json.loads(row["supporting_factors_json"]),
        )

    # -------------------------------------------------------------------
    # Keyholder Assessment
    # -------------------------------------------------------------------

    def save_keyholder_assessment(self, a: KeyholderAssessment) -> str:
        with self._core.transaction() as tx:
            tx.execute(
                """
                INSERT INTO keyholder_assessments
                    (id, created_at, engine_version, context_snapshot_id,
                     recommendation, reasoning, confidence, consistency_score,
                     trust_state_json, reward_state_json, rule_relevance_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    a.id,
                    iso(a.created_at),
                    a.engine_version,
                    a.context_snapshot_id,
                    a.recommendation,
                    a.reasoning,
                    a.confidence,
                    a.consistency_score,
                    json.dumps(a.trust_state.__dict__),
                    json.dumps(a.reward_state.__dict__),
                    json.dumps(a.rule_relevance),
                ),
            )
        return a.id

    def get_keyholder_assessment(self, assessment_id: str) -> KeyholderAssessment | None:
        with self._core.transaction() as tx:
            row = tx.execute(
                "SELECT * FROM keyholder_assessments WHERE id = ?", (assessment_id,)
            ).fetchone()
        if row is None:
            return None
        return KeyholderAssessment(
            id=row["id"],
            created_at=parse_iso(row["created_at"]),
            engine_version=row["engine_version"],
            context_snapshot_id=row["context_snapshot_id"],
            recommendation=row["recommendation"],
            reasoning=row["reasoning"],
            confidence=row["confidence"],
            consistency_score=row["consistency_score"],
            trust_state=TrustState(**json.loads(row["trust_state_json"])),
            reward_state=RewardState(**json.loads(row["reward_state_json"])),
            rule_relevance=json.loads(row["rule_relevance_json"]),
        )

    def record_trust_history(
        self, trust_score: float, reason: str, now: datetime, keyholder_assessment_id: str | None = None
    ) -> str:
        from database.models import new_id

        record_id = new_id()
        with self._core.transaction() as tx:
            tx.execute(
                """
                INSERT INTO trust_history (id, recorded_at, trust_score, reason, keyholder_assessment_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (record_id, iso(now), trust_score, reason, keyholder_assessment_id),
            )
        return record_id

    # -------------------------------------------------------------------
    # Decision Result
    # -------------------------------------------------------------------

    def save_decision_result(self, d: DecisionResult) -> str:
        with self._core.transaction() as tx:
            tx.execute(
                """
                INSERT INTO decision_results
                    (id, created_at, engine_version, context_snapshot_id,
                     coach_assessment_id, keyholder_assessment_id, final_decision,
                     resolution_method, impact_score, impact_is_significant,
                     impact_factors_json, is_critical_change, requires_user_approval,
                     safety_override, explanation, approval_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    d.id,
                    iso(d.created_at),
                    d.engine_version,
                    d.context_snapshot_id,
                    d.coach_assessment_id,
                    d.keyholder_assessment_id,
                    d.final_decision,
                    d.resolution_method.value,
                    d.impact_score.value,
                    int(d.impact_score.is_significant),
                    json.dumps(d.impact_score.contributing_factors),
                    int(d.is_critical_change),
                    int(d.requires_user_approval),
                    int(d.safety_override),
                    d.explanation,
                    d.approval_status.value,
                ),
            )
        return d.id

    def get_decision_result(self, decision_id: str) -> DecisionResult | None:
        with self._core.transaction() as tx:
            row = tx.execute(
                "SELECT * FROM decision_results WHERE id = ?", (decision_id,)
            ).fetchone()
        if row is None:
            return None
        return DecisionResult(
            id=row["id"],
            created_at=parse_iso(row["created_at"]),
            engine_version=row["engine_version"],
            context_snapshot_id=row["context_snapshot_id"],
            coach_assessment_id=row["coach_assessment_id"],
            keyholder_assessment_id=row["keyholder_assessment_id"],
            final_decision=row["final_decision"],
            resolution_method=ResolutionMethod(row["resolution_method"]),
            impact_score=ImpactScore(
                value=row["impact_score"],
                is_significant=bool(row["impact_is_significant"]),
                contributing_factors=json.loads(row["impact_factors_json"]),
            ),
            is_critical_change=bool(row["is_critical_change"]),
            requires_user_approval=bool(row["requires_user_approval"]),
            safety_override=bool(row["safety_override"]),
            explanation=row["explanation"],
            approval_status=ApprovalStatus(row["approval_status"]),
        )

    def set_decision_approval_status(self, decision_id: str, status: ApprovalStatus) -> None:
        with self._core.transaction() as tx:
            tx.execute(
                "UPDATE decision_results SET approval_status = ? WHERE id = ?",
                (status.value, decision_id),
            )

    def get_pending_approvals(self) -> list[DecisionResult]:
        """Rozhodnutí čekající na schválení uživatele — pro Discord approval_flow."""
        with self._core.transaction() as tx:
            rows = tx.execute(
                "SELECT id FROM decision_results WHERE approval_status = ?",
                (ApprovalStatus.PENDING.value,),
            ).fetchall()
        return [self.get_decision_result(r["id"]) for r in rows]

    # -------------------------------------------------------------------
    # Observations (runtime pouze zapisuje — viz observations/ modul)
    # -------------------------------------------------------------------

    def save_observation(self, o: ObservationRecord) -> str:
        with self._core.transaction() as tx:
            tx.execute(
                """
                INSERT INTO observations
                    (id, created_at, observation_type, related_decision_id,
                     description, raw_data_json, flagged_for_review)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    o.id,
                    iso(o.created_at),
                    o.observation_type.value,
                    o.related_decision_id,
                    o.description,
                    json.dumps(o.raw_data),
                    int(o.flagged_for_review),
                ),
            )
        return o.id

    def get_unreviewed_observations(self) -> list[ObservationRecord]:
        """Používá výhradně audit export nástroj (observations/export.py), ne runtime."""
        with self._core.transaction() as tx:
            rows = tx.execute(
                "SELECT * FROM observations WHERE reviewed_at IS NULL ORDER BY created_at"
            ).fetchall()
        result = []
        for row in rows:
            result.append(
                ObservationRecord(
                    id=row["id"],
                    created_at=parse_iso(row["created_at"]),
                    observation_type=ObservationType(row["observation_type"]),
                    related_decision_id=row["related_decision_id"],
                    description=row["description"],
                    raw_data=json.loads(row["raw_data_json"]),
                    flagged_for_review=bool(row["flagged_for_review"]),
                    reviewed_at=parse_iso(row["reviewed_at"]) if row["reviewed_at"] else None,
                    review_notes=row["review_notes"],
                )
            )
        return result

    def mark_observation_reviewed(self, observation_id: str, now: datetime, notes: str | None = None) -> None:
        """Volá výhradně audit export / review nástroj."""
        with self._core.transaction() as tx:
            tx.execute(
                "UPDATE observations SET reviewed_at = ?, review_notes = ? WHERE id = ?",
                (iso(now), notes, observation_id),
            )

    # -------------------------------------------------------------------
    # Rules
    # -------------------------------------------------------------------

    @staticmethod
    def _insert_rule_row(tx: Transaction, rule: Rule) -> None:
        """Sdílená INSERT logika pro rules — použita jak přímo (save_rule),
        tak jako součást větší atomické operace (supersede_rule,
        record_rule_change_with_consent)."""
        tx.execute(
            """
            INSERT INTO rules
                (id, rule_group_id, version, title, description, category,
                 parameters_json, is_active, supersedes_id, created_at,
                 created_by, is_critical)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule.id,
                rule.rule_group_id,
                rule.version,
                rule.title,
                rule.description,
                rule.category,
                json.dumps(rule.parameters),
                int(rule.is_active),
                rule.supersedes_id,
                iso(rule.created_at),
                rule.created_by.value,
                int(rule.is_critical),
            ),
        )

    @staticmethod
    def _insert_consent_row(tx: Transaction, c: ConsentRecord) -> None:
        """Sdílená INSERT logika pro consent_log — použita jak přímo
        (save_consent_record), tak jako součást record_rule_change_with_consent."""
        tx.execute(
            """
            INSERT INTO consent_log
                (id, created_at, target_type, target_id, target_version,
                 action, decision_result_id, explanation_shown, user_comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                c.id,
                iso(c.created_at),
                c.target_type.value,
                c.target_id,
                c.target_version,
                c.action.value,
                c.decision_result_id,
                c.explanation_shown,
                c.user_comment,
            ),
        )

    def save_rule(self, rule: Rule) -> str:
        apply_transition(self._core, write=lambda tx, _state: self._insert_rule_row(tx, rule))
        return rule.id

    def supersede_rule(self, new_rule: Rule) -> str:
        """
        Vytvoří novou verzi pravidla a deaktivuje předchozí verzi ve stejné
        transakci. new_rule.supersedes_id musí být nastaveno na ID předchozí verze.

        Refaktorováno na apply_transition() (infrastructure/database.py) —
        stejné chování jako dřív (dva SQL příkazy v jedné transakci), teď
        postavené na sdíleném generickém helperu místo vlastní ad-hoc
        kompozice.
        """

        def write(tx: Transaction, _state: object) -> str:
            if new_rule.supersedes_id:
                tx.execute(
                    "UPDATE rules SET is_active = 0 WHERE id = ?",
                    (new_rule.supersedes_id,),
                )
            self._insert_rule_row(tx, new_rule)
            return new_rule.id

        return apply_transition(self._core, write=write)

    def record_rule_change_with_consent(self, new_rule: Rule, consent: ConsentRecord) -> tuple[str, str]:
        """
        Atomicky zapíše novou verzi pravidla (případně deaktivuje
        předchozí, stejně jako supersede_rule) SPOLU s jejím
        ConsentRecordem — philosophy.md 2.5 (Consent & Control): změna
        pravidla nikdy není legitimní bez odpovídajícího souhlasu, takže
        tahle metoda existuje právě proto, aby ty dva zápisy nikdy nemohly
        vzniknout nezávisle na sobě (ani při chybě uprostřed).

        Skutečná ukázka apply_transition() přes DVĚ různé tabulky
        (rules + consent_log), ne jen dvakrát stejnou tabulku jako
        supersede_rule.

        Fáze 1.4: první reálné využití `events=` slotu (mimo testy).
        Není to katalogový event z `domain_events_catalog.md` — ten
        katalog popisuje budoucí doménové moduly (Trust Manager,
        Penalty Engine, ...), které ještě neexistují jako kód. Jde o
        poctivě pojmenovanou demonstraci na existující Fázi 0 tabulce,
        ne o předstírání, že `rules`/`consent_log` jsou plnohodnotný
        doménový modul podle katalogu.
        """

        def write(tx: Transaction, _state: object) -> tuple[str, str]:
            if new_rule.supersedes_id:
                tx.execute(
                    "UPDATE rules SET is_active = 0 WHERE id = ?",
                    (new_rule.supersedes_id,),
                )
            self._insert_rule_row(tx, new_rule)
            self._insert_consent_row(tx, consent)
            return (new_rule.id, consent.id)

        def events(tx: Transaction, _state: object, result: tuple[str, str]) -> None:
            rule_id, consent_id = result
            write_event(
                tx,
                DomainEvent(
                    event_type="consent_log.rule_change_recorded",
                    source_module="database",
                    payload={"rule_id": rule_id, "consent_id": consent_id},
                    occurred_at=new_rule.created_at,
                ),
            )

        return apply_transition(self._core, write=write, events=events)

    def get_active_rules(self) -> list[Rule]:
        with self._core.transaction() as tx:
            rows = tx.execute("SELECT * FROM rules WHERE is_active = 1").fetchall()
        return [self._row_to_rule(r) for r in rows]

    def get_rule_history(self, rule_group_id: str) -> list[Rule]:
        with self._core.transaction() as tx:
            rows = tx.execute(
                "SELECT * FROM rules WHERE rule_group_id = ? ORDER BY version",
                (rule_group_id,),
            ).fetchall()
        return [self._row_to_rule(r) for r in rows]

    @staticmethod
    def _row_to_rule(row: sqlite3.Row) -> Rule:
        return Rule(
            id=row["id"],
            rule_group_id=row["rule_group_id"],
            version=row["version"],
            title=row["title"],
            description=row["description"],
            category=row["category"],
            parameters=json.loads(row["parameters_json"]),
            is_active=bool(row["is_active"]),
            supersedes_id=row["supersedes_id"],
            created_at=parse_iso(row["created_at"]),
            created_by=CreatedBy(row["created_by"]),
            is_critical=bool(row["is_critical"]),
        )

    # -------------------------------------------------------------------
    # Consent Log (append-only)
    # -------------------------------------------------------------------

    def save_consent_record(self, c: ConsentRecord) -> str:
        apply_transition(self._core, write=lambda tx, _state: self._insert_consent_row(tx, c))
        return c.id

    def get_consent_history(
        self, target_type: ConsentTargetType, target_id: str | None = None
    ) -> list[ConsentRecord]:
        with self._core.transaction() as tx:
            if target_id is not None:
                rows = tx.execute(
                    "SELECT * FROM consent_log WHERE target_type = ? AND target_id = ? ORDER BY created_at",
                    (target_type.value, target_id),
                ).fetchall()
            else:
                rows = tx.execute(
                    "SELECT * FROM consent_log WHERE target_type = ? ORDER BY created_at",
                    (target_type.value,),
                ).fetchall()
        return [
            ConsentRecord(
                id=r["id"],
                created_at=parse_iso(r["created_at"]),
                target_type=ConsentTargetType(r["target_type"]),
                target_id=r["target_id"],
                target_version=r["target_version"],
                action=ConsentAction(r["action"]),
                decision_result_id=r["decision_result_id"],
                explanation_shown=r["explanation_shown"],
                user_comment=r["user_comment"],
            )
            for r in rows
        ]

    # -------------------------------------------------------------------
    # Conversation messages
    # -------------------------------------------------------------------

    def save_conversation_message(self, m: ConversationMessage) -> str:
        with self._core.transaction() as tx:
            tx.execute(
                """
                INSERT INTO conversation_messages
                    (id, created_at, role, content, discord_channel_id,
                     discord_message_id, related_decision_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    m.id,
                    iso(m.created_at),
                    m.role.value,
                    m.content,
                    m.discord_channel_id,
                    m.discord_message_id,
                    m.related_decision_id,
                ),
            )
        return m.id

    def get_recent_messages(
        self, discord_channel_id: str, limit: int = 20
    ) -> list[ConversationMessage]:
        """Krátkodobá paměť: posledních N zpráv v kanálu, chronologicky."""
        with self._core.transaction() as tx:
            rows = tx.execute(
                """
                SELECT * FROM conversation_messages
                WHERE discord_channel_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (discord_channel_id, limit),
            ).fetchall()
        messages = [
            ConversationMessage(
                id=r["id"],
                created_at=parse_iso(r["created_at"]),
                role=MessageRole(r["role"]),
                content=r["content"],
                discord_channel_id=r["discord_channel_id"],
                discord_message_id=r["discord_message_id"],
                related_decision_id=r["related_decision_id"],
            )
            for r in rows
        ]
        return list(reversed(messages))
