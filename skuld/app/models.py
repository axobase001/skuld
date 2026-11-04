"""Strict boundary objects; providers propose, the runtime validates and commits."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Statement(Strict):
    key: str
    kind: Literal['Fact','Commitment','Goal','Task','Boundary','Preference','Relationship','Hypothesis']
    text: str
    value: Any
    quote: str
    modality: Literal['actual','intended','reported','hypothetical','uncertain']
    status: Literal['active','completed','cancelled'] = 'active'
    # This object is a provider proposal, never an admitted graph statement.
    # Enforce the closed role vocabulary at admission, after the independent
    # reviewer rejects historical answers masquerading as new assertions.
    actor: str = Field(default='UNSPECIFIED',description='Obligation actor: USER, ASSISTANT, SHARED or UNSPECIFIED. This is not source authority.')
    replaces_id: str | None = None
    due_at: str | None = None
    valid_from: str | None = None

class Conversation(Strict):
    statements: list[Statement]
    reply: str

class Compilation(Strict):
    statements: list[Statement]

class ConversationReview(Strict):
    accepted_indices: list[int]
    reasons: list[str]
    grounded_reply: str

class Entailment(Strict):
    index: int
    entailed_kind: Literal['Fact','Commitment','Goal','Task','Boundary','Preference','Relationship','Hypothesis','NONE']
    reason: str
    entailed_status: Literal['active','completed','cancelled','NONE'] = 'NONE'

class Admission(Strict):
    accepted_indices: list[int]
    reasons: list[str]
    entailments: list[Entailment]

class Expression(Strict):
    grounded_reply: str

class Binding(Strict):
    responsibility_id: str
    belief_id: str
    reason: str

class Bindings(Strict):
    bindings: list[Binding]

class Concern(Strict):
    responsibility_ids: list[str]
    evidence_ids: list[str]
    reason: str
    goal: str
    draft: str
    impact: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    prepare_benefit: float = Field(ge=0, le=1)
    ask_benefit: float = Field(ge=0, le=1)

class Deliberation(Strict):
    concerns: list[Concern]
    wait_reason: str

class ReportedProperty(Strict):
    key: str
    value: str | int | float | bool | None
    text: str
    quote: str
    valid_from: str | None = None

class WebObservation(Strict):
    facts: list[ReportedProperty]
