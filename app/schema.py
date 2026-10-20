"""Validation models for the Muse update protocol."""
import re
from datetime import date, datetime, time
from typing import Annotated, Literal, Optional, Union
from zoneinfo import ZoneInfo
import os

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

LOCAL = ZoneInfo(os.environ.get("TZ") or "UTC")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_dt(s: str) -> str:
    try:
        if _DATE_RE.match(s):
            date.fromisoformat(s)
        else:
            datetime.fromisoformat(s)
    except ValueError:
        raise ValueError("expected ISO 8601 date (YYYY-MM-DD) or datetime (YYYY-MM-DDTHH:MM[:SS][+TZ])")
    return s


def to_ts(s, end_of_day=False):
    if not s:
        return None
    if _DATE_RE.match(s):
        dt = datetime.combine(date.fromisoformat(s), time(23, 59, 59) if end_of_day else time(0, 0), tzinfo=LOCAL)
    else:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL)
    return dt.timestamp()


def check_plan_date(s: str) -> str:
    if not _DATE_RE.match(s):
        raise ValueError("planned_for must be a local calendar date YYYY-MM-DD")
    try:
        date.fromisoformat(s)
    except ValueError:
        raise ValueError("planned_for is not a real date")
    return s


Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Dt = Annotated[str, AfterValidator(check_dt)]
PlanDate = Annotated[str, AfterValidator(check_plan_date)]

Basis = Literal["observed", "planned", "recommended", "unknown"]
AREAS = [("research", "Research"), ("course", "Courses"), ("career", "Career"), ("admin", "Admin"),
         ("purchase", "Purchases"), ("logistics", "Logistics"), ("health", "Health"), ("life", "Life"), ("system", "System")]
Area = Literal["research", "course", "career", "admin", "purchase", "logistics", "health", "life", "system"]
TEMPLATES = Literal["generic", "paper", "review", "course", "application", "purchase", "order", "cycle", "log", "admin", "event"]
Priority = Literal["p0", "p1", "p2", "p3", "unknown"]


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Focus(Base):
    title: Title
    reason: Optional[Text] = None
    project: Optional[Id] = None
    task: Optional[Id] = None
    priority: Optional[Priority] = None
    deadline: Optional[Dt] = None
    basis: Optional[Basis] = None


class Item(Base):
    id: Id
    delete: bool = False


class Linked(Item):
    """Optional links shared by every record: stable area enum, stable thread id, soft-merge pointer."""
    area: Optional[Area] = None
    thread: Optional[Id] = None
    merged_into: Optional[Id] = None


_SID = r"^[a-z][a-z0-9_]{1,40}$"
_FORBIDDEN_INTENT = ("pay", "purchase", "buy", "transfer", "send", "email", "mail", "delete", "wire", "withdraw", "checkout", "order", "submit_payment", "post", "publish", "sign")


class UiChoice(Base):
    value: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,30}$")]
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class UiStep(Base):
    id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,20}$")]
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    choices: Annotated[list[UiChoice], Field(min_length=2, max_length=5)]

    @model_validator(mode="after")
    def _uniq(self):
        if len({c.value for c in self.choices}) != len(self.choices):
            raise ValueError("duplicate choice values in step '%s'" % self.id)
        return self


class UiAction(Base):
    """Declarative, Muse-published button. The browser can only submit one of these declarations back."""
    id: Annotated[str, StringConstraints(pattern=_SID)]
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    type: Literal["intent"] = "intent"
    intent: Annotated[str, StringConstraints(pattern=_SID)]
    steps: Optional[Annotated[list[UiStep], Field(max_length=3)]] = None
    sensitive: bool = False
    expires_at: Optional[Dt] = None

    @model_validator(mode="after")
    def _check(self):
        low = self.intent.lower()
        if any(low == w or low.startswith(w + "_") or ("_" + w + "_") in low or low.endswith("_" + w) for w in _FORBIDDEN_INTENT):
            raise ValueError("intent '%s' implies an external side-effect (payment/purchase/send/delete…); request it through a sensitive inbox item instead" % self.intent)
        if self.steps and len({s.id for s in self.steps}) != len(self.steps):
            raise ValueError("duplicate step ids")
        return self


class ThreadItem(Item):
    title: Optional[Title] = None
    area: Optional[Area] = None
    template: Optional[TEMPLATES] = None
    status: Optional[Literal["active", "waiting", "paused", "done", "dropped"]] = None
    stage: Optional[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]] = None
    stages: Optional[list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]]] = None
    summary: Optional[Text] = None
    next_step: Optional[Text] = None
    basis: Optional[Basis] = None
    ui_actions: Optional[Annotated[list[UiAction], Field(max_length=3)]] = None   # replaces the whole list; [] or null clears


Opt = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class InboxItem(Item):
    """Something only the human can decide/confirm. Muse requests; the human answers by click."""
    title: Optional[Title] = None
    detail: Optional[Text] = None
    kind: Optional[Literal["decision", "confirm", "alert", "todo"]] = None
    status: Optional[Literal["open", "acknowledged", "confirmed", "declined", "resolved"]] = None
    sensitive: Optional[bool] = None
    options: Optional[list[Opt]] = None
    choice: Optional[Opt] = None
    area: Optional[Area] = None
    thread: Optional[Id] = None
    due: Optional[Dt] = None
    basis: Optional[Basis] = None


