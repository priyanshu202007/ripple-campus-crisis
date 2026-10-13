"""First-pass activity scheduler using only declared dataset requirements."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any

import pandas as pd

try:
    from .repair_scheduler import schedule_repairs
except ImportError:
    from repair_scheduler import schedule_repairs


def _rows(dataset: dict[str, Any], filename: str) -> list[dict[str, Any]]:
    frame = dataset.get("_csv_data", {}).get(filename)
    return frame.to_dict(orient="records") if isinstance(frame, pd.DataFrame) else []


def _time(value: Any) -> int | None:
    try:
        hour, minute = str(value).strip().split(":", 1)
        result = int(hour) * 60 + int(minute)
        return result if 0 <= result < 24 * 60 else None
    except (ValueError, AttributeError):
        return None


def _fmt(value: int | None) -> str | None:
    return f"{value // 60:02d}:{value % 60:02d}" if value is not None else None


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _list(value: Any) -> list[str]:
    text = str(value or "").strip()
    return [] if not text or text.lower() == "none" else [x.strip() for x in text.split(";") if x.strip()]


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _merge(windows: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[list[int]] = []
    for start, end in sorted(windows):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return [(start, end) for start, end in result]


def _closure_windows(value: Any) -> tuple[list[tuple[int, int]], bool]:
    text = str(value or "").strip()
    if not text or text.lower() == "none":
        return [], True
    parsed = []
    for token in text.split(";"):
        pieces = token.strip().split("-")
        if len(pieces) != 2:
            return [], False
        start, end = _time(pieces[0]), _time(pieces[1])
        if start is None or end is None or end <= start:
            return [], False
        parsed.append((start, end))
    return parsed, True


def _hard_topological_order(activities: list[dict[str, Any]], dependencies: list[dict[str, Any]], strategy: str = "high_priority") -> tuple[list[str], set[str]]:
    ids = {str(row.get("activity_id", "")).strip() for row in activities}
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {activity_id: 0 for activity_id in ids if activity_id}
    for dependency in dependencies:
        if str(dependency.get("constraint_type", "")).strip().lower() != "hard":
            continue
        source = str(dependency.get("source_activity", "")).strip()
        target = str(dependency.get("target_activity", "")).strip()
        if source in indegree and target in indegree:
            outgoing[source].append(target)
            indegree[target] += 1
    by_id = {str(row.get("activity_id", "")).strip(): row for row in activities}

    def order_key(activity_id: str) -> tuple[int, int, str]:
        row = by_id[activity_id]
        original_start = _time(row.get("original_start"))
        if strategy == "early_service":
            return (original_start if original_start is not None else 24 * 60, -(_int(row.get("priority_weight")) or 0), activity_id)
        return (-(_int(row.get("priority_weight")) or 0), original_start if original_start is not None else 24 * 60, activity_id)

    ready = sorted((key for key, degree in indegree.items() if degree == 0), key=order_key)
    ordered = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(outgoing.get(current, [])):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=order_key)
    cyclic = set(indegree) - set(ordered)
    ordered.extend(sorted(cyclic, key=order_key))
    return ordered, cyclic


def schedule_activities(dataset: dict[str, Any], strategy: str = "high_priority", repair_plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """Schedule activities with a 15-minute start grid and a deterministic greedy search."""
    required_files = [
        "activities.csv", "activity_modes.csv", "rooms.csv", "room_availability.csv",
        "room_features.csv", "room_services.csv", "services.csv", "staff_assignments.csv",
        "staff_directory.csv", "staff_roles.csv", "dependencies.csv", "buildings.csv",
        "constraints.csv",
    ]
    incomplete: list[dict[str, Any]] = []
    for filename in required_files:
        if filename not in dataset.get("_csv_data", {}):
            incomplete.append({"file": filename, "detail": "Required scheduling input is missing or unreadable."})

    activities = _rows(dataset, "activities.csv")
    modes = _rows(dataset, "activity_modes.csv")
    rooms = _rows(dataset, "rooms.csv")
    availability_rows = _rows(dataset, "room_availability.csv")
    feature_dictionary = {str(r.get("feature_id", "")).strip(): r for r in _rows(dataset, "room_features.csv")}
    room_services = _rows(dataset, "room_services.csv")
    services = _rows(dataset, "services.csv")
    assignments = _rows(dataset, "staff_assignments.csv")
    staff = _rows(dataset, "staff_directory.csv")
    staff_roles = {str(r.get("role_id", "")).strip(): r for r in _rows(dataset, "staff_roles.csv")}
    dependencies = _rows(dataset, "dependencies.csv")
    buildings = {str(r.get("building_id", "")).strip(): r for r in _rows(dataset, "buildings.csv")}
    service_by_id = {str(r.get("service_id", "")).strip(): r for r in services}
    room_by_id = {str(r.get("room_id", "")).strip(): r for r in rooms}
    staff_by_id = {str(r.get("staff_id", "")).strip(): r for r in staff}
    activity_by_id = {str(r.get("activity_id", "")).strip(): r for r in activities}
    modes_by_activity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mode in modes:
        aid = str(mode.get("activity_id", "")).strip()
        if aid:
            modes_by_activity[aid].append(mode)
    room_services_by_room: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in room_services:
        room_services_by_room[str(row.get("room_id", "")).strip()].append(row)
    availability_by_room: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in availability_rows:
        if str(row.get("day", "")).strip() == "event_day":
            availability_by_room[str(row.get("room_id", "")).strip()].append(row)
    baseline_by_activity_role: dict[tuple[str, str], list[str]] = defaultdict(list)
    baseline_by_staff: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in assignments:
        aid, sid, role = (str(row.get(k, "")).strip() for k in ("activity_id", "staff_id", "assignment_role"))
        if aid and sid and role and _bool(row.get("required", "true")):
            baseline_by_activity_role[(aid, role)].append(sid)
            baseline_by_staff[sid].append(row)

    activity_ids = set(activity_by_id)
    room_ids = set(room_by_id)
    staff_ids = set(staff_by_id)
    for mode in modes:
        if str(mode.get("activity_id", "")).strip() not in activity_ids:
            incomplete.append({"type": "activity_mode", "mode_id": mode.get("mode_id"), "detail": "Mode references an activity absent from activities.csv."})
    for row in assignments:
        if str(row.get("activity_id", "")).strip() not in activity_ids or str(row.get("staff_id", "")).strip() not in staff_ids:
            incomplete.append({"type": "staff_assignment", "id": row.get("assignment_id"), "detail": "Baseline assignment references a missing activity or staff record."})
    for dep in dependencies:
        if str(dep.get("source_activity", "")).strip() not in activity_ids or str(dep.get("target_activity", "")).strip() not in activity_ids:
            incomplete.append({"type": "dependency", "id": dep.get("dependency_id"), "detail": "Dependency references an activity absent from activities.csv."})
    for row in room_services:
        if str(row.get("room_id", "")).strip() not in room_ids:
            incomplete.append({"type": "room_service", "room_id": row.get("room_id"), "service_id": row.get("service_id"), "detail": "Room service mapping references a room absent from rooms.csv."})
        if str(row.get("service_id", "")).strip() not in service_by_id:
            incomplete.append({"type": "room_service", "room_id": row.get("room_id"), "service_id": row.get("service_id"), "detail": "Room service mapping references a service absent from services.csv; state is unknown."})

    if repair_plan is None:
        try:
            repair_plan = schedule_repairs(dataset)
        except Exception as exc:
            repair_plan = {"service_restorations": [], "scheduled_jobs": [], "unscheduled_jobs": []}
            incomplete.append({"type": "repair_schedule", "detail": f"Repair scheduler failed; services are not assumed restored: {exc}"})
    restoration_time: dict[str, int] = {}
    restoration_state: dict[str, str] = {}
    for item in repair_plan.get("service_restorations", []):
        if item.get("status") == "confirmed_restoration":
            service_id = str(item.get("service_id", "")).strip()
            time_value = _time(item.get("restoration_time"))
            state = str(item.get("restored_state", "")).strip().lower()
            if service_id and time_value is not None and state:
                restoration_time[service_id] = time_value
                restoration_state[service_id] = state
    repair_summary = {
        "scheduled_jobs": len(repair_plan.get("scheduled_jobs", [])),
        "unscheduled_jobs": len(repair_plan.get("unscheduled_jobs", [])),
        "confirmed_service_restorations": [
            {"service_id": sid, "state": restoration_state[sid], "time": _fmt(restoration_time[sid])}
            for sid in sorted(restoration_time)
        ],
    }
    if repair_plan.get("unscheduled_jobs"):
        incomplete.append({"type": "repair_schedule", "detail": "Some repairs were not scheduled; only confirmed repair-plan restorations are applied to service timelines."})

    incident = dataset.get("documents", {}).get("incident.json", {})
    incident_start = _time(incident.get("incident_start")) if isinstance(incident, dict) else None
    service_links = _rows(dataset, "service_links.csv")
    links_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in service_links:
        links_by_target[str(link.get("target_service", "")).strip()].append(link)
    service_change_points = set(restoration_time.values())
    service_change_points.update(value for row in services if (value := _time(row.get("state_from"))) is not None)

    def state_at(service_id: str, minute: int, path: set[str] | None = None) -> str:
        path = set() if path is None else path
        if service_id in path:
            return "unknown"
        row = service_by_id.get(service_id)
        if not row:
            return "unknown"
        service_path = path | {service_id}
        if service_id in restoration_time and minute >= restoration_time[service_id]:
            state = restoration_state[service_id]
        else:
            state_from = _time(row.get("state_from"))
            if state_from is None or minute < state_from:
                return "unknown"
            state = str(row.get("initial_state", "")).strip().lower() or "unknown"
        if state not in {"available", "degraded", "unavailable"}:
            return "unknown"
        if state != "available":
            return state
        for link in links_by_target.get(service_id, []):
            source = str(link.get("source_service", "")).strip()
            required = str(link.get("required_state", "")).strip().lower()
            if not source or not required:
                return "unknown"
            source_state = state_at(source, minute, service_path)
            if source_state == "unknown":
                return "unknown"
            if source_state != required:
                return "unavailable"
        return state

    def room_state_reason(room_id: str, service_types: list[str], start: int, end: int) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not service_types:
            return True, reasons
        mappings = room_services_by_room.get(room_id, [])
        for service_type in service_types:
            matched = [row for row in mappings if str(row.get("service_type", "")).strip() == service_type]
            if not matched:
                reasons.append(f"Room {room_id} has no mapped {service_type} service; state is unknown.")
                continue
            if len(matched) > 1:
                reasons.append(f"Room {room_id} has multiple mapped {service_type} services; mapping is ambiguous.")
                continue
            service_id = str(matched[0].get("service_id", "")).strip()
            if not service_id:
                reasons.append(f"Room {room_id} has an empty service ID for {service_type}; state is unknown.")
                continue
            samples = [start, *sorted(t for t in service_change_points if start < t < end)]
            failed = [(t, state_at(service_id, t)) for t in samples]
            bad = [(t, state) for t, state in failed if state != "available"]
            if bad:
                t, state = bad[0]
                reasons.append(f"Service {service_id} is {state} at {_fmt(t)} within activity interval [{_fmt(start)}, {_fmt(end)}).")
        return not reasons, reasons

    def staff_candidate_ids(activity_id: str, role: str, start: int, end: int, used_ids: set[str]) -> list[str]:
        role_row = staff_roles.get(role)
        qualification = str(role_row.get("required_qualification_tag", "")).strip() if role_row else ""
        if not role_row or not qualification:
            return []
        preference = baseline_by_activity_role.get((activity_id, role), [])
        eligible = []
        for sid, person in staff_by_id.items():
            qualifications = set(_list(person.get("qualifications", "")))
            if qualification not in qualifications or sid in used_ids:
                continue
            available_from, available_until = _time(person.get("available_from")), _time(person.get("available_until"))
            max_concurrent = _int(person.get("max_concurrent_activities"))
            if available_from is None or available_until is None or max_concurrent is None:
                continue
            if start < available_from or end > available_until:
                continue
            break_start, break_end = _time(person.get("break_start")), _time(person.get("break_end"))
            if break_start is not None and break_end is not None and start < break_end and break_start < end:
                continue
            overlaps = sum(1 for item in staff_calendar[sid] if start < item[1] and item[0] < end)
            if overlaps >= max_concurrent:
                continue
            is_baseline = sid in preference
            if not _bool(person.get("reassignable", "true")) and not is_baseline:
                continue
            eligible.append(sid)
        return sorted(eligible, key=lambda sid: (sid not in preference, sid))

    def assign_staff(activity: dict[str, Any], room: dict[str, Any], start: int, end: int) -> tuple[list[dict[str, str]] | None, str | None]:
        aid = str(activity.get("activity_id", "")).strip()
        try:
            role_count = int(activity.get("required_staff_count", "0"))
            technical_count = int(activity.get("required_technical_support_count", "0"))
        except (ValueError, TypeError):
            return None, "Required staff counts are missing or invalid."
        if role_count < 0 or technical_count < 0:
            return None, "Required staff counts cannot be negative."
        slots: list[tuple[str, str]] = []
        main_role = str(activity.get("required_staff_role", "")).strip()
        if role_count and not main_role:
            return None, "Required staff role is missing."
        slots.extend((main_role, "activity_role") for _ in range(role_count))
        slots.extend(("technical_support", "technical_support") for _ in range(technical_count))

        features = set(_list(activity.get("required_features", "")))
        room_features = set(_list(room.get("features", "")))
        is_lab = "specialist_lab" in features or "controlled_access" in features
        is_food_venue = "food_service" in features or "food_service" in room_features
        facility_role = "lab_technician" if is_lab else "food_service_lead" if is_food_venue else None
        if facility_role and not any(role == facility_role for role, _ in slots):
            slots.append((facility_role, "facility_safety"))
        if _bool(room.get("requires_facilitator", "false")):
            if facility_role:
                # The safety-qualified lead also satisfies the venue's
                # facilitator requirement; do not allocate a duplicate slot.
                pass
            elif main_role:
                if not any(role == main_role for role, _ in slots):
                    slots.append((main_role, "facility_facilitator"))
            else:
                return None, f"Room {room.get('room_id')} requires a facilitator but the dataset does not declare the required facility role."

        if not slots:
            return [], None
        # Select scarce role slots first; backtracking avoids using one person twice.
        used: set[str] = set()
        chosen: list[dict[str, str]] = []
        role_candidates = []
        for role, kind in slots:
            if role not in staff_roles:
                return None, f"Staff role {role!r} has no staff_roles.csv qualification mapping."
            candidates = staff_candidate_ids(aid, role, start, end, used)
            if not candidates:
                return None, f"No qualified, available staff member for role {role} over [{_fmt(start)}, {_fmt(end)})."
            role_candidates.append((len(candidates), role, kind, candidates))
        role_candidates.sort(key=lambda item: (item[0], item[1], item[2]))

        def search(index: int) -> bool:
            if index == len(role_candidates):
                return True
            _, role, kind, candidate_ids = role_candidates[index]
            for sid in candidate_ids:
                if sid in used:
                    continue
                used.add(sid)
                chosen.append({"staff_id": sid, "role": role, "assignment_type": kind})
                if search(index + 1):
                    return True
                chosen.pop()
                used.remove(sid)
            return False

        return (chosen, None) if search(0) else (None, "Qualified roles cannot be covered by distinct staff members simultaneously.")

    # Calendars are populated below; closure captured by staff_candidate_ids.
    staff_calendar: dict[str, list[tuple[int, int]]] = defaultdict(list)
    room_calendar: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    scheduled_by_activity: dict[str, dict[str, Any]] = {}
    scheduled: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
    unscheduled: list[dict[str, Any]] = []
    diagnostic: dict[str, list[str]] = {}
    order, cyclic_ids = _hard_topological_order(activities, dependencies, strategy=strategy)

    def record_unplaced(activity: dict[str, Any], reasons: list[str]) -> None:
        aid = str(activity.get("activity_id", "")).strip()
        row = {
            "activity_id": aid,
            "activity_name": activity.get("activity_name"),
            "reasons": sorted(set(reasons), key=lambda reason: (
                0 if "Hard prerequisite" in reason or "Hard dependency" in reason else
                1 if "Service " in reason or "service is unknown" in reason.lower() or "mapped .* service" in reason.lower() else
                2 if "availability" in reason.lower() or "closure" in reason.lower() or "operating hours" in reason.lower() else
                3 if "capacity" in reason.lower() else
                4 if "No qualified" in reason or "staff" in reason.lower() else 5,
                reason,
            ))[:8] or ["No feasible placement was found by the heuristic."],
            "search_result": "heuristic_search_failure; infeasibility is not proven",
        }
        if _bool(activity.get("deferral_allowed", "false")):
            deferred.append({**row, "status": "deferred", "deferral_allowed": True})
        elif _bool(activity.get("cancellation_allowed", "false")):
            cancelled.append({**row, "status": "cancelled", "cancellation_allowed": True, "cancellation_penalty": _int(activity.get("cancellation_penalty"))})
        else:
            unscheduled.append({**row, "status": "unscheduled", "cancellation_allowed": False, "deferral_allowed": False})

    all_activity_ids = set(activity_by_id)
    for aid in order:
        activity = activity_by_id[aid]
        reasons: list[str] = []
        if aid in cyclic_ids:
            record_unplaced(activity, ["Hard dependency cycle detected; no ordering can be certified."])
            continue
        required_files_missing = [item["file"] for item in incomplete if item.get("file")]
        if required_files_missing:
            record_unplaced(activity, [f"Required scheduling input is missing: {', '.join(sorted(set(required_files_missing)))}."])
            continue
        modes_for_activity = modes_by_activity.get(aid, [])
        if not modes_for_activity:
            incomplete.append({"type": "activity_mode", "activity_id": aid, "detail": "No activity_modes.csv record; no unapproved mode is invented."})
            record_unplaced(activity, ["No approved activity mode is declared."])
            continue
        allowed_modes = [mode for mode in modes_for_activity if _bool(mode.get("allowed", "false"))]
        if not allowed_modes:
            record_unplaced(activity, ["No approved activity mode is currently allowed."])
            continue

        hard_deps = [d for d in dependencies if str(d.get("target_activity", "")).strip() == aid and str(d.get("constraint_type", "")).strip().lower() == "hard"]
        lower_bound = _time(activity.get("earliest_start"))
        upper_bound = _time(activity.get("latest_start"))
        original_start = _time(activity.get("original_start"))
        max_advance = _int(activity.get("max_advance_minutes"))
        max_delay = _int(activity.get("max_delay_minutes"))
        participants = _int(activity.get("participants"))
        priority_weight = _int(activity.get("priority_weight")) or 0
        if None in (lower_bound, upper_bound, original_start, max_advance, max_delay, participants) or lower_bound > upper_bound:
            incomplete.append({"type": "activity", "activity_id": aid, "detail": "Missing/invalid activity time window, original start, limits, or participant count."})
            record_unplaced(activity, ["Activity scheduling bounds or participant count are missing/invalid."])
            continue
        lower_bound = max(lower_bound, original_start - max_advance)
        upper_bound = min(upper_bound, original_start + max_delay)
        if lower_bound > upper_bound:
            record_unplaced(activity, [f"Allowed start intervals do not overlap after applying earliest/latest and advance/delay limits ({_fmt(lower_bound)} after {_fmt(upper_bound)})."])
            continue
        hard_start_floor = lower_bound
        hard_dep_failed = False
        for dep in hard_deps:
            source = str(dep.get("source_activity", "")).strip()
            source_plan = scheduled_by_activity.get(source)
            gap = _int(dep.get("minimum_gap_minutes"))
            if source not in all_activity_ids or gap is None:
                incomplete.append({"type": "hard_dependency", "activity_id": aid, "source_activity": source or None, "detail": "Dependency source or minimum_gap_minutes is missing/invalid."})
                hard_dep_failed = True
                reasons.append(f"Hard dependency source {source or '[missing]'} or gap is unknown.")
                break
            if source_plan is None:
                hard_dep_failed = True
                reasons.append(f"Hard prerequisite activity {source} was not scheduled; dependent activity cannot be certified.")
                break
            hard_start_floor = max(hard_start_floor, source_plan["end_minute"] + gap)
        if hard_dep_failed:
            record_unplaced(activity, reasons)
            continue
        if hard_start_floor > upper_bound:
            record_unplaced(activity, [f"Hard prerequisite completion requires start at {_fmt(hard_start_floor)}, after the permitted latest start {_fmt(upper_bound)}."])
            continue

        candidates = []
        modes_sorted = sorted(allowed_modes, key=lambda m: (_int(m.get("quality_penalty")) or 0, str(m.get("mode_id", ""))))
        for mode in modes_sorted:
            mode_id = str(mode.get("mode_id", "")).strip()
            duration_max = _int(mode.get("duration_minutes"))
            duration_max_allowed = _int(activity.get("duration_minutes"))
            duration_min = _int(activity.get("minimum_duration_minutes"))
            if duration_max is None or duration_max_allowed is None or duration_min is None or duration_max < duration_min or duration_max > duration_max_allowed:
                incomplete.append({"type": "activity_mode", "activity_id": aid, "mode_id": mode_id or None, "detail": "Mode duration is missing or violates the activity minimum/maximum duration."})
                reasons.append(f"Mode {mode_id or '[missing]'} has invalid duration bounds.")
                continue
            try:
                quality_penalty = int(mode.get("quality_penalty", "0"))
            except (TypeError, ValueError):
                quality_penalty = 0
                incomplete.append({"type": "activity_mode", "activity_id": aid, "mode_id": mode_id, "detail": "Invalid mode quality penalty."})
            base_features = set(_list(activity.get("required_features", "")))
            mode_features = set(_list(mode.get("required_features", "")))
            # Approved modes can relax ordinary requirements; they cannot waive
            # safety-marked requirements declared by the activity.
            safety_features = {feature for feature, row in feature_dictionary.items() if _bool(row.get("mandatory_safety_feature", "false"))}
            required_features = mode_features | (base_features & safety_features)
            required_services = _list(mode.get("required_service_types", ""))
            if "specialist_lab" in base_features or "controlled_access" in base_features:
                required_features.update({"controlled_access"})
                required_services = sorted(set(required_services) | {"access"})
            if "food_service" in base_features:
                required_features.add("food_service")
                required_services = sorted(set(required_services) | {"power", "water"})
            if not set(required_features).issubset(feature_dictionary):
                missing_features = sorted(set(required_features) - set(feature_dictionary))
                incomplete.append({"type": "feature", "activity_id": aid, "feature_ids": missing_features, "detail": "Required feature is absent from room_features.csv; it cannot be validated."})
                reasons.append(f"Mode {mode_id} uses unknown feature(s): {', '.join(missing_features)}.")
                continue
            durations = list(range(duration_max, duration_min - 1, -1))
            if _int(activity.get("minimum_duration_minutes")) == duration_max_allowed:
                durations = [duration_max]
            for duration in durations:
                original_room_id = str(activity.get("original_room_id", "")).strip()
                eligible_rooms = room_by_id.items() if _bool(activity.get("relocatable", "false")) else [(original_room_id, room_by_id[original_room_id])] if original_room_id in room_by_id else []
                for room_id, room in sorted(eligible_rooms):
                    room_feature_set = set(_list(room.get("features", "")))
                    accessibility = str(room.get("accessibility", "")).strip().lower()
                    available_features = room_feature_set | ({"accessible"} if accessibility in {"step_free", "accessible"} else set())
                    if not required_features.issubset(available_features):
                        reasons.append(f"Room {room_id} lacks required feature(s) for {mode_id}.")
                        continue
                    capacity = _int(room.get("capacity"))
                    if capacity is None:
                        reasons.append(f"Room {room_id} capacity is unknown.")
                        continue
                    if capacity < participants:
                        reasons.append(f"Room {room_id} capacity {capacity} is below {participants} participants.")
                        continue
                    building = buildings.get(str(room.get("building_id", "")).strip())
                    if not building:
                        reasons.append(f"Room {room_id} building record is missing.")
                        continue
                    room_open = _time(room.get("opening_time"))
                    room_close = _time(room.get("closing_time"))
                    building_open = _time(building.get("opening_time"))
                    building_close = _time(building.get("closing_time"))
                    if None in (room_open, room_close, building_open, building_close):
                        reasons.append(f"Room {room_id} or its building has unknown operating hours.")
                        continue
                    windows = availability_by_room.get(room_id, [])
                    valid_windows = []
                    invalid_window = False
                    closures = []
                    for window in windows:
                        start_w, end_w = _time(window.get("available_from")), _time(window.get("available_until"))
                        if start_w is None or end_w is None:
                            invalid_window = True
                            continue
                        if str(window.get("status", "")).strip().lower() == "available":
                            valid_windows.append((start_w, end_w))
                        else:
                            closures.append((start_w, end_w))
                    if invalid_window or not valid_windows:
                        reasons.append(f"Room {room_id} availability is missing or unknown.")
                        continue
                    closure_windows, closure_parse_ok = _closure_windows(room.get("closure_windows", ""))
                    if not closure_parse_ok:
                        reasons.append(f"Room {room_id} closure_windows is not parseable; availability is unknown.")
                        continue
                    closures.extend(closure_windows)
                    try:
                        activity_setup = int(activity.get("setup_minutes", "0"))
                        activity_teardown = int(activity.get("teardown_minutes", "0"))
                        room_setup = int(room.get("setup_buffer_minutes", "0"))
                        room_teardown = int(room.get("teardown_buffer_minutes", "0"))
                    except (ValueError, TypeError):
                        reasons.append(f"Room {room_id} or activity setup/teardown buffer is invalid.")
                        continue
                    if min(activity_setup, activity_teardown, room_setup, room_teardown) < 0:
                        reasons.append(f"Room {room_id} or activity setup/teardown buffer is negative.")
                        continue
                    occupancy_before = activity_setup + room_setup
                    occupancy_after = activity_teardown + room_teardown
                    first_start = math.ceil(max(hard_start_floor, 0) / 15) * 15
                    for start in range(first_start, upper_bound + 1, 15):
                        end = start + duration
                        occupancy_start = start - occupancy_before
                        occupancy_end = end + occupancy_after
                        if end > 24 * 60:
                            continue
                        if occupancy_start < max(room_open, building_open) or occupancy_end > min(room_close, building_close):
                            continue
                        if not any(occupancy_start >= left and occupancy_end <= right for left, right in valid_windows):
                            continue
                        if any(occupancy_start < close_end and close_start < occupancy_end for close_start, close_end in closures):
                            continue
                        if any(occupancy_start < busy_end and busy_start < occupancy_end for busy_start, busy_end, _ in room_calendar[room_id]):
                            continue
                        service_ok, service_reasons = room_state_reason(room_id, required_services, start, end)
                        if not service_ok:
                            reasons.extend(service_reasons)
                            continue
                        staff_plan, staff_reason = assign_staff(activity, room, start, end)
                        if staff_plan is None:
                            if staff_reason:
                                reasons.append(staff_reason)
                            continue
                        delay = max(0, start - original_start)
                        relocation = int(room_id != str(activity.get("original_room_id", "")).strip())
                        provisional_soft_penalty = 0
                        for dep in dependencies:
                            if str(dep.get("constraint_type", "")).strip().lower() != "soft":
                                continue
                            source_id = str(dep.get("source_activity", "")).strip()
                            target_id = str(dep.get("target_activity", "")).strip()
                            other_id = target_id if source_id == aid else source_id if target_id == aid else ""
                            other = scheduled_by_activity.get(other_id)
                            if not other:
                                continue
                            gap = _int(dep.get("minimum_gap_minutes")) or 0
                            violates = (source_id == aid and end + gap > other["start_minute"]) or (target_id == aid and other["end_minute"] + gap > start)
                            if violates:
                                provisional_soft_penalty += _int(dep.get("penalty_weight")) or 0
                        baseline_staff_count = sum(1 for row in staff_plan if row["staff_id"] in baseline_by_activity_role.get((aid, row["role"]), []))
                        score = (
                            priority_weight * delay,
                            relocation,
                            provisional_soft_penalty,
                            quality_penalty,
                            abs(start - original_start),
                            -duration,
                            -baseline_staff_count,
                            start,
                            room_id,
                            mode_id,
                        )
                        candidates.append((score, {
                            "activity_id": aid,
                            "activity_name": activity.get("activity_name"),
                            "status": "scheduled",
                            "mode_id": mode_id,
                            "mode_name": mode.get("mode_name"),
                            "start": _fmt(start),
                            "end": _fmt(end),
                            "duration_minutes": duration,
                            "room_id": room_id,
                            "building_id": room.get("building_id"),
                            "original_room_id": activity.get("original_room_id"),
                            "original_start": activity.get("original_start"),
                            "delay_minutes": start - original_start,
                            "relocated": bool(relocation),
                            "quality_penalty": quality_penalty,
                            "required_services": required_services,
                            "assigned_staff": staff_plan,
                            "room_occupancy": {"from": _fmt(occupancy_start), "until": _fmt(occupancy_end), "setup_minutes": occupancy_before, "teardown_minutes": occupancy_after},
                            "service_checks": [],
                            "_start_minute": start,
                            "_end_minute": end,
                            "_occupancy_start": occupancy_start,
                            "_occupancy_end": occupancy_end,
                            "_score": score,
                        }))
        if not candidates:
            diagnostic[aid] = list(dict.fromkeys(reasons))[:8]
            record_unplaced(activity, diagnostic[aid])
            continue

        _, chosen = min(candidates, key=lambda item: item[0])
        chosen.pop("_score")
        start, end = chosen.pop("_start_minute"), chosen.pop("_end_minute")
        occupancy_start, occupancy_end = chosen.pop("_occupancy_start"), chosen.pop("_occupancy_end")
        for assignment in chosen["assigned_staff"]:
            staff_calendar[assignment["staff_id"]].append((start, end))
        room_calendar[chosen["room_id"]].append((occupancy_start, occupancy_end, aid))
        service_checks = []
        for service_type in chosen["required_services"]:
            mapped = [r for r in room_services_by_room.get(chosen["room_id"], []) if str(r.get("service_type", "")).strip() == service_type]
            if mapped:
                sid = str(mapped[0].get("service_id", "")).strip()
                service_checks.append({"service_id": sid, "service_type": service_type, "state_at_start": state_at(sid, start), "state_at_end_minus_boundary": state_at(sid, max(start, end - 1)), "available_for_full_interval": True})
        chosen["service_checks"] = service_checks
        chosen["_start_minute"] = start
        chosen["_end_minute"] = end
        scheduled.append(chosen)
        scheduled_by_activity[aid] = {"start_minute": start, "end_minute": end, "room_id": chosen["room_id"], "start": _fmt(start), "end": _fmt(end), **chosen}

    soft_violations = []
    for dep in dependencies:
        if str(dep.get("constraint_type", "")).strip().lower() != "soft":
            continue
        source, target = str(dep.get("source_activity", "")).strip(), str(dep.get("target_activity", "")).strip()
        source_plan, target_plan = scheduled_by_activity.get(source), scheduled_by_activity.get(target)
        gap = _int(dep.get("minimum_gap_minutes")) or 0
        violated = not source_plan or not target_plan or source_plan["end_minute"] + gap > target_plan["start_minute"]
        if violated:
            soft_violations.append({"dependency_id": dep.get("dependency_id"), "source_activity": source, "target_activity": target, "penalty": _int(dep.get("penalty_weight")) or 0, "reason": "One endpoint was not scheduled or the preferred continuity/gap condition was not met."})

    for item in scheduled:
        item.pop("_start_minute", None)
        item.pop("_end_minute", None)
    scheduled.sort(key=lambda row: (row["start"], row["activity_id"]))
    objective = {
        "hard_feasibility": "All listed scheduled activities passed the modeled hard checks; unscheduled/undelivered activities are not certified feasible.",
        "weighted_undelivered_priority": sum((_int(activity_by_id.get(row["activity_id"], {}).get("priority_weight")) or 0) for row in deferred + cancelled + unscheduled),
        "cancellation_penalty_total": sum(row.get("cancellation_penalty") or 0 for row in cancelled),
        "priority_weighted_delay_minutes": sum((_int(activity_by_id[row["activity_id"]].get("priority_weight")) or 0) * max(0, row["delay_minutes"]) for row in scheduled),
        "relocations": sum(1 for row in scheduled if row["relocated"]),
        "soft_dependency_penalty_total": sum(row["penalty"] for row in soft_violations),
        "mode_quality_penalty_total": sum(row["quality_penalty"] for row in scheduled),
        "staff_not_retained_from_baseline": sum(1 for row in scheduled for person in row["assigned_staff"] if person["staff_id"] not in baseline_by_activity_role.get((row["activity_id"], person["role"]), [])),
        "engineering_completion_time": max((item["time"] for item in repair_summary["confirmed_service_restorations"]), default=None),
    }
    return {
        "scheduler": "deterministic hard-feasibility-first greedy placement heuristic",
        "optimality": "This heuristic does not prove global optimality or infeasibility; an unplaced activity may reflect greedy search limits.",
        "time_grid_minutes": 15,
        "scheduled_activities": scheduled,
        "deferred_activities": deferred,
        "cancelled_activities": cancelled,
        "unscheduled_activities": unscheduled,
        "soft_dependency_violations": soft_violations,
        "objective_summary": objective,
        "repair_timeline_used": repair_summary,
        "incomplete_input_information": incomplete,
        "counts": {"scheduled": len(scheduled), "deferred": len(deferred), "cancelled": len(cancelled), "unscheduled": len(unscheduled)},
        "assumptions_and_limits": [
            "Room occupancy buffers add activity setup/teardown to the room setup/teardown buffers; both are included conservatively.",
            "Staff are assigned for the activity interval; room setup and teardown occupy the room but are not treated as staff duty because the data gives no separate setup staffing rule.",
            "Required roles use exact qualification tags. The staff role table has no explicit substitute-role mapping, so can_substitute=true is not used to infer cross-role qualifications.",
            "For rooms marked requires_facilitator, lab and food-safety roles are enforced from the documented safety rules; a generic facilitator role is not inferred where the dataset provides no qualification mapping.",
            "Unknown service or room availability states block certification; only repair scheduler restorations with confirmed mappings are applied.",
            "Soft dependency penalties are reported after placement; greedy decisions are not globally backtracked to reduce every soft penalty.",
        ],
    }
