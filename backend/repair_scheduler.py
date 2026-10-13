"""Deterministic, read-only first-pass engineering repair scheduler."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import pandas as pd


def _rows(dataset: dict[str, Any], filename: str) -> list[dict[str, Any]]:
    table = dataset.get("_csv_data", {}).get(filename)
    return table.to_dict(orient="records") if isinstance(table, pd.DataFrame) else []


def _time(value: Any) -> int | None:
    try:
        hour, minute = str(value).strip().split(":", 1)
        result = int(hour) * 60 + int(minute)
        return result if 0 <= result < 24 * 60 else None
    except (ValueError, AttributeError):
        return None


def _fmt(minutes: int | None) -> str | None:
    if minutes is None:
        return None
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _list(value: Any) -> list[str]:
    text = str(value or "").strip()
    return [] if not text or text.lower() == "none" else [part.strip() for part in text.split(";") if part.strip()]


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def schedule_repairs(dataset: dict[str, Any], strategy: str = "baseline") -> dict[str, Any]:
    """Assign jobs by earliest prerequisite-ready start, then engineer ID.

    Jobs are continuous (never split). Active duration is rounded up to a whole
    minute from the supplied work rate; handover and verification follow it.
    The engineer remains occupied for all three phases, conservatively matching
    the documented one-job-at-a-time and verification availability rules.
    """
    required_files = [
        "work_requests.csv", "engineering_personnel.csv", "parts.csv",
        "service_repair_map.csv", "services.csv", "service_links.csv",
    ]
    incomplete: list[dict[str, Any]] = []
    for filename in required_files:
        if filename not in dataset.get("_csv_data", {}):
            incomplete.append({"file": filename, "detail": "Required repair scheduling table is missing or unreadable."})

    jobs = _rows(dataset, "work_requests.csv")
    engineers = _rows(dataset, "engineering_personnel.csv")
    parts_rows = _rows(dataset, "parts.csv")
    mappings = _rows(dataset, "service_repair_map.csv")
    services = _rows(dataset, "services.csv")
    links = _rows(dataset, "service_links.csv")
    engineers_by_id = {str(row.get("person_id", "")).strip(): row for row in engineers}
    service_by_id = {str(row.get("service_id", "")).strip(): row for row in services}
    mapping_by_job: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mapping in mappings:
        mapping_by_job[str(mapping.get("request_id", "")).strip()].append(mapping)
    jobs_by_id = {str(row.get("request_id", "")).strip(): row for row in jobs}

    for mapping in mappings:
        request_id = str(mapping.get("request_id", "")).strip()
        service_id = str(mapping.get("target_service_id", "")).strip()
        if request_id and request_id not in jobs_by_id:
            incomplete.append({"type": "repair_mapping", "job_id": request_id, "service_id": service_id or None, "detail": "Mapping references a request absent from work_requests.csv."})
        if service_id and service_id not in service_by_id:
            incomplete.append({"type": "repair_mapping", "job_id": request_id or None, "service_id": service_id, "detail": "Mapping references a service absent from services.csv."})

    inventory: dict[str, int] = {}
    compatible: dict[str, set[str]] = {}
    for part in parts_rows:
        part_id = str(part.get("part_id", "")).strip()
        try:
            inventory[part_id] = max(0, int(part.get("quantity_available", "0")) - int(part.get("reserved_quantity", "0")))
        except (TypeError, ValueError):
            inventory[part_id] = 0
            incomplete.append({"type": "inventory", "id": part_id, "detail": "Part quantity or reserved quantity is missing/invalid; usable stock treated as unknown and unavailable."})
        compatible[part_id] = set(_list(part.get("compatible_job_ids", "")))

    # Detect dependency cycles and missing prerequisite IDs before scheduling.
    dependency_graph: dict[str, list[str]] = defaultdict(list)
    for job_id, job in jobs_by_id.items():
        prerequisite = str(job.get("prerequisite_request_id", "")).strip()
        if prerequisite:
            if prerequisite not in jobs_by_id:
                incomplete.append({"type": "prerequisite", "job_id": job_id, "id": prerequisite, "detail": "Prerequisite request is not present in work_requests.csv."})
            else:
                dependency_graph[prerequisite].append(job_id)
    visiting: set[str] = set()
    visited: set[str] = set()
    cycle_jobs: set[str] = set()

    def visit(job_id: str, trail: list[str]) -> None:
        if job_id in visiting:
            cycle_jobs.update(trail[trail.index(job_id):])
            return
        if job_id in visited:
            return
        visiting.add(job_id)
        for child in dependency_graph.get(job_id, []):
            visit(child, [*trail, child])
        visiting.remove(job_id)
        visited.add(job_id)

    for job_id in jobs_by_id:
        visit(job_id, [job_id])

    if strategy == "early_service":
        # Earliest projected completion first; use the fastest actually eligible
        # qualified engineer, without changing any eligibility or feasibility rule.
        def completion_key(row: dict[str, Any]) -> tuple[int, int, str]:
            skill = str(row.get("required_skill", "")).strip()
            units = None
            try:
                units = float(row.get("work_units", ""))
            except (TypeError, ValueError):
                pass
            rates = []
            for eid in _list(row.get("eligible_staff", "")):
                engineer = engineers_by_id.get(eid)
                if not engineer or skill not in {str(engineer.get("skill", "")).strip(), *_list(engineer.get("secondary_skills", ""))} or not _bool(engineer.get("verification_authorized", "false")):
                    continue
                try: rates.append(float(engineer.get("work_units_per_hour", "")))
                except (TypeError, ValueError): continue
            try:
                extra = int(row.get("estimated_handover_minutes", 0)) + int(row.get("verification_minutes", 0))
            except (TypeError, ValueError): extra = 0
            duration = math.ceil(units / max(rates) * 60) + extra if units is not None and rates and max(rates) > 0 else 24 * 60
            earliest = _time(row.get("earliest_start"))
            return ((earliest if earliest is not None else 24 * 60) + duration, earliest if earliest is not None else 24 * 60, str(row.get("request_id", "")))

        ordered = sorted(jobs, key=completion_key)
    elif strategy == "high_priority":
        # Aggregate only supplied activity priority_weight values for activities
        # requiring the mapped service type. These are ordering keys, not new costs.
        service_type = {str(r.get("service_id", "")).strip(): str(r.get("service_type", "")).strip() for r in services}
        demand: dict[str, int] = defaultdict(int)
        for act in _rows(dataset, "activities.csv"):
            try: weight = int(act.get("priority_weight", 0))
            except (TypeError, ValueError): continue
            for service_type_id in _list(act.get("required_service_types", "")):
                demand[service_type_id] += weight

        def priority_key(row: dict[str, Any]) -> tuple[int, int, str]:
            targets = _list(row.get("target_service_ids", ""))
            score = sum(demand.get(service_type.get(sid, ""), 0) for sid in targets)
            earliest = _time(row.get("earliest_start"))
            return (-score, earliest if earliest is not None else 24 * 60, str(row.get("request_id", "")))

        ordered = sorted(jobs, key=priority_key)
        # Priority may not move a dependent request ahead of its prerequisite.
        by_job = {str(r.get("request_id", "")).strip(): r for r in jobs}
        rank = {str(r.get("request_id", "")).strip(): i for i, r in enumerate(ordered)}
        remaining = set(by_job)
        topological = []
        while remaining:
            ready = [jid for jid in remaining if not str(by_job[jid].get("prerequisite_request_id", "")).strip() or str(by_job[jid].get("prerequisite_request_id", "")).strip() not in remaining]
            if not ready:  # Leave cycles for the existing cycle diagnostic.
                topological.extend(by_job[jid] for jid in sorted(remaining, key=lambda x: rank.get(x, 0)))
                break
            jid = min(ready, key=lambda x: rank.get(x, 0))
            topological.append(by_job[jid])
            remaining.remove(jid)
        ordered = topological
    else:
        ordered = sorted(jobs, key=lambda row: (_time(row.get("earliest_start")) if _time(row.get("earliest_start")) is not None else 24 * 60, str(row.get("request_id", ""))))
    if strategy in {"early_service", "high_priority"}:
        # Both strategies preserve the request prerequisite DAG while choosing
        # among currently ready jobs according to their own deterministic rank.
        by_job = {str(r.get("request_id", "")).strip(): r for r in jobs}
        rank = {str(r.get("request_id", "")).strip(): i for i, r in enumerate(ordered)}
        remaining = set(by_job)
        topological = []
        while remaining:
            ready = [jid for jid in remaining if not str(by_job[jid].get("prerequisite_request_id", "")).strip() or str(by_job[jid].get("prerequisite_request_id", "")).strip() not in remaining]
            if not ready:
                topological.extend(by_job[jid] for jid in sorted(remaining, key=lambda x: rank.get(x, 0)))
                break
            jid = min(ready, key=lambda x: rank.get(x, 0))
            topological.append(by_job[jid])
            remaining.remove(jid)
        ordered = topological
    scheduled: list[dict[str, Any]] = []
    unscheduled: list[dict[str, Any]] = []
    engineer_busy_until: dict[str, int] = defaultdict(int)
    restoration_by_service: dict[str, int] = {}
    restoration_state_by_service: dict[str, str] = {}
    scheduled_by_job: dict[str, dict[str, Any]] = {}
    consumed_parts: dict[str, int] = defaultdict(int)
    unknown_restore_services: set[str] = set()
    job_ids = set(jobs_by_id)

    def fail(job_id: str, reason: str, status: str = "unscheduled") -> None:
        unscheduled.append({"request_id": job_id, "status": status, "reason": reason})

    for job in ordered:
        job_id = str(job.get("request_id", "")).strip()
        if not job_id:
            incomplete.append({"type": "work_request", "detail": "A work request has no request_id."})
            fail(job_id, "Missing request_id.")
            continue
        if job_id in cycle_jobs:
            fail(job_id, "Prerequisite cycle detected; heuristic cannot order this job.", "search_failure")
            continue
        if str(job.get("status", "")).strip().lower() != "open":
            fail(job_id, f"Request status is {job.get('status')!r}; only open requests are scheduled.", "not_scheduled")
            continue

        maps = mapping_by_job.get(job_id, [])
        if not maps:
            incomplete.append({"type": "repair_mapping", "job_id": job_id, "detail": "No service_repair_map.csv entry; no restoration will be claimed."})
        target_services = _list(job.get("target_service_ids", ""))
        mapping_targets = {str(m.get("target_service_id", "")).strip() for m in maps}
        if not target_services or not maps:
            fail(job_id, "Missing target service or explicit repair mapping.")
            continue
        if set(target_services) - mapping_targets:
            missing = sorted(set(target_services) - mapping_targets)
            incomplete.append({"type": "repair_mapping", "job_id": job_id, "service_ids": missing, "detail": "Target service is not explicitly mapped to this request; it will not be marked restored."})

        prerequisite_ids = [str(job.get("prerequisite_request_id", "")).strip()]
        prerequisite_ids = [value for value in prerequisite_ids if value]
        prerequisite_times = []
        blocked_reason = None
        for prerequisite in prerequisite_ids:
            completed = scheduled_by_job.get(prerequisite)
            if not completed:
                blocked_reason = f"Prerequisite request {prerequisite} was not scheduled/completed by this heuristic."
                break
            prerequisite_times.append(completed["restoration_minute"])
        if blocked_reason:
            fail(job_id, blocked_reason)
            continue

        map_requires_service = any(_bool(m.get("requires_prerequisite_service", "false")) for m in maps)
        required_service_states: dict[str, str] = {}
        for target in target_services:
            target_links = [link for link in links if str(link.get("target_service", "")).strip() == target]
            for link in target_links:
                source = str(link.get("source_service", "")).strip()
                required_state = str(link.get("required_state", "")).strip()
                if source and required_state:
                    required_service_states[source] = required_state
                elif source or required_state:
                    incomplete.append({"type": "service_prerequisite", "job_id": job_id, "service_id": target, "detail": "A service link has an incomplete source or required_state."})
        if map_requires_service and not required_service_states and not prerequisite_ids:
            incomplete.append({"type": "service_prerequisite", "job_id": job_id, "detail": "Repair map requires a prerequisite service, but neither a service link nor a request prerequisite is declared."})
            fail(job_id, "Required service prerequisite is unspecified; cannot safely schedule.")
            continue

        request_earliest = _time(job.get("earliest_start"))
        request_latest = _time(job.get("latest_start"))
        try:
            units = float(job.get("work_units", ""))
        except (TypeError, ValueError):
            units = -1
        skill = str(job.get("required_skill", "")).strip()
        part_id = str(job.get("part_required", "")).strip()
        if request_earliest is None or request_latest is None or request_earliest > request_latest or units < 0 or not skill:
            incomplete.append({"type": "work_request", "job_id": job_id, "detail": "Missing/invalid time window, work_units, or required_skill."})
            fail(job_id, "Required scheduling fields are missing or invalid.")
            continue
        if not part_id or part_id not in inventory:
            incomplete.append({"type": "inventory", "job_id": job_id, "part_id": part_id or None, "detail": "Required part is missing from parts.csv; job cannot be stocked."})
            fail(job_id, "Required part stock is unknown or missing.")
            continue
        if inventory[part_id] <= 0:
            fail(job_id, f"No usable {part_id} stock remains after reserved quantities and earlier scheduled jobs.")
            continue
        if job_id not in compatible.get(part_id, set()):
            fail(job_id, f"Part {part_id} does not declare this request in compatible_job_ids.")
            continue

        candidates = []
        for engineer_id in sorted(_list(job.get("eligible_staff", ""))):
            engineer = engineers_by_id.get(engineer_id)
            if not engineer:
                incomplete.append({"type": "engineer", "job_id": job_id, "id": engineer_id, "detail": "Eligible staff ID is absent from engineering_personnel.csv."})
                continue
            engineer_skills = {str(engineer.get("skill", "")).strip(), *_list(engineer.get("secondary_skills", ""))}
            if skill not in engineer_skills:
                continue
            try:
                rate = float(engineer.get("work_units_per_hour", ""))
                max_parallel = int(engineer.get("maximum_parallel_jobs", ""))
            except (TypeError, ValueError):
                incomplete.append({"type": "engineer", "job_id": job_id, "id": engineer_id, "detail": "Invalid rate or maximum_parallel_jobs."})
                continue
            if rate <= 0 or max_parallel < 1:
                continue
            # Rules say one job at a time; do not exploit any value above one.
            if not _bool(engineer.get("verification_authorized", "false")):
                continue
            available_from = _time(engineer.get("available_from"))
            available_until = _time(engineer.get("available_until"))
            if available_from is None or available_until is None:
                incomplete.append({"type": "engineer", "job_id": job_id, "id": engineer_id, "detail": "Engineer availability window is missing or invalid."})
                continue
            active_minutes = math.ceil(units / rate * 60)
            try:
                handover = int(job.get("estimated_handover_minutes", "0"))
                verification = int(job.get("verification_minutes", "0"))
            except (TypeError, ValueError):
                incomplete.append({"type": "work_request", "job_id": job_id, "detail": "Invalid handover or verification duration."})
                continue
            if handover < 0 or verification < 0:
                continue
            total_minutes = active_minutes + handover + verification
            start = max(request_earliest, available_from, engineer_busy_until[engineer_id], *prerequisite_times)
            if start > request_latest or start + total_minutes > available_until:
                continue

            unmet_service = None
            for source, required_state in sorted(required_service_states.items()):
                source_restoration = restoration_by_service.get(source)
                if source_restoration is not None:
                    source_state = restoration_state_by_service.get(source, "unknown") if source_restoration <= start else "not_yet_restored"
                else:
                    source_row = service_by_id.get(source)
                    if not source_row:
                        source_state = "unknown"
                    else:
                        source_state_time = _time(source_row.get("state_from"))
                        state = str(source_row.get("initial_state", "")).strip().lower()
                        source_state = state if source_state_time is not None and source_state_time <= start else "unknown"
                if source_state != required_state:
                    unmet_service = f"Service prerequisite {source} must be {required_state}; its state at {_fmt(start)} is {source_state}."
                    if source_state == "unknown":
                        incomplete.append({"type": "service_prerequisite", "job_id": job_id, "service_id": source, "detail": "Required upstream service is missing or has no usable state at the proposed start."})
                    break
            if unmet_service:
                continue
            candidates.append((start + total_minutes, engineer_id, engineer, start, active_minutes, handover, verification, total_minutes))

        if not candidates:
            reason = "No eligible, qualified, verification-authorized engineer can cover the continuous job within availability and request windows."
            if map_requires_service and required_service_states:
                reason += " Service prerequisites must also be restored before work starts."
            fail(job_id, reason, "search_failure")
            continue

        _, engineer_id, engineer, start, active_minutes, handover, verification, total_minutes = min(candidates, key=lambda item: (item[0], item[1]))
        work_end = start + active_minutes
        restoration_minute = work_end + handover + verification
        mapping_detail = []
        confirmed_services = []
        uncertain_services = []
        for mapping in maps:
            target = str(mapping.get("target_service_id", "")).strip()
            effective_rule = str(mapping.get("effective_after", "")).strip()
            restored_state = str(mapping.get("restored_state", "")).strip()
            if target not in target_services:
                continue
            supported_rule = effective_rule == "work_end_plus_handover_plus_verification"
            if supported_rule and restored_state and target in service_by_id and target in mapping_targets:
                restoration_by_service[target] = restoration_minute
                restoration_state_by_service[target] = restored_state
                confirmed_services.append(target)
            else:
                unknown_restore_services.add(target)
                uncertain_services.append(target)
                incomplete.append({"type": "restoration", "job_id": job_id, "service_id": target, "detail": f"Mapping state/effective_after is incomplete or unsupported ({restored_state!r}, {effective_rule!r}); restoration time is not confirmed."})
            mapping_detail.append({
                "service_id": target,
                "restored_state": restored_state or None,
                "effective_after": effective_rule or None,
                "status": "confirmed" if target in confirmed_services else "uncertain",
            })

        inventory[part_id] -= 1
        consumed_parts[part_id] += 1
        engineer_busy_until[engineer_id] = restoration_minute
        scheduled_job = {
            "request_id": job_id,
            "status": "scheduled",
            "engineer_id": engineer_id,
            "engineer_name": engineer.get("staff_name"),
            "skill_evidence": {"required_skill": skill, "matched_primary_or_secondary_skill": skill},
            "start": _fmt(start),
            "active_work_minutes": active_minutes,
            "work_end": _fmt(work_end),
            "handover_minutes": handover,
            "verification_minutes": verification,
            "engineer_occupied_until": _fmt(restoration_minute),
            "restoration_time": _fmt(restoration_minute) if confirmed_services else None,
            "part_id": part_id,
            "part_consumed": True,
            "restored_services": mapping_detail,
            "prerequisite_request_ids": prerequisite_ids,
        }
        scheduled.append(scheduled_job)
        scheduled_by_job[job_id] = {**scheduled_job, "restoration_minute": restoration_minute}

    scheduled_ids = {row["request_id"] for row in scheduled}
    for job_id in sorted(set(jobs_by_id) - scheduled_ids - {row["request_id"] for row in unscheduled}):
        fail(job_id, "Job was not processed by the heuristic.", "search_failure")

    repair_map_by_service: dict[str, list[str]] = defaultdict(list)
    for mapping in mappings:
        service_id = str(mapping.get("target_service_id", "")).strip()
        request_id = str(mapping.get("request_id", "")).strip()
        if service_id and request_id:
            repair_map_by_service[service_id].append(request_id)
    restoration_status = []
    for service_id, service in sorted(service_by_id.items()):
        mapped_jobs = repair_map_by_service.get(service_id, [])
        restored_job = next((scheduled_by_job[job_id] for job_id in mapped_jobs if job_id in scheduled_by_job and service_id in restoration_by_service), None)
        if restored_job:
            restoration_status.append({"service_id": service_id, "status": "confirmed_restoration", "restored_state": restoration_state_by_service.get(service_id), "restoration_time": _fmt(restoration_by_service[service_id]), "request_id": restored_job["request_id"]})
        elif service_id in unknown_restore_services:
            restoration_status.append({"service_id": service_id, "status": "uncertain_restoration", "restoration_time": None, "mapped_request_ids": mapped_jobs})
        elif mapped_jobs:
            failed_jobs = [row for row in unscheduled if row["request_id"] in mapped_jobs]
            if failed_jobs:
                restoration_status.append({
                    "service_id": service_id,
                    "status": "uncertain_restoration",
                    "restoration_time": None,
                    "mapped_request_ids": mapped_jobs,
                    "reason": "; ".join(row["reason"] for row in failed_jobs),
                })

    return {
        "scheduler": "deterministic earliest-completion heuristic",
        "optimality": "This is a heuristic schedule, not a global-optimality claim. Unscheduled jobs may reflect search/order limitations rather than proven infeasibility.",
        "assumptions": [
            "Jobs are ordered by earliest_start then request_id; eligible engineers are ranked by earliest completion then person_id.",
            "A job is continuous and not preempted; active duration is rounded up to whole minutes from work_units / work_units_per_hour.",
            "The assigned engineer is occupied through active work, handover, and verification; the rules require one job at a time and availability through verification.",
            "One item is consumed at job start; usable stock is quantity_available minus reserved_quantity.",
            "Service restoration is confirmed only for an explicit mapping with the documented effective_after rule, a restored_state, and a known service record.",
        ],
        "scheduled_jobs": scheduled,
        "unscheduled_jobs": unscheduled,
        "service_restorations": restoration_status,
        "inventory_after_schedule": {part: {"remaining_usable_quantity": quantity, "consumed": consumed_parts.get(part, 0)} for part, quantity in sorted(inventory.items())},
        "incomplete_input_information": incomplete,
        "counts": {"scheduled": len(scheduled), "unscheduled": len(unscheduled), "confirmed_restorations": sum(1 for row in restoration_status if row["status"] == "confirmed_restoration"), "uncertain_restorations": sum(1 for row in restoration_status if row["status"] == "uncertain_restoration")},
    }