class QueueItem(Linked):
    title: Optional[Title] = None
    status: Optional[Literal["todo", "active", "waiting", "blocked", "done", "dropped", "unknown"]] = None
    priority: Optional[Priority] = None
    order: Optional[int] = None
    project: Optional[Id] = None
    planned_for: Optional[PlanDate] = None      # local calendar day the user plans to do it (NOT a deadline, NOT a reminder)
    time_hint: Optional[Literal["morning", "afternoon", "evening", "tonight"]] = None   # coarse part of day; never a clock time
    note: Optional[Text] = None
    basis: Optional[Basis] = None


class DeadlineItem(Linked):
    title: Optional[Title] = None
    due: Optional[Dt] = None
    status: Optional[Literal["open", "done", "dropped", "unknown"]] = None
    project: Optional[Id] = None
    note: Optional[Text] = None
    basis: Optional[Basis] = None


class ProjectItem(Linked):
    name: Optional[Title] = None
    status: Optional[Literal["active", "planned", "paused", "blocked", "done", "unknown"]] = None
    progress: Optional[Text] = None
    next_step: Optional[Text] = None
    blockers: Optional[Text] = None
    note: Optional[Text] = None
    basis: Optional[Basis] = None


class ScheduleItem(Linked):
    title: Optional[Title] = None
    start: Optional[Dt] = None
    end: Optional[Dt] = None
    location: Optional[Text] = None
    status: Optional[Literal["confirmed", "tentative", "unconfirmed", "cancelled"]] = None
    attendance: Optional[Literal["going", "not_going"]] = None     # the human's own intent; NOT the event's status (cancelled = the event itself was cancelled)
    note: Optional[Text] = None
    basis: Optional[Basis] = None


class AutomationItem(Linked):
    name: Optional[Title] = None
    frequency: Optional[Text] = None
    last_run: Optional[Dt] = None
    next_run: Optional[Dt] = None
    status: Optional[Literal["ok", "running", "failed", "paused", "unknown"]] = None
    kind: Optional[Literal["daily", "weekly", "monthly", "once", "always"]] = None
    note: Optional[Text] = None
    basis: Optional[Basis] = None


class JournalEntry(Base):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    kind: Literal["observed", "planned", "recommended", "decision", "note", "digest", "alert"] = "note"
    title: Optional[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]] = None
    area: Optional[Area] = None
    thread: Optional[Id] = None


JMsg = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

# section -> (model, label field, fields required on create)
SECTIONS = {
    "threads": (ThreadItem, "title", ("area",)),
    "inbox": (InboxItem, "title", ()),
    "queue": (QueueItem, "title", ()),
    "deadlines": (DeadlineItem, "title", ("due",)),
    "projects": (ProjectItem, "name", ()),
    "schedule": (ScheduleItem, "title", ("start",)),
    "automations": (AutomationItem, "name", ()),
}


class ActionResult(Base):
    id: int
    status: Literal["completed", "declined", "unable"]
    note: Optional[Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]] = None


class UpdateRequest(Base):
    focus: Optional[Focus] = None
    threads: Optional[list[ThreadItem]] = None
    inbox: Optional[list[InboxItem]] = None
    queue: Optional[list[QueueItem]] = None
    deadlines: Optional[list[DeadlineItem]] = None
    projects: Optional[list[ProjectItem]] = None
    schedule: Optional[list[ScheduleItem]] = None
    automations: Optional[list[AutomationItem]] = None
    journal: Optional[Union[JMsg, JournalEntry, list[Union[JMsg, JournalEntry]]]] = None
    heartbeat: Optional[bool] = None
    actions_seen: Optional[int] = None
    action_results: Optional[list[ActionResult]] = Field(default=None, max_length=50)
    idempotency_key: Optional[Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]] = None


class ActionRequest(Base):
    """A human click, sent by the protected UI script with the human token."""
    type: Literal["done", "reopen", "set_status", "set_stage", "ack", "confirm", "decline", "resolve", "choose", "ui_action",
                  "not_attending", "attend", "stop_reminding", "resume_reminding"]
    section: Literal["queue", "deadlines", "threads", "inbox", "schedule", "automations"]
    id: Id
    params: Optional[dict] = None
    sensitive_ack: bool = False
    request_id: Optional[Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,64}$")]] = None


# --- Chronicle -> Dashboard context events (observations only; never instructions) ---
CONTEXT_PLACES = ("home", "office")
# The phone's native vocabulary -> (canonical type, place). Only Home/Office are stored in v1.
CHRONICLE_NATIVE = {"arrived_home": ("geofence_enter", "home"), "left_home": ("geofence_exit", "home"),
                    "arrived_office": ("geofence_enter", "office"), "left_office": ("geofence_exit", "office")}
CHRONICLE_KNOWN_IGNORED = ("arrived_gym", "left_gym", "woke_up")
_EID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")]
_TS = Annotated[str, StringConstraints(min_length=20, max_length=40)]


class ContextNative(Base):
    """What a phone automation (e.g. an iOS Shortcut) sends: {"event","device","ts"} (+ optional event_id)."""
    event: Annotated[str, StringConstraints(pattern=r"^[a-z_]{3,30}$")]
    device: Optional[Literal["iphone"]] = None
    ts: _TS
    event_id: Optional[_EID] = None


class ContextCanonical(Base):
    event_id: Optional[_EID] = None
    source: Literal["chronicle"] = "chronicle"
    type: Literal["geofence_enter", "geofence_exit"]
    place: Literal["home", "office"]
    observed_at: _TS
