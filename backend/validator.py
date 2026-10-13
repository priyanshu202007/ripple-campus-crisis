"""Independent checks for repair and activity schedule payloads."""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Any

import pandas as pd

try:
    from .repair_scheduler import schedule_repairs
    from .activity_scheduler import schedule_activities
except ImportError:
    from repair_scheduler import schedule_repairs
    from activity_scheduler import schedule_activities


def _rows(ds: dict[str, Any], name: str) -> list[dict[str, Any]]:
    frame = ds.get("_csv_data", {}).get(name)
    return frame.to_dict(orient="records") if isinstance(frame, pd.DataFrame) else []


def _s(x: Any) -> str:
    return str(x if x is not None else "").strip()


def _list(x: Any) -> list[str]:
    return [v.strip() for v in _s(x).split(";") if v.strip() and v.strip().lower() != "none"]


def _bool(x: Any) -> bool:
    return _s(x).lower() in {"true", "yes", "1"}


def _t(x: Any) -> int | None:
    try:
        h, m = _s(x).split(":", 1)
        v = int(h) * 60 + int(m)
        return v if 0 <= v < 1440 else None
    except (ValueError, AttributeError):
        return None


def _overlap(a: int, b: int, c: int, d: int) -> bool:
    return a < d and c < b


def validate_schedules(dataset: dict[str, Any], repair_schedule: dict[str, Any] | None = None,
                       activity_schedule: dict[str, Any] | None = None) -> dict[str, Any]:
    """Recheck schedule facts against source tables; unknown evidence stays unknown."""
    repair = repair_schedule if isinstance(repair_schedule, dict) else schedule_repairs(dataset)
    activity = activity_schedule if isinstance(activity_schedule, dict) else schedule_activities(dataset)
    checks: list[dict[str, Any]] = []

    def add(status: str, category: str, subject: str, detail: str, evidence: Any = None) -> None:
        item = {"status": status, "category": category, "subject_id": subject, "detail": detail}
        if evidence is not None:
            item["evidence"] = evidence
        checks.append(item)

    jobs = {r["request_id"]: r for r in _rows(dataset, "work_requests.csv") if _s(r.get("request_id"))}
    engineers = {r["person_id"]: r for r in _rows(dataset, "engineering_personnel.csv") if _s(r.get("person_id"))}
    parts = {r["part_id"]: r for r in _rows(dataset, "parts.csv") if _s(r.get("part_id"))}
    maps = _rows(dataset, "service_repair_map.csv")
    mapping = defaultdict(list)
    for m in maps: mapping[_s(m.get("request_id"))].append(m)
    repair_rows = repair.get("scheduled_jobs", []) if isinstance(repair.get("scheduled_jobs"), list) else []
    job_rows = { _s(r.get("request_id")): r for r in repair_rows if isinstance(r, dict) }
    consumed: dict[str, int] = defaultdict(int)
    engineer_intervals: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    restoration: dict[str, tuple[int, str]] = {}

    # Repair checks: eligibility, windows, stock, order, and computed completion.
    for jid, row in job_rows.items():
        source = jobs.get(jid)
        if source is None:
            add("failed", "repair", jid, "Scheduled request is absent from work_requests.csv."); continue
        start, work_end, occupied = _t(row.get("start")), _t(row.get("work_end")), _t(row.get("engineer_occupied_until"))
        engineer_id = _s(row.get("engineer_id")); engineer = engineers.get(engineer_id)
        required_skill = _s(source.get("required_skill"))
        eligible = set(_list(source.get("eligible_staff")))
        if engineer is None or engineer_id not in eligible:
            add("failed", "repair_skill", jid, "Assigned engineer is missing or not listed as eligible.", engineer_id)
        elif required_skill not in {_s(engineer.get("skill")), *_list(engineer.get("secondary_skills"))}:
            add("failed", "repair_skill", jid, "Engineer lacks required primary/secondary skill.", required_skill)
        else:
            add("passed", "repair_skill", jid, "Engineer eligibility and skill match dataset.", engineer_id)
            if not _bool(engineer.get("verification_authorized")):
                add("failed", "repair_skill", jid, "Assigned engineer is not authorized for verification.")
        try:
            active, hand, verify = int(row.get("active_work_minutes")), int(row.get("handover_minutes")), int(row.get("verification_minutes"))
        except (ValueError, TypeError):
            active = hand = verify = -1
        if None in (start, work_end, occupied) or min(active, hand, verify) < 0:
            add("unknown", "repair_timing", jid, "Start/work/occupancy timing is incomplete or invalid.")
        else:
            expected_end, expected_done = start + active, start + active + hand + verify
            try:
                expected_active = math.ceil(float(source.get("work_units")) / float(engineer.get("work_units_per_hour")) * 60) if engineer else None
                expected_handover = int(source.get("estimated_handover_minutes"))
                expected_verification = int(source.get("verification_minutes"))
            except (TypeError, ValueError, ZeroDivisionError):
                expected_active = expected_handover = expected_verification = None
            earliest, latest = _t(source.get("earliest_start")), _t(source.get("latest_start"))
            ok = (work_end == expected_end and occupied == expected_done and active == expected_active
                  and hand == expected_handover and verify == expected_verification
                  and start >= (earliest if earliest is not None else 1440) and start <= (latest if latest is not None else -1)
                  and not _bool(source.get("preemptible")))
            if engineer:
                af, au = _t(engineer.get("available_from")), _t(engineer.get("available_until"))
                ok = ok and af is not None and au is not None and start >= af and expected_done <= au
            add("passed" if ok else "failed", "repair_timing", jid, "Repair phases, request window and engineer availability checked." if ok else "Repair phase arithmetic, request window or engineer availability is violated.")
            if _t(row.get("restoration_time")) not in (expected_done, None):
                add("failed", "restoration", jid, "Reported restoration time differs from work + handover + verification.")
            if engineer:
                for left, right, other in engineer_intervals[engineer_id]:
                    if _overlap(start, expected_done, left, right): add("failed", "repair_overlap", jid, f"Engineer overlaps scheduled request {other}.")
                engineer_intervals[engineer_id].append((start, expected_done, jid))
        prerequisite = _s(source.get("prerequisite_request_id"))
        if prerequisite:
            prior = job_rows.get(prerequisite)
            if prior is None or _t(prior.get("engineer_occupied_until")) is None or start is None:
                add("failed", "repair_prerequisite", jid, f"Prerequisite {prerequisite} is not scheduled/completed before this job.")
            elif _t(prior.get("engineer_occupied_until")) > start:
                add("failed", "repair_prerequisite", jid, f"Prerequisite {prerequisite} completes after job start.")
            else: add("passed", "repair_prerequisite", jid, f"Prerequisite {prerequisite} completes before start.")
        part_id = _s(row.get("part_id")); part = parts.get(part_id)
        if part_id != _s(source.get("part_required")):
            add("failed", "parts", jid, "Assigned part does not match the request's required part.")
        try: available = int(part.get("quantity_available", 0)) - int(part.get("reserved_quantity", 0)) if part else -1
        except (ValueError, TypeError): available = -1
        compatible = _list(part.get("compatible_job_ids")) if part else []
        if not part or available < 0:
            add("unknown", "parts", jid, "Part stock or reservation data is missing/invalid.")
        elif jid not in compatible:
            add("failed", "parts", jid, f"Part {part_id} is not declared compatible with this request.")
        else:
            consumed[part_id] += 1
            add("passed" if consumed[part_id] <= available else "failed", "parts", jid, "Part compatibility and aggregate unreserved inventory checked.", {"part_id": part_id, "usable_stock": available})
        declared = {( _s(m.get("target_service_id")), _s(m.get("restored_state")), _s(m.get("effective_after"))) for m in mapping[jid]}
        for restored in row.get("restored_services", []) if isinstance(row.get("restored_services"), list) else []:
            sid = _s(restored.get("service_id")); state = _s(restored.get("restored_state")); rule = _s(restored.get("effective_after"))
            if (sid, state, rule) not in declared:
                add("failed", "restoration", jid, f"Restoration for {sid} does not match an explicit mapping.")
            elif rule != "work_end_plus_handover_plus_verification" or not state or occupied is None:
                add("unknown", "restoration", sid, "Mapping restoration rule/state is unsupported or timing unavailable.")
            else:
                restoration[sid] = (occupied, state.lower())
                add("passed", "restoration", sid, "Mapped restoration occurs after work, handover and verification.")
    # Validate service-restoration report entries against completed mapped jobs.
    for item in repair.get("service_restorations", []) if isinstance(repair.get("service_restorations"), list) else []:
        if item.get("status") == "confirmed_restoration":
            sid, jid = _s(item.get("service_id")), _s(item.get("request_id"))
            if sid not in restoration or jid not in job_rows:
                add("failed", "restoration", sid, "Confirmed restoration has no independently verified mapped job.")

    # Activity classification and permission checks.
    activities = {r["activity_id"]: r for r in _rows(dataset, "activities.csv") if _s(r.get("activity_id"))}
    modes = {_s(r.get("mode_id")): r for r in _rows(dataset, "activity_modes.csv")}
    rooms = {_s(r.get("room_id")): r for r in _rows(dataset, "rooms.csv")}
    buildings = {_s(r.get("building_id")): r for r in _rows(dataset, "buildings.csv")}
    features = { _s(r.get("room_id")): set(_list(r.get("features"))) for r in _rows(dataset, "rooms.csv") }
    feature_flags = { _s(r.get("feature_id")): _bool(r.get("mandatory_safety_feature")) for r in _rows(dataset, "room_features.csv") }
    availability = defaultdict(list)
    for r in _rows(dataset, "room_availability.csv"):
        if _s(r.get("day")) == "event_day": availability[_s(r.get("room_id"))].append(r)
    room_services = defaultdict(list)
    for r in _rows(dataset, "room_services.csv"): room_services[_s(r.get("room_id"))].append(r)
    services = {_s(r.get("service_id")): r for r in _rows(dataset, "services.csv")}
    links = defaultdict(list)
    for r in _rows(dataset, "service_links.csv"): links[_s(r.get("target_service"))].append(r)
    staff = {_s(r.get("staff_id")): r for r in _rows(dataset, "staff_directory.csv")}
    roles = {_s(r.get("role_id")): _s(r.get("required_qualification_tag")) for r in _rows(dataset, "staff_roles.csv")}
    deps = _rows(dataset, "dependencies.csv")
    placed: dict[str, dict[str, Any]] = {}
    room_busy: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    staff_busy: dict[str, list[tuple[int, int, str]]] = defaultdict(list)

    # Derived service state: original state plus only confirmed dataset-mapped repair completions.
    def service_state(sid: str, minute: int, path: frozenset[str] = frozenset()) -> str:
        if not sid or sid in path or sid not in services: return "unknown"
        row = services[sid]
        restored = restoration.get(sid)
        if restored and minute >= restored[0]: state = restored[1]
        else:
            since = _t(row.get("state_from"))
            if since is None or minute < since: return "unknown"
            state = _s(row.get("initial_state")).lower()
        if state != "available": return state if state in {"degraded", "unavailable"} else "unknown"
        for edge in links.get(sid, []):
            source, expected = _s(edge.get("source_service")), _s(edge.get("required_state")).lower()
            got = service_state(source, minute, path | {sid})
            if not source or not expected or got == "unknown": return "unknown"
            if got != expected: return got
        return "available"

    classifications = [("scheduled_activities", "scheduled"), ("deferred_activities", "deferred"), ("cancelled_activities", "cancelled"), ("unscheduled_activities", "unscheduled")]
    for key, status in classifications:
        for row in activity.get(key, []) if isinstance(activity.get(key), list) else []:
            aid = _s(row.get("activity_id")); source = activities.get(aid)
            if source is None:
                add("failed", "activity", aid, "Activity is absent from activities.csv."); continue
            if status in {"deferred", "cancelled"}:
                permitted = _bool(source.get("deferral_allowed" if status == "deferred" else "cancellation_allowed"))
                add("permitted" if permitted else "failed", "disposition", aid, f"{status.title()} disposition " + ("is explicitly allowed." if permitted else "is not allowed by source data."))
                continue
            if status != "scheduled":
                add("warning", "disposition", aid, "Activity is unscheduled; this alone does not prove infeasibility.")
                continue
            start, end = _t(row.get("start")), _t(row.get("end"))
            mode_id = _s(row.get("mode_id")); mode = modes.get(mode_id)
            room_id = _s(row.get("room_id")); room = rooms.get(room_id)
            if start is None or end is None or end <= start:
                add("failed", "activity_timing", aid, "Scheduled activity interval is missing or invalid."); continue
            placed[aid] = {**row, "_start": start, "_end": end}
            if not mode or _s(mode.get("activity_id")) != aid or not _bool(mode.get("allowed")):
                add("failed", "activity_mode", aid, "Mode is missing, belongs to another activity, or is not approved.")
                continue
            try: duration = int(row.get("duration_minutes")); minimum = int(source.get("minimum_duration_minutes")); maximum = int(source.get("duration_minutes"))
            except (TypeError, ValueError): duration = minimum = maximum = -1
            earliest, latest, original = _t(source.get("earliest_start")), _t(source.get("latest_start")), _t(source.get("original_start"))
            try: advance, delay = int(source.get("max_advance_minutes")), int(source.get("max_delay_minutes"))
            except (TypeError, ValueError): advance = delay = -1
            timing_ok = duration == end-start and minimum <= duration <= maximum and earliest is not None and latest is not None and earliest <= start <= latest and original is not None and advance >= 0 and delay >= 0 and original-advance <= start <= original+delay
            add("passed" if timing_ok else "failed", "activity_timing", aid, "Duration and start bounds checked." if timing_ok else "Duration, minimum/maximum, earliest/latest or advance/delay bound is violated/unknown.")
            base_features = set(_list(source.get("required_features")))
            # The approved mode declares ordinary requirements; base safety
            # features remain mandatory even when a mode relaxes other needs.
            safety = {f for f in base_features if feature_flags.get(f, False)}
            expected_features = set(_list(mode.get("required_features"))) | safety
            required_services = set(_list(mode.get("required_service_types")))
            if {"specialist_lab", "controlled_access"} & base_features:
                expected_features.add("controlled_access")
                required_services.add("access")
            if "food_service" in base_features:
                expected_features.add("food_service")
                required_services.update({"power", "water"})
            if not room:
                add("failed", "room", aid, f"Room {room_id} is missing."); continue
            if int(room.get("capacity", 0) or 0) < int(source.get("participants", 0) or 0):
                add("failed", "room_capacity", aid, "Room capacity is below participant count.")
            else: add("passed", "room_capacity", aid, "Room capacity covers participant count.")
            missing = expected_features - features.get(room_id, set())
            add("failed" if missing else "passed", "room_features", aid, "Missing required features: " + ", ".join(sorted(missing)) if missing else "Required activity and approved-mode features are present.")
            # Occupancy uses the reported interval only after verifying it equals source activity + room buffers.
            try:
                before = int(source.get("setup_minutes")) + int(room.get("setup_buffer_minutes")); after = int(source.get("teardown_minutes")) + int(room.get("teardown_buffer_minutes"))
                occ0, occ1 = start-before, end+after
                occ = row.get("room_occupancy", {})
                reported0, reported1 = _t(occ.get("from")), _t(occ.get("until"))
                if reported0 != occ0 or reported1 != occ1: add("failed", "room_buffers", aid, "Reported room occupancy does not include exact activity and room setup/teardown buffers.")
                open_t, close_t = _t(room.get("opening_time")), _t(room.get("closing_time"))
                building = buildings.get(_s(room.get("building_id")), {})
                bo, bc = _t(building.get("opening_time")), _t(building.get("closing_time"))
                windows = [( _t(w.get("available_from")), _t(w.get("available_until"))) for w in availability[room_id] if _s(w.get("status")).lower()=="available"]
                closures = [( _t(w.get("available_from")), _t(w.get("available_until"))) for w in availability[room_id] if _s(w.get("status")).lower()!="available"]
                ok_hours = None not in (open_t,close_t,bo,bc) and occ0 >= max(open_t,bo) and occ1 <= min(close_t,bc)
                ok_window = any(a is not None and b is not None and occ0 >= a and occ1 <= b for a,b in windows)
                closed = any(a is not None and b is not None and _overlap(occ0,occ1,a,b) for a,b in closures)
                closure_text = _s(room.get("closure_windows"))
                if closure_text and closure_text.lower() != "none":
                    try:
                        for token in closure_text.split(";"):
                            left, right = token.split("-", 1)
                            ca, cb = _t(left), _t(right)
                            if ca is None or cb is None:
                                raise ValueError("invalid closure window")
                            closed = closed or _overlap(occ0, occ1, ca, cb)
                    except ValueError:
                        add("unknown", "room_availability", aid, "Room closure_windows value is not parseable.")
                add("passed" if ok_hours and ok_window and not closed else "failed" if windows else "unknown", "room_availability", aid, "Occupancy fits operating and explicit availability windows." if ok_hours and ok_window and not closed else "Occupancy violates or cannot be verified against opening, availability or closure windows.")
                if any(_overlap(occ0,occ1,a,b) for a,b,_ in room_busy[room_id]): add("failed", "room_overlap", aid, "Room occupancy overlaps another scheduled activity.")
                room_busy[room_id].append((occ0,occ1,aid))
            except (ValueError, TypeError):
                add("unknown", "room_availability", aid, "Setup/teardown buffer data is invalid.")
            # Bind required service types to services mapped to this room and inspect the whole interval.
            for stype in sorted(required_services):
                candidates = [r for r in room_services[room_id] if _s(r.get("service_type")) == stype]
                if not candidates:
                    add("unknown", "service", aid, f"No room-specific service mapping for required type {stype}."); continue
                sid = _s(candidates[0].get("service_id"))
                changes = [start] + [t for t, _ in restoration.values() if start < t < end] + [_t(s.get("state_from")) for s in services.values() if start < (_t(s.get("state_from")) or -1) < end]
                states = [service_state(sid, t) for t in sorted(set(x for x in changes if x is not None))]
                result = "passed" if states and all(s == "available" for s in states) else "unknown" if "unknown" in states else "failed"
                add(result, "service_interval", aid, f"Room service {sid} ({stype}) states over activity interval: {states}.", {"service_id": sid, "sample_minutes": sorted(set(x for x in changes if x is not None))})
            # staff role totals and each assignment's qualification/availability/break/overlap
            assigned = row.get("assigned_staff", []) if isinstance(row.get("assigned_staff"), list) else []
            required_role = _s(source.get("required_staff_role")); required_n = int(source.get("required_staff_count", 0) or 0); tech_n = int(source.get("required_technical_support_count", 0) or 0)
            role_counts = defaultdict(int)
            for assignment in assigned:
                sid, role = _s(assignment.get("staff_id")), _s(assignment.get("role")); person = staff.get(sid); role_counts[role] += 1
                tag = roles.get(role)
                if not person or not tag:
                    add("unknown", "staff_qualification", aid, f"Staff or role qualification record missing for {sid}/{role}."); continue
                ok = tag in _list(person.get("qualifications"))
                af, au = _t(person.get("available_from")), _t(person.get("available_until"))
                ok = ok and af is not None and au is not None and start >= af and end <= au
                bs, be = _t(person.get("break_start")), _t(person.get("break_end"))
                if bs is not None and be is not None and _overlap(start,end,bs,be): ok = False
                if any(_overlap(start,end,a,b) for a,b,_ in staff_busy[sid]): ok = False
                try: maxc = int(person.get("max_concurrent_activities"))
                except (ValueError,TypeError): maxc = 0
                if maxc < 1: ok = False
                add("passed" if ok else "failed", "staff", aid, f"Staff {sid} qualification, availability, break and overlap checked for {role}.")
                staff_busy[sid].append((start,end,aid))
            technical_role = "technical_support" if role_counts["technical_support"] else "technical_facilitator"
            if role_counts[required_role] < required_n or role_counts[technical_role] < tech_n:
                add("failed", "staff_coverage", aid, "Required activity role or technical support count is not covered.")
            else: add("passed", "staff_coverage", aid, "Required role and technical support counts are covered.")
            if "lab_benches" in expected_features or "specialist_lab" in expected_features:
                if role_counts["lab_technician"] < 1 or "controlled_access" not in features.get(room_id,set()): add("failed", "safety", aid, "Lab activity lacks lab technician or controlled-access room feature.")
            if "food_service" in expected_features:
                if role_counts["food_service_lead"] < 1 or "food_service" not in features.get(room_id,set()): add("failed", "safety", aid, "Food service lacks qualified lead or approved room feature.")
    # Hard dependencies: only scheduled-to-scheduled placement can violate temporal order.
    for dep in deps:
        source, target = _s(dep.get("source_activity")), _s(dep.get("target_activity"))
        if _s(dep.get("constraint_type")).lower() != "hard": continue
        if source in placed and target in placed:
            try: gap = int(dep.get("minimum_gap_minutes", 0))
            except (TypeError, ValueError): gap = 0
            ok = placed[source]["_end"] + gap <= placed[target]["_start"]
            add("passed" if ok else "failed", "activity_dependency", _s(dep.get("dependency_id")), f"Hard dependency {source} -> {target} {'satisfied' if ok else 'violated'}.")
        elif source in placed and target not in placed:
            target_row = next((r for r in activity.get("deferred_activities", []) + activity.get("cancelled_activities", []) + activity.get("unscheduled_activities", []) if _s(r.get("activity_id")) == target), {})
            add("warning", "dependency_disposition", _s(dep.get("dependency_id")), f"Dependent activity {target} is not scheduled; review its declared disposition and downstream consequences.", {"source": source, "target_status": target_row.get("status")})
        elif target in placed and source not in placed:
            add("failed", "activity_dependency", _s(dep.get("dependency_id")), f"Dependent activity {target} is scheduled although prerequisite {source} is not scheduled.")
        else:
            missing = { _s(r.get("activity_id")): r.get("status", "deferred" if r in activity.get("deferred_activities", []) else "") for key in ("deferred_activities", "cancelled_activities", "unscheduled_activities") for r in activity.get(key, []) if isinstance(r, dict) }
            if source in missing or target in missing:
                add("warning", "dependency_disposition", _s(dep.get("dependency_id")), f"Hard dependency {source} -> {target} has an unplaced endpoint; permitted deferrals are reported separately and not treated as placements.", {"source_status": missing.get(source), "target_status": missing.get(target)})
    # Soft dependencies are warnings only.
    for dep in deps:
        if _s(dep.get("constraint_type")).lower() != "soft": continue
        a,b = placed.get(_s(dep.get("source_activity"))), placed.get(_s(dep.get("target_activity")))
        if a and b:
            try: gap=int(dep.get("minimum_gap_minutes",0))
            except (ValueError,TypeError): gap=0
            if a["_end"] + gap > b["_start"]: add("warning", "soft_dependency", _s(dep.get("dependency_id")), "Soft dependency condition is unmet; this is a penalty, not a hard violation.")
    failed = sum(c["status"] == "failed" for c in checks)
    unknown = sum(c["status"] == "unknown" for c in checks)
    permitted_deferrals = [c["subject_id"] for c in checks if c["category"] == "disposition" and c["status"] == "permitted" and "Deferred" in c["detail"]]
    permitted_cancellations = [c["subject_id"] for c in checks if c["category"] == "disposition" and c["status"] == "permitted" and "Cancelled" in c["detail"]]
    return {"validator": "independent dataset-based feasibility checks", "optimality": "No optimality claim; this validates constraints only.",
            "overall_status": "violations_found" if failed else "incomplete_verification" if unknown else "verified_with_warnings" if any(c["status"] == "warning" for c in checks) else "verified",
            "summary": {"checks": len(checks), "passed": sum(c["status"] == "passed" for c in checks), "failed": failed, "unknown": unknown, "warnings": sum(c["status"] == "warning" for c in checks)},
            "hard_constraint_violations": [c for c in checks if c["status"] == "failed"], "warnings": [c for c in checks if c["status"] == "warning"],
            "unknown_or_unverifiable": [c for c in checks if c["status"] == "unknown"], "permitted_deferrals": permitted_deferrals,
            "permitted_cancellations": permitted_cancellations, "checks": checks}
