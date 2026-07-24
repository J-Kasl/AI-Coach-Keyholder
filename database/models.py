"""
database/models.py

Python dataclass reprezentace datových kontraktů domluvených mezi Context,
Coach, Keyholder a Decision enginem. Tyto třídy jsou "runtime" tvar dat;
(de)serializace do SQLite (viz database.py) převádí vnořené struktury na
JSON stringy pro *_json sloupce podle schématu v migrations/001_initial_schema.sql.

Cíleno na Python 3.13 — používá `from __future__ import annotations`,
`dataclasses`, `enum.StrEnum` a moderní typing (`list[X]`, `X | None`).

Princip: tyto třídy neobsahují žádnou byznys logiku ani validaci nad rámec
tvaru dat. Byznys logika patří do core/*_engine.py.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import StrEnum


# =============================================================================
# Pomocné funkce
# =============================================================================

def new_id() -> str:
    """Generuje nové UUID4 jako string — jednotný formát ID napříč celým systémem."""
    return str(uuid.uuid4())


def utc_now() -> datetime:
    """Aktuální čas v UTC. Používat vždy tohle, ne datetime.now() bez tz."""
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Formátuje datetime do ISO 8601 stringu tak, jak je ukládán v DB (TEXT sloupce)."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """Parsuje ISO 8601 string zpět na timezone-aware datetime (UTC)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# =============================================================================
# Enums
# =============================================================================

class FactorSource(StrEnum):
    APPLE_HEALTH = "apple_health"
    USER_REPORTED = "user_reported"
    INFERRED = "inferred"
    MANUAL_LOG = "manual_log"


class RiskDirection(StrEnum):
    OVERLOAD = "overload"
    STAGNATION = "stagnation"
    NONE = "none"


class ResolutionMethod(StrEnum):
    RULE_BASED = "rule_based"
    WEIGHTED_SCORE = "weighted_score"
    LLM_ARBITRATION = "llm_arbitration"


class ApprovalStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ObservationType(StrEnum):
    DECISION_MADE = "decision_made"
    PERSPECTIVE_CONFLICT = "perspective_conflict"
    UNEXPECTED_OUTCOME = "unexpected_outcome"
    RECURRING_PATTERN = "recurring_pattern"
    ESTIMATION_ERROR = "estimation_error"


class ConsentTargetType(StrEnum):
    RULE = "rule"
    PHILOSOPHY = "philosophy"
    TRUST_ALGORITHM = "trust_algorithm"
    REWARD_ALGORITHM = "reward_algorithm"
    INTEGRATION = "integration"


class ConsentAction(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class CreatedBy(StrEnum):
    USER = "user"
    AI_PROPOSAL = "ai_proposal"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


# =============================================================================
# Context Engine
# =============================================================================

@dataclass
class ContextFactor:
    name: str
    value: float | str | bool
    source: FactorSource
    confidence: float
    observed_at: datetime

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        d["observed_at"] = iso(self.observed_at)
        return d

    @staticmethod
    def from_dict(d: dict) -> ContextFactor:
        return ContextFactor(
            name=d["name"],
            value=d["value"],
            source=FactorSource(d["source"]),
            confidence=d["confidence"],
            observed_at=parse_iso(d["observed_at"]),
        )


@dataclass
class RelevantPattern:
    pattern_id: str
    description: str
    strength: float
    last_confirmed: datetime

    def to_dict(self) -> dict:
        d = asdict(self)
        d["last_confirmed"] = iso(self.last_confirmed)
        return d

    @staticmethod
    def from_dict(d: dict) -> RelevantPattern:
        return RelevantPattern(
            pattern_id=d["pattern_id"],
            description=d["description"],
            strength=d["strength"],
            last_confirmed=parse_iso(d["last_confirmed"]),
        )


@dataclass
class ContextSnapshot:
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)
    engine_version: str = "context_engine@0.1.0"

    context_factors: list[ContextFactor] = field(default_factory=list)
    relevant_patterns: list[RelevantPattern] = field(default_factory=list)
    overall_confidence: float = 0.0
    data_freshness_hours: float = 0.0


# =============================================================================
# Coach Engine
# =============================================================================

@dataclass
class CoachAssessment:
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)
    engine_version: str = "coach_engine@0.1.0"
    context_snapshot_id: str = ""

    recommendation: str = ""
    reasoning: str = ""
    confidence: float = 0.0

    risk_direction: RiskDirection = RiskDirection.NONE
    sustainability_score: float = 0.0
    supporting_factors: list[str] = field(default_factory=list)


