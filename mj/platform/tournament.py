"""Formal tournament contracts shared by the runner and its workers.

The objects in this module deliberately contain only token labels and other
public identity facts.  A bearer token is an input to :class:`Api`, never a
field in a tournament context or result.
"""

from dataclasses import dataclass, field
from typing import Any, Mapping


LIFECYCLE_STATES = frozenset({
    "registering", "stage_open", "running", "stage_done",
    "finished", "closed", "void",
})
TERMINAL_STATES = frozenset({"finished", "closed", "void"})
TERMINATION_REASONS = frozenset({
    "FINISHED", "ELIMINATED", "VOID", "CLOSED", "TOKEN_NOT_BOUND",
    "AUTH_FAILED", "PROTOCOL_FATAL", "INTERRUPTED",
})

# These are the rule fields currently understood by the client.  Unknown
# server additions remain outside the normalized snapshot by design.
_RULE_KEYS = {
    "M", "Rounds", "BaseScore", "YouCaiBiKao",
    "WindowSec", "ResponseWindowSec", "DiscardSec", "ResponseTimeout",
    "DiscardTimeout", "WindowTimeout", "PengTimeout", "ChiTimeout",
    "HuTimeout", "window_sec", "response_window_sec", "discard_sec",
    "response_timeout", "discard_timeout", "window_timeout",
    "peng_timeout", "chi_timeout", "hu_timeout",
}


def _number(value, default=None):
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return value
    return default


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _json_value(value):
    """Return a conservative JSON-safe copy for result metadata."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(v) for v in value]
    return str(value)


@dataclass(frozen=True)
class TournamentRules:
    """The token-scoped, known subset of ``/rules.config``."""

    config: Mapping[str, Any] = field(default_factory=dict)
    m: int | None = None
    rounds: int | None = None
    base_score: int | float = 1
    you_cai_bi_kao: bool = False
    timeout_window: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(cls, response):
        if not isinstance(response, Mapping):
            raise ValueError("rules 响应必须是对象")
        config = response.get("config", response)
        if not isinstance(config, Mapping):
            raise ValueError("rules.config 必须是对象")
        known = {key: _json_value(value)
                 for key, value in config.items() if key in _RULE_KEYS}
        m = _number(config.get("M"))
        rounds = _number(config.get("Rounds"))
        base = _number(config.get("BaseScore"), 1)
        timeout_window = {
            key: known[key] for key in known
            if key not in {"M", "Rounds", "BaseScore", "YouCaiBiKao"}
        }
        return cls(
            config=known,
            m=int(m) if isinstance(m, (int, float)) else None,
            rounds=int(rounds) if isinstance(rounds, (int, float)) else None,
            base_score=base,
            you_cai_bi_kao=(config.get("YouCaiBiKao", False)
                           if isinstance(config.get("YouCaiBiKao", False), bool)
                           else str(config.get("YouCaiBiKao", "")).lower()
                           in ("1", "true", "yes", "on")),
            timeout_window=timeout_window,
        )

    def as_dict(self):
        return {
            "config": _json_value(self.config),
            "M": self.m,
            "Rounds": self.rounds,
            "BaseScore": self.base_score,
            "YouCaiBiKao": self.you_cai_bi_kao,
            "timeout_window": _json_value(self.timeout_window),
        }


@dataclass(frozen=True)
class TournamentContext:
    """Public facts resolved for one registration token."""

    token_label: str
    server: str
    tournament_id: str
    user_id: str | None = None
    active_games: tuple[str, ...] = ()
    rules: TournamentRules | None = None

    @classmethod
    def from_me(cls, *, token_label, server, response, rules=None):
        if not isinstance(response, Mapping):
            raise ValueError("/api/me 响应必须是对象")
        active = response.get("active_games") or ()
        game_ids = []
        for item in active:
            gid = item.get("game_id") if isinstance(item, Mapping) else item
            if gid:
                game_ids.append(str(gid))
        tid = response.get("tournament_id") or ""
        return cls(
            token_label=str(token_label),
            server=str(server),
            tournament_id=str(tid),
            user_id=(str(response["user_id"])
                     if response.get("user_id") is not None else None),
            active_games=tuple(dict.fromkeys(game_ids)),
            rules=rules,
        )


@dataclass
class TournamentResult:
    """Stable machine-readable result for one token worker."""

    token_label: str
    tournament_id: str | None = None
    user_id: str | None = None
    termination_reason: str = "PROTOCOL_FATAL"
    final_status: str | None = None
    final_stage: Any = None
    qualified: bool | None = None
    games: int = 0
    actions: int = 0
    hu: int = 0
    response_409: int = 0
    post_uncertain: int = 0
    stage_transitions: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self):
        reason = (self.termination_reason
                  if self.termination_reason in TERMINATION_REASONS
                  else "PROTOCOL_FATAL")
        return {
            "token_label": str(self.token_label),
            "tournament_id": self.tournament_id,
            "user_id": self.user_id,
            "termination_reason": reason,
            "final_status": self.final_status,
            "final_stage": _json_value(self.final_stage),
            "qualified": self.qualified,
            "games": _integer(self.games),
            "actions": _integer(self.actions),
            "hu": _integer(self.hu),
            "response_409": _integer(self.response_409),
            "post_uncertain": _integer(self.post_uncertain),
            "stage_transitions": _json_value(self.stage_transitions),
            "diagnostics": _json_value(self.diagnostics),
            "error": self.error,
        }
