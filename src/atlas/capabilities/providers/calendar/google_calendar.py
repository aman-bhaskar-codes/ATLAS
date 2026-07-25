"""Google Calendar provider — Calendar REST API, OAuth2 token from the Identity Platform.

WHY token via vault: ADR-016. Every call fetches a fresh token (refresh handled inside
6.2). Google's timed-vs-all-day split (event['start']['dateTime'] vs ['date']) and its
attendee/responseStatus shape are mapped to EventTime/Attendee HERE and never escape.
WHY writes return only an id: the platform re-reads if it needs the committed event, so
the adapter stays a thin mapper with no caching/state.
"""
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import httpx

from atlas.capabilities.domain.calendar import (
    Attendee,
    AttendeeResponse,
    Availability,
    CalendarEvent,
    EventDraft,
    EventTime,
    FreeBusySlot,
)
from atlas.capabilities.errors import ProviderAuthError, ProviderExecutionError
from atlas.capabilities.identity.platform import IdentityPlatform

_API = "https://www.googleapis.com/calendar/v3"


class GoogleCalendarProvider:
    name = "google_calendar"
    requires_auth = True

    def __init__(self, identity: IdentityPlatform, credential_id: str,
                 timeout_s: float = 30.0) -> None:
        self._identity = identity
        self._credential_id = credential_id
        self._client = httpx.AsyncClient(timeout=timeout_s)

    async def initialize(self) -> None:
        pass

    async def authenticate(self) -> None:
        await self._identity.get_usable_secret(self._credential_id)

    async def _headers(self) -> dict[str, str]:
        try:
            token = await self._identity.get_usable_secret(self._credential_id)
        except Exception as exc:
            raise ProviderAuthError(f"gcal token unavailable: {exc}") from exc
        return {"Authorization": f"Bearer {token}"}

    async def health(self) -> bool:
        try:
            await self._identity.get_usable_secret(self._credential_id)
            return True
        except Exception:
            return False

    # ---- reads (Tier-1) -------------------------------------------------------
    async def list_events(self, calendar_id: str, *, start: datetime,
                          end: datetime, limit: int) -> list[CalendarEvent]:
        h = await self._headers()
        r = await self._client.get(
            f"{_API}/calendars/{calendar_id}/events", headers=h,
            params={"timeMin": _iso(start), "timeMax": _iso(end),
                    "singleEvents": "true", "orderBy": "startTime",
                    "maxResults": limit})
        r.raise_for_status()
        return [self._to_event(calendar_id, e) for e in r.json().get("items", [])]

    async def search(self, query: str, *, limit: int) -> list[CalendarEvent]:
        h = await self._headers()
        r = await self._client.get(
            f"{_API}/calendars/primary/events", headers=h,
            params={"q": query, "singleEvents": "true", "maxResults": limit})
        r.raise_for_status()
        return [self._to_event("primary", e) for e in r.json().get("items", [])]

    async def get_event(self, calendar_id: str, event_id: str) -> CalendarEvent:
        h = await self._headers()
        r = await self._client.get(
            f"{_API}/calendars/{calendar_id}/events/{event_id}", headers=h)
        r.raise_for_status()
        return self._to_event(calendar_id, r.json())

    async def free_busy(self, calendar_id: str, *, start: datetime,
                        end: datetime) -> Availability:
        h = await self._headers()
        r = await self._client.post(
            f"{_API}/freeBusy", headers=h,
            json={"timeMin": _iso(start), "timeMax": _iso(end),
                  "items": [{"id": calendar_id}]})
        r.raise_for_status()
        cal = r.json().get("calendars", {}).get(calendar_id, {})
        busy = tuple(
            FreeBusySlot(start=_parse(b["start"]), end=_parse(b["end"]))
            for b in cal.get("busy", []))
        return Availability(calendar_id=calendar_id, window_start=start,
                            window_end=end, busy=busy)

    # ---- writes (only reached post-approval by the platform) -----------------
    async def create_event(self, draft: EventDraft) -> str:
        h = await self._headers()
        body = self._to_body(draft)
        params: dict[str, str] = {"sendUpdates": "all" if draft.send_invites else "none"}
        if draft.conferencing_request:
            body["conferenceData"] = {"createRequest": {"requestId": uuid.uuid4().hex}}
            params["conferenceDataVersion"] = "1"
        try:
            r = await self._client.post(
                f"{_API}/calendars/{draft.calendar_id}/events",
                headers=h, params=params, json=body)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderExecutionError(f"gcal create failed: {exc}") from exc
        return str(r.json()["id"])

    async def update_event(self, draft: EventDraft) -> str:
        if not draft.event_id:
            raise ProviderExecutionError("update requires event_id")
        h = await self._headers()
        r = await self._client.patch(
            f"{_API}/calendars/{draft.calendar_id}/events/{draft.event_id}",
            headers=h, params={"sendUpdates": "all" if draft.send_invites else "none"},
            json=self._to_body(draft))
        r.raise_for_status()
        return str(r.json()["id"])

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        h = await self._headers()
        r = await self._client.delete(
            f"{_API}/calendars/{calendar_id}/events/{event_id}",
            headers=h, params={"sendUpdates": "all"})
        if r.status_code not in (200, 204):
            raise ProviderExecutionError(f"gcal delete failed: {r.status_code}")

    async def shutdown(self) -> None:
        await self._client.aclose()

    # ---- mapping (the ONLY place Google's shape exists) ----------------------
    def _to_event(self, calendar_id: str, data: dict[str, object]) -> CalendarEvent:
        start = data.get("start") or {}
        end = data.get("end") or {}
        return CalendarEvent(
            id=str(data.get("id", "")),
            calendar_id=calendar_id,
            title=str(data.get("summary", "")),
            description=str(data.get("description", "")),
            location=str(data.get("location", "")),
            when=_to_time(start if isinstance(start, dict) else {},
                          end if isinstance(end, dict) else {}),
            attendees=tuple(
                _to_attendee(a)
                for a in (data.get("attendees") or [])  # type: ignore
                if isinstance(a, dict)
            ),
            conferencing=(str(data.get("hangoutLink"))
                          if data.get("hangoutLink")
                          else _extract_conf(
                              data.get("conferenceData")  # type: ignore
                              if isinstance(data.get("conferenceData"), dict) else None)),
            recurrence=tuple(
                str(r) for r in (data.get("recurrence") or [])  # type: ignore
            ),
            status=str(data.get("status", "confirmed")))

    def _to_body(self, draft: EventDraft) -> dict[str, object]:
        body: dict[str, object] = {
            "summary": draft.title,
            "description": draft.description,
            "location": draft.location,
            "attendees": [{"email": str(a.email), "optional": a.optional}
                          for a in draft.attendees],
        }
        if draft.when.all_day:
            body["start"] = {"date": draft.when.start_date.isoformat()
                             if draft.when.start_date else ""}
            body["end"] = {"date": draft.when.end_date.isoformat()
                           if draft.when.end_date else ""}
        else:
            body["start"] = {"dateTime": _iso(draft.when.start_dt)
                             if draft.when.start_dt else "", "timeZone": draft.when.tz}
            body["end"] = {"dateTime": _iso(draft.when.end_dt)
                           if draft.when.end_dt else "", "timeZone": draft.when.tz}
        if draft.recurrence:
            body["recurrence"] = list(draft.recurrence)
        return body


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _to_time(start: dict[str, object], end: dict[str, object]) -> EventTime:
    if "date" in start:  # all-day
        return EventTime(start_date=date.fromisoformat(str(start["date"])),
                         end_date=date.fromisoformat(str(end["date"])))
    return EventTime(start_dt=_parse(str(start.get("dateTime", ""))),
                     end_dt=_parse(str(end.get("dateTime", ""))),
                     tz=str(start.get("timeZone", "UTC")))


def _to_attendee(a: dict[str, object]) -> Attendee:
    resp = {
        "accepted": AttendeeResponse.ACCEPTED,
        "declined": AttendeeResponse.DECLINED,
        "tentative": AttendeeResponse.TENTATIVE,
    }.get(str(a.get("responseStatus", "")), AttendeeResponse.NEEDS_ACTION)
    return Attendee(
        email=str(a.get("email", "")),
        name=str(a["displayName"]) if a.get("displayName") else None,
        optional=bool(a.get("optional", False)),
        organizer=bool(a.get("organizer", False)),
        response=resp)


def _extract_conf(conf: dict[str, object] | None) -> str | None:
    if not conf:
        return None
    entry_points = conf.get("entryPoints") or []
    if not isinstance(entry_points, list):
        return None
    for ep in entry_points:
        if isinstance(ep, dict) and ep.get("entryPointType") == "video":
            return str(ep.get("uri", ""))
    return None
