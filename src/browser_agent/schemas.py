"""Validated data contracts shared by the browser agent components."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Self
from uuid import uuid4

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
EnvironmentReference = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")]


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and supports immutable contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TestSpec(StrictModel):
    """User-provided test request. Secrets are referenced, never embedded here."""

    url: AnyHttpUrl
    instruction: Annotated[str, Field(min_length=5, max_length=4_000)]
    test_data_refs: list[EnvironmentReference] = Field(default_factory=list)
    max_steps: int = Field(default=25, ge=1, le=100)
    timeout_seconds: int = Field(default=120, ge=5, le=900)

    @field_validator("instruction")
    @classmethod
    def instruction_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("instruction must not be blank")
        return value.strip()

    @field_validator("test_data_refs")
    @classmethod
    def refs_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("test_data_refs must not contain duplicates")
        return value


class FrameInfo(StrictModel):
    """A browser frame discovered during an observation."""

    frame_id: Identifier
    parent_frame_id: Identifier | None = None
    url: str
    name: str | None = None
    is_main_frame: bool = False
    depth: int = Field(default=0, ge=0)


class ElementInfo(StrictModel):
    """An interactive or otherwise relevant DOM element."""

    element_id: Identifier
    frame_id: Identifier
    tag: str
    role: str | None = None
    name: str | None = None
    label: str | None = None
    placeholder: str | None = None
    input_type: str | None = None
    text: str | None = None
    href: str | None = None
    visible: bool = True
    enabled: bool = True
    checked: bool | None = None
    id_attribute: str | None = None
    name_attribute: str | None = None
    test_id: str | None = None
    aria_label: str | None = None
    autocomplete: str | None = None
    required: bool = False
    read_only: bool = False
    multiple: bool = False
    selected: bool | None = None


class BrowserObservation(StrictModel):
    """Compact representation of the current browser state."""

    observation_id: Identifier = Field(
        default_factory=lambda: f"obs_{uuid4().hex[:20]}"
    )
    url: str
    title: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    frames: list[FrameInfo] = Field(default_factory=list)
    elements: list[ElementInfo] = Field(default_factory=list)
    visible_text: str = Field(default="", max_length=20_000)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def element_ids_must_be_unique(self) -> Self:
        ids = [element.element_id for element in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("element_id values must be unique within an observation")
        frame_ids = {frame.frame_id for frame in self.frames}
        for element in self.elements:
            if element.frame_id not in frame_ids:
                raise ValueError(
                    f"element {element.element_id!r} references unknown frame "
                    f"{element.frame_id!r}"
                )
        return self


class ActionType(StrEnum):
    CLICK = "click"
    FILL = "fill"
    SELECT = "select"
    CHECK = "check"
    UNCHECK = "uncheck"
    PRESS = "press"
    WAIT = "wait"


class AgentAction(StrictModel):
    """One constrained browser operation selected by the planner."""

    action: ActionType
    element_id: Identifier
    observation_id: Identifier
    value: str | None = None
    value_ref: EnvironmentReference | None = None
    timeout_ms: int | None = Field(default=None, ge=100, le=60_000)
    wait_state: str = Field(
        default="visible", pattern=r"^(visible|hidden|attached|detached)$"
    )

    @model_validator(mode="after")
    def validate_action_arguments(self) -> Self:
        if self.action in {ActionType.FILL, ActionType.SELECT}:
            if (self.value is None) == (self.value_ref is None):
                raise ValueError(
                    f"{self.action.value} requires exactly one of value or value_ref"
                )
        elif self.action == ActionType.PRESS:
            if self.value is None or not self.value.strip():
                raise ValueError("press requires a non-empty key in value")
            if self.value_ref is not None:
                raise ValueError("press does not accept value_ref")
        elif self.value is not None or self.value_ref is not None:
            raise ValueError(f"{self.action.value} does not accept value or value_ref")
        if self.action != ActionType.WAIT and self.wait_state != "visible":
            raise ValueError("wait_state can only be customized for wait actions")
        return self


class LocatorStrategy(StrEnum):
    """Supported deterministic locator strategies for Phase 1 plans."""

    ROLE = "role"
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    TEXT = "text"
    TEST_ID = "test_id"
    CSS = "css"


class ManualLocator(StrictModel):
    """A locator supplied by a developer-authored deterministic action plan."""

    strategy: LocatorStrategy
    value: NonEmptyString | None = None
    role: NonEmptyString | None = None
    exact: bool = True
    index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_locator_shape(self) -> Self:
        if self.strategy == LocatorStrategy.ROLE:
            if self.role is None:
                raise ValueError("role locators require a role")
        elif self.role is not None:
            raise ValueError("role is only valid with the role locator strategy")

        if self.value is None and self.strategy != LocatorStrategy.ROLE:
            raise ValueError(f"{self.strategy.value} locators require a value")
        return self


class ManualAction(StrictModel):
    """One browser action with a locator known in advance (no LLM involved)."""

    action: ActionType
    locator: ManualLocator
    value: str | None = None
    value_ref: EnvironmentReference | None = None
    timeout_ms: int | None = Field(default=None, ge=100, le=60_000)
    wait_state: str = Field(default="visible", pattern=r"^(visible|hidden|attached|detached)$")

    @model_validator(mode="after")
    def validate_action_arguments(self) -> Self:
        if self.action in {ActionType.FILL, ActionType.SELECT}:
            if (self.value is None) == (self.value_ref is None):
                raise ValueError(
                    f"{self.action.value} requires exactly one of value or value_ref"
                )
        elif self.action == ActionType.PRESS:
            if self.value is None or not self.value.strip():
                raise ValueError("press requires a non-empty key in value")
            if self.value_ref is not None:
                raise ValueError("press does not accept value_ref")
        elif self.value is not None or self.value_ref is not None:
            raise ValueError(f"{self.action.value} does not accept value or value_ref")

        if self.action != ActionType.WAIT and self.wait_state != "visible":
            raise ValueError("wait_state can only be customized for wait actions")
        return self


class ManualTestPlan(StrictModel):
    """A manually authored action sequence used to validate the browser engine."""

    name: NonEmptyString
    actions: list[ManualAction] = Field(min_length=1, max_length=100)


class ManualStepResult(StrictModel):
    """Safe execution summary for one manual action (never includes secret values)."""

    step_number: int = Field(ge=1)
    action: ActionType
    locator: str
    success: bool
    message: str
    started_at: datetime
    finished_at: datetime
    screenshot_path: str | None = None


class TestStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ERROR = "ERROR"
    BLOCKED = "BLOCKED"


class ManualExecutionReport(StrictModel):
    """Phase 1 action-sequence outcome; it does not yet evaluate test assertions."""

    test_name: str
    status: TestStatus
    url: str
    started_at: datetime
    finished_at: datetime
    steps: list[ManualStepResult] = Field(default_factory=list)
    console_errors: list[str] = Field(default_factory=list)
    page_errors: list[str] = Field(default_factory=list)
    opened_page_count: int = Field(default=0, ge=0)
    popup_count: int = Field(default=0, ge=0)
    failure_reason: str | None = None
    artifact_paths: list[str] = Field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds())


class AssertionType(StrEnum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    TEXT_CONTAINS = "text_contains"
    URL_CONTAINS = "url_contains"
    CHECKED = "checked"
    ENABLED = "enabled"
    VALUE_EQUALS = "value_equals"


class AssertionSpec(StrictModel):
    """A condition that must be checked against real browser state."""

    assertion: AssertionType
    target: NonEmptyString
    expected: str | bool | None = None

    @model_validator(mode="after")
    def validate_expected_value(self) -> Self:
        if self.assertion in {AssertionType.TEXT_CONTAINS, AssertionType.URL_CONTAINS}:
            if not isinstance(self.expected, str) or not self.expected:
                raise ValueError(
                    f"{self.assertion.value} requires a non-empty string expected"
                )
        elif self.assertion == AssertionType.VALUE_EQUALS:
            if not isinstance(self.expected, str):
                raise ValueError("value_equals requires a string expected")
        elif self.assertion in {AssertionType.CHECKED, AssertionType.ENABLED}:
            if not isinstance(self.expected, bool):
                raise ValueError(f"{self.assertion.value} requires a boolean expected")
        elif self.expected is not None:
            raise ValueError(f"{self.assertion.value} does not accept expected")
        return self


class AgentDecisionType(StrEnum):
    ACT = "act"
    FINISH = "finish"
    BLOCKED = "blocked"


class AgentDecision(StrictModel):
    """Planner output. FINISH is not equivalent to a passing test."""

    decision: AgentDecisionType
    action: AgentAction | None = None
    reason: NonEmptyString

    @model_validator(mode="after")
    def action_required_only_for_act(self) -> Self:
        if self.decision == AgentDecisionType.ACT and self.action is None:
            raise ValueError("act decision requires an action")
        if self.decision != AgentDecisionType.ACT and self.action is not None:
            raise ValueError("only an act decision may include an action")
        return self


class StepResult(StrictModel):
    """Outcome and diagnostics for one attempted browser action."""

    step_number: int = Field(ge=1)
    action: AgentAction
    success: bool
    message: str
    started_at: datetime
    finished_at: datetime
    screenshot_path: str | None = None


class AssertionResult(StrictModel):
    """Outcome of one deterministic assertion."""

    assertion: AssertionSpec
    passed: bool
    actual: str | bool | None = None
    message: str


class ExecutionResult(StrictModel):
    """Final result returned to the CLI and report writer."""

    test_name: str
    status: TestStatus
    url: str
    started_at: datetime
    finished_at: datetime
    steps: list[StepResult] = Field(default_factory=list)
    assertions: list[AssertionResult] = Field(default_factory=list)
    failure_reason: str | None = None
    artifact_paths: list[str] = Field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds())

    @property
    def passed_assertions(self) -> int:
        return sum(result.passed for result in self.assertions)

    @property
    def failed_assertions(self) -> int:
        return sum(not result.passed for result in self.assertions)
