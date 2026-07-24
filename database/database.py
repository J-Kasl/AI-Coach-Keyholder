"""
database/database.py

Přístupová vrstva nad SQLite. Zodpovídá za:
  - správu připojení,
  - aplikaci migrací (sekvenční .sql soubory v database/migrations/),
  - převod mezi dataclass modely (database/models.py) a DB řádky,
    včetně (de)serializace *_json sloupců.

Použití:
    from database.database import Database

    db = Database("data/coach_keyholder.db")
    db.migrate()

    snapshot_id = db.save_context_snapshot(snapshot)
    snapshot = db.get_context_snapshot(snapshot_id)

Vědomě NEpoužíváme ORM (viz odůvodnění v konverzaci k Fázi 0) — hybridní
schéma (normalizovaná pole + JSON) je s raw sqlite3 přímočařejší a čitelnější
pro audit, než by bylo přes abstrakci navíc.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

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

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DEFAULT_BACKUP_RETENTION = 14


class Database:
    def __init__(
        self,
        db_path: str | Path,
        backup_dir: str | Path | None = None,
        backup_retention: int = DEFAULT_BACKUP_RETENTION,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Zálohy defaultně vedle databáze, ve stejné (gitignored) data/ složce,
        # takže updaty zdrojového kódu se jich nedotknou (viz .gitignore).
        self.backup_dir = Path(backup_dir) if backup_dir else self.db_path.parent / "backups"
        self.backup_retention = backup_retention

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -------------------------------------------------------------------
    # Migrations
    # -------------------------------------------------------------------

    def migrate(self) -> list[int]:
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
        """
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

        with self._connect() as conn:
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
            self.create_backup(reason="pre_migration")

        applied: list[int] = []
        with self._connect() as conn:
            for path in pending:
                version = int(path.name.split("_")[0])
                sql = path.read_text(encoding="utf-8")
                conn.executescript(sql)
                applied.append(version)

        return applied

    # -------------------------------------------------------------------
    # Backups
    # -------------------------------------------------------------------

    def create_backup(self, reason: str) -> Path | None:
        """Vytvoří zálohu a rovnou aplikuje rotační politiku."""
        path = backup_module.create_backup(self.db_path, self.backup_dir, reason)
        backup_module.rotate_backups(self.backup_dir, keep=self.backup_retention)
        return path

    def ensure_daily_backup(self) -> Path | None:
        """
        Zaručí max. 1 automatickou zálohu za den. Volat při startu aplikace
        (nezávisle na tom, jestli proběhly migrace — pokrývá i běžný denní
        provoz bez schematických změn).
        """
        path = backup_module.ensure_daily_backup(self.db_path, self.backup_dir)
        if path is not None:
            backup_module.rotate_backups(self.backup_dir, keep=self.backup_retention)
        return path

    # -------------------------------------------------------------------
    # Context Snapshot
    # -------------------------------------------------------------------

    def save_context_snapshot(self, snap: ContextSnapshot) -> str:
        with self._connect() as conn:
            conn.execute(
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
        with self._connect() as conn:
            row = conn.execute(
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
        with self._connect() as conn:
            conn.execute(
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
        with self._connect() as conn:
            row = conn.execute(
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
        with self._connect() as conn:
            conn.execute(
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
        with self._connect() as conn:
            row = conn.execute(
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
        self, trust_score: float, reason: str, keyholder_assessment_id: str | None = None
    ) -> str:
        from database.models import new_id, utc_now

        record_id = new_id()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trust_history (id, recorded_at, trust_score, reason, keyholder_assessment_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (record_id, iso(utc_now()), trust_score, reason, keyholder_assessment_id),
            )
        return record_id

    # -------------------------------------------------------------------
    # Decision Result
    # -------------------------------------------------------------------

    def save_decision_result(self, d: DecisionResult) -> str:
        with self._connect() as conn:
            conn.execute(
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
        with self._connect() as conn:
            row = conn.execute(
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
        with self._connect() as conn:
            conn.execute(
                "UPDATE decision_results SET approval_status = ? WHERE id = ?",
                (status.value, decision_id),
            )

    def get_pending_approvals(self) -> list[DecisionResult]:
        """Rozhodnutí čekající na schválení uživatele — pro Discord approval_flow."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM decision_results WHERE approval_status = ?",
                (ApprovalStatus.PENDING.value,),
            ).fetchall()
        return [self.get_decision_result(r["id"]) for r in rows]

    # -------------------------------------------------------------------
    # Observations (runtime pouze zapisuje — viz observations/ modul)
    # -------------------------------------------------------------------

    def save_observation(self, o: ObservationRecord) -> str:
        with self._connect() as conn:
            conn.execute(
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
        with self._connect() as conn:
            rows = conn.execute(
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

    def mark_observation_reviewed(self, observation_id: str, notes: str | None = None) -> None:
        """Volá výhradně audit export / review nástroj."""
        from database.models import utc_now

        with self._connect() as conn:
            conn.execute(
                "UPDATE observations SET reviewed_at = ?, review_notes = ? WHERE id = ?",
                (iso(utc_now()), notes, observation_id),
            )

    # -------------------------------------------------------------------
    # Rules
    # -------------------------------------------------------------------

    def save_rule(self, rule: Rule) -> str:
        with self._connect() as conn:
            conn.execute(
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
        return rule.id

    def supersede_rule(self, new_rule: Rule) -> str:
        """
        Vytvoří novou verzi pravidla a deaktivuje předchozí verzi ve stejné
        transakci. new_rule.supersedes_id musí být nastaveno na ID předchozí verze.
        """
        with self._connect() as conn:
            if new_rule.supersedes_id:
                conn.execute(
                    "UPDATE rules SET is_active = 0 WHERE id = ?",
                    (new_rule.supersedes_id,),
                )
            conn.execute(
                """
                INSERT INTO rules
                    (id, rule_group_id, version, title, description, category,
                     parameters_json, is_active, supersedes_id, created_at,
                     created_by, is_critical)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_rule.id,
                    new_rule.rule_group_id,
                    new_rule.version,
                    new_rule.title,
                    new_rule.description,
                    new_rule.category,
                    json.dumps(new_rule.parameters),
                    int(new_rule.is_active),
                    new_rule.supersedes_id,
                    iso(new_rule.created_at),
                    new_rule.created_by.value,
                    int(new_rule.is_critical),
                ),
            )
        return new_rule.id

    def get_active_rules(self) -> list[Rule]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM rules WHERE is_active = 1").fetchall()
        return [self._row_to_rule(r) for r in rows]

    def get_rule_history(self, rule_group_id: str) -> list[Rule]:
        with self._connect() as conn:
            rows = conn.execute(
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
        with self._connect() as conn:
            conn.execute(
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
        return c.id

    def get_consent_history(
        self, target_type: ConsentTargetType, target_id: str | None = None
    ) -> list[ConsentRecord]:
        with self._connect() as conn:
            if target_id is not None:
                rows = conn.execute(
                    "SELECT * FROM consent_log WHERE target_type = ? AND target_id = ? ORDER BY created_at",
                    (target_type.value, target_id),
                ).fetchall()
            else:
                rows = conn.execute(
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
        with self._connect() as conn:
            conn.execute(
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
        with self._connect() as conn:
            rows = conn.execute(
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
