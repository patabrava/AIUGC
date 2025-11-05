"""
FLOW-FORGE State Machine Core
State definitions and transition logic per Canon § 3.
Per Constitution § VII: State Machine Discipline
"""

from enum import Enum
from typing import Set, Dict
from app.core.errors import StateTransitionError


class BatchState(str, Enum):
    """Batch state definitions per Canon § 3.1"""
    S1_SETUP = "S1_SETUP"
    S2_SEEDED = "S2_SEEDED"
    S4_SCRIPTED = "S4_SCRIPTED"
    S5_PROMPTS_BUILT = "S5_PROMPTS_BUILT"
    S6_QA = "S6_QA"
    S7_PUBLISH_PLAN = "S7_PUBLISH_PLAN"
    S8_COMPLETE = "S8_COMPLETE"


# Valid state transitions per Canon § 3.2
STATE_TRANSITIONS: Dict[BatchState, Set[BatchState]] = {
    BatchState.S1_SETUP: {BatchState.S2_SEEDED},
    BatchState.S2_SEEDED: {BatchState.S4_SCRIPTED},
    BatchState.S4_SCRIPTED: {BatchState.S5_PROMPTS_BUILT},
    BatchState.S5_PROMPTS_BUILT: {BatchState.S6_QA},
    BatchState.S6_QA: {
        BatchState.S7_PUBLISH_PLAN,  # All approved
        BatchState.S4_SCRIPTED,       # Regenerate from scripts
        BatchState.S5_PROMPTS_BUILT   # Regenerate from prompts
    },
    BatchState.S7_PUBLISH_PLAN: {BatchState.S8_COMPLETE},
    BatchState.S8_COMPLETE: set()  # Terminal state
}


def validate_state_transition(
    current_state: BatchState,
    target_state: BatchState
) -> None:
    """
    Validate state transition is allowed.
    Raises StateTransitionError if invalid.
    Per Constitution § VII: Explicit guards.
    """
    allowed_transitions = STATE_TRANSITIONS.get(current_state, set())
    
    if target_state not in allowed_transitions:
        raise StateTransitionError(
            message=f"Invalid state transition from {current_state} to {target_state}",
            details={
                "current_state": current_state,
                "target_state": target_state,
                "allowed_transitions": [s.value for s in allowed_transitions]
            }
        )


def get_next_states(current_state: BatchState) -> Set[BatchState]:
    """Get all valid next states from current state."""
    return STATE_TRANSITIONS.get(current_state, set())


def is_terminal_state(state: BatchState) -> bool:
    """Check if state is terminal (no further transitions)."""
    return len(STATE_TRANSITIONS.get(state, set())) == 0
