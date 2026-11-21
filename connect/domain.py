"""Grounded appointment arguments. All business dates use the clinic's timezone."""
import re
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

CLINIC = "Willowmere Demo Clinic"
ASR_HINT = "Doctor Lee and Doctor Rao, Willowmere Demo Clinic."
DOCTORS = ("Lee", "Rao")
HOURS = ("09:00", "10:00", "11:00", "14:00", "15:00", "16:00")


def today():
    return datetime.now(ZoneInfo("America/New_York")).date()


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["book", "cancel", "list", "unknown"]
    date: str = Field(max_length=10)
    time: str = Field(max_length=5)
    clinician: Literal["", "Lee", "Rao"]
    booking_id: str = Field(max_length=40)


class Appointment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    date: str
    time: Literal["09:00", "10:00", "11:00", "14:00", "15:00", "16:00"]
    clinician: Literal["Lee", "Rao"]

    @field_validator("date")
    @classmethod
    def iso_date(cls, value):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use an unambiguous ISO date")
        date.fromisoformat(value)
        return value

    def validate_schedule(self, current: date):
        day = date.fromisoformat(self.date)
        if not current < day <= current + timedelta(days=30) or day.weekday() >= 5:
            raise ValueError("Choose a weekday within the next thirty days")


def normalized(text):
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


YES = {"yes", "yes please", "yes confirm", "yeah", "yeah please", "yep", "confirm", "that is right", "thats right", "correct"}
NO = {"no", "no thanks", "no thank you", "never mind", "nevermind", "stop"}


def grounded_fields(text: str, current: date):
    """Resolve explicit supported expressions; ambiguous expressions remain empty."""
    lower = text.lower()
    days = []
    for raw in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", lower):
        try:
            days.append(date.fromisoformat(raw).isoformat())
        except ValueError:
            pass
    if "tomorrow" in lower:
        days.append((current + timedelta(days=1)).isoformat())
    for weekday, name in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")):
        if re.search(r"\b" + name + r"\b", lower):
            offset = (weekday - current.weekday()) % 7 or 7
            days.append((current + timedelta(days=offset)).isoformat())
    times = re.findall(r"\b(?:09|10|11|14|15|16):00\b", lower)
    words = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve".split())}
    pattern = r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|1[0-2]|[1-9])(?:\s+o.?clock)?\s*(a\.?m\.?|p\.?m\.?)\b"
    for number, period in re.findall(pattern, lower):
        hour = int(number) if number.isdigit() else words[number]
        hour = hour % 12 + (12 if period.startswith("p") else 0)
        times.append(f"{hour:02}:00")
    doctors = [doctor for doctor in DOCTORS if re.search(r"\b" + doctor.lower() + r"\b", lower)]
    ids = re.findall(r"\b[a-f0-9]{32}\b", lower)
    def unique(values):
        return values[0] if len(set(values)) == 1 else ""
    return {"date": unique(days), "time": unique(times), "clinician": unique(doctors), "booking_id": unique(ids)}


def intent_schema(fields):
    return {"type": "object", "additionalProperties": False,
        "properties": {"operation": {"type": "string", "enum": ["book", "cancel", "list", "unknown"]},
            **{name: {"type": "string", "enum": list(dict.fromkeys(["", value]))} for name, value in fields.items()}},
        "required": ["operation", "date", "time", "clinician", "booking_id"]}