# =============================================================================
# Keyholder Engine (+ interní TrustManager / RewardManager stav)
# =============================================================================

@dataclass
class TrustState:
    trust_score: float = 0.5
    recent_trend: str = "stable"          # "improving" | "declining" | "stable"
    consecutive_failures: int = 0
    consecutive_successes: int = 0


@dataclass
class RewardState:
    eligible_rewards: list[str] = field(default_factory=list)
    pending_consequence: str | None = None
    # Reward jako pozitivní zpětná vazba (philosophy.md 2.7): tento seznam
    # se vyhodnocuje nezávisle na "failure streak" logice, ne jako jeho zrcadlo.
    positive_streak_note: str | None = None


@dataclass
class KeyholderAssessment:
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)
    engine_version: str = "keyholder_engine@0.1.0"
    context_snapshot_id: str = ""

    recommendation: str = ""
    reasoning: str = ""
    confidence: float = 0.0

    consistency_score: float = 0.0
    trust_state: TrustState = field(default_factory=TrustState)
    reward_state: RewardState = field(default_factory=RewardState)
    rule_relevance: list[str] = field(default_factory=list)   # rule_group_id hodnoty


# =============================================================================
# Decision Engine
# =============================================================================

@dataclass
class ImpactScore:
    value: float = 0.0
    is_significant: bool = False
    contributing_factors: dict[str, float] = field(default_factory=dict)


@dataclass
class DecisionResult:
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)
    engine_version: str = "decision_engine@0.1.0"

    context_snapshot_id: str = ""
    coach_assessment_id: str = ""
    keyholder_assessment_id: str = ""

    final_decision: str = ""
    resolution_method: ResolutionMethod = ResolutionMethod.RULE_BASED
    impact_score: ImpactScore = field(default_factory=ImpactScore)

    # Dvouvrstvé requires_user_approval — viz philosophy.md 2.5 a database
    # schema komentář: is_critical_change (pevná pravidla) OR impact.is_significant,
    # a bezpečnostní override (philosophy.md 2.3) běží mimo tuto úvahu úplně.
    is_critical_change: bool = False
    safety_override: bool = False
    requires_user_approval: bool = False

    explanation: str | None = None
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED

    def compute_requires_approval(self) -> bool:
        """
        Kanonický výpočet requires_user_approval. Volá se explicitně z
        decision_engine.py po naplnění is_critical_change a impact_score —
        není to automatický property, aby bylo v testech vidět, kdy přesně
        se pole nastavuje.
        """
        return self.is_critical_change or self.impact_score.is_significant or self.safety_override


# =============================================================================
# Observations (write-only z pohledu runtime)
# =============================================================================

@dataclass
class ObservationRecord:
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)

    observation_type: ObservationType = ObservationType.DECISION_MADE
    related_decision_id: str | None = None

    description: str = ""
    raw_data: dict = field(default_factory=dict)
    flagged_for_review: bool = False

    # Vyplňuje výhradně audit export nástroj, nikdy runtime.
    reviewed_at: datetime | None = None
    review_notes: str | None = None


# =============================================================================
# Rules & Consent
# =============================================================================

@dataclass
class Rule:
    id: str = field(default_factory=new_id)
    rule_group_id: str = field(default_factory=new_id)   # nové pravidlo = nové group_id; nová verze = stejné group_id
    version: int = 1
    title: str = ""
    description: str = ""
    category: str = ""
    parameters: dict = field(default_factory=dict)

    is_active: bool = True
    supersedes_id: str | None = None

    created_at: datetime = field(default_factory=utc_now)
    created_by: CreatedBy = CreatedBy.USER
    is_critical: bool = False


@dataclass
class ConsentRecord:
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)

    target_type: ConsentTargetType = ConsentTargetType.RULE
    target_id: str | None = None
    target_version: str | None = None

    action: ConsentAction = ConsentAction.APPROVED
    decision_result_id: str | None = None

    explanation_shown: str | None = None
    user_comment: str | None = None


# =============================================================================
# Conversation (krátkodobá paměť / surový log)
# =============================================================================

@dataclass
class ConversationMessage:
    id: str = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)
    role: MessageRole = MessageRole.USER
    content: str = ""
    discord_channel_id: str | None = None
    discord_message_id: str | None = None
    related_decision_id: str | None = None
