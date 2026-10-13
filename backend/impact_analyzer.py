"""Read-only impact tracing over the supplied incident and dataset tables."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable

import pandas as pd


def _split(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text or text.lower() == "none":
        return []
    return [part.strip() for part in text.split(";") if part.strip()]


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _records(dataset: dict[str, Any], filename: str) -> list[dict[str, Any]]:
    frame = dataset.get("_csv_data", {}).get(filename)
    return frame.to_dict(orient="records") if isinstance(frame, pd.DataFrame) else []


def trace_dependencies(
    direct_ids: Iterable[str], links: Iterable[dict[str, Any]], known_service_ids: set[str]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return reachable downstream IDs and one cycle-safe path per root/target."""
    graph: dict[str, list[str]] = defaultdict(list)
    for link in links:
        source = str(link.get("source_service", "")).strip()
        target = str(link.get("target_service", "")).strip()
        if source and target:
            graph[source].append(target)

    paths: list[dict[str, Any]] = []
    indirect: set[str] = set()
    for root in sorted(set(direct_ids)):
        queue = deque([(root, [root])])
        visited = {root}
        while queue:
            current, path = queue.popleft()
            for target in graph.get(current, []):
                if target in path or target in visited:
                    continue
                visited.add(target)
                next_path = [*path, target]
                if target not in set(direct_ids):
                    indirect.add(target)
                    paths.append({
                        "from_direct_service": root,
                        "to_service": target,
                        "service_path": next_path,
                        "known_service_ids": [sid for sid in next_path if sid in known_service_ids],
                        "unknown_service_ids": [sid for sid in next_path if sid not in known_service_ids],
                    })
                queue.append((target, next_path))
    return sorted(indirect), paths


def analyze_impacts(dataset: dict[str, Any]) -> dict[str, Any]:
    incident = dataset.get("documents", {}).get("incident.json")
    if not isinstance(incident, dict):
        return {
            "incident_id": None, "directly_affected_services": [],
            "indirectly_affected_services": [], "causal_paths": [],
            "rooms": [], "activities": [], "repairs": [],
            "unknown_or_incomplete_information": [
                {"type": "incident", "detail": "incident.json is missing or unreadable."}
            ],
        }

    service_rows = _records(dataset, "services.csv")
    links = _records(dataset, "service_links.csv")
    room_services = _records(dataset, "room_services.csv")
    activities = _records(dataset, "activities.csv")
    modes = _records(dataset, "activity_modes.csv")
    repairs = _records(dataset, "service_repair_map.csv")
    service_by_id = {str(r.get("service_id", "")).strip(): r for r in service_rows if str(r.get("service_id", "")).strip()}
    known_service_ids = set(service_by_id)
    declared_direct = incident.get("affected_service_ids", [])
    direct_ids = sorted({str(s).strip() for s in declared_direct if str(s).strip()}) if isinstance(declared_direct, list) else []
    indirect_ids, paths = trace_dependencies(direct_ids, links, known_service_ids)
    impacted_ids = set(direct_ids) | set(indirect_ids)
    unknown: list[dict[str, Any]] = []

    for service_id in direct_ids:
        if service_id not in known_service_ids:
            unknown.append({"type": "service", "id": service_id, "detail": "Incident lists this service, but services.csv has no matching record; state is unknown."})
    for link in links:
        for field in ("source_service", "target_service"):
            sid = str(link.get(field, "")).strip()
            if sid and sid not in known_service_ids:
                unknown.append({"type": "service_link", "id": sid, "detail": f"{field} references a service absent from services.csv."})
    for filename in ("services.csv", "service_links.csv", "room_services.csv", "activities.csv", "activity_modes.csv", "service_repair_map.csv"):
        if filename not in dataset.get("_csv_data", {}):
            unknown.append({"type": "dataset_file", "file": filename, "detail": "Required impact-analysis table is missing or unreadable."})

    service_state = {sid: row.get("initial_state", "") or "unknown" for sid, row in service_by_id.items()}
    service_effective = sorted(sid for sid in impacted_ids if sid in known_service_ids)
    direct_info = [
        {"service_id": sid, "state": service_state.get(sid, "unknown"),
         "state_from": service_by_id.get(sid, {}).get("state_from"),
         "evidence": "listed in incident.json affected_service_ids"}
        for sid in direct_ids
    ]
    indirect_info = [
        {"service_id": sid, "state": service_state.get(sid, "unknown"),
         "evidence": "reachable through service_links.csv; propagation indicates a dependency risk, not an independently confirmed outage"}
        for sid in indirect_ids
    ]

    rooms_by_service: dict[str, set[str]] = defaultdict(set)
    service_types_by_service: dict[str, str] = {}
    for sid, row in service_by_id.items():
        service_types_by_service[sid] = str(row.get("service_type", "")).strip()
    for row in room_services:
        room_id = str(row.get("room_id", "")).strip()
        sid = str(row.get("service_id", "")).strip()
        if room_id and sid:
            rooms_by_service[sid].add(room_id)
            if sid not in known_service_ids:
                unknown.append({"type": "room_service", "id": sid, "room_id": room_id, "detail": "Room mapping references a service absent from services.csv; state is unknown."})
    room_ids = sorted({rid for sid in impacted_ids for rid in rooms_by_service.get(sid, set())})
    affected_types = {service_types_by_service[sid] for sid in impacted_ids if sid in service_types_by_service}

    modes_by_activity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mode in modes:
        if _truthy(mode.get("allowed", "")):
            modes_by_activity[str(mode.get("activity_id", "")).strip()].append(mode)
    impacted_activities = []
    for activity in activities:
        aid = str(activity.get("activity_id", "")).strip()
        original_room = str(activity.get("original_room_id", "")).strip()
        considered_modes = modes_by_activity.get(aid, [])
        if not considered_modes:
            considered_modes = [activity]
        evidence = []
        for mode in considered_modes:
            required_types = set(_split(mode.get("required_service_types", "")))
            for room_row in room_services:
                rid = str(room_row.get("room_id", "")).strip()
                sid = str(room_row.get("service_id", "")).strip()
                stype = str(room_row.get("service_type", "")).strip()
                if stype in required_types and sid in impacted_ids:
                    evidence.append({
                        "mode_id": mode.get("mode_id"), "room_id": rid,
                        "service_id": sid, "service_type": stype,
                        "original_room": rid == original_room,
                        "impact_kind": "direct" if sid in direct_ids else "dependency_risk",
                    })
        if evidence:
            impacted_activities.append({
                "activity_id": aid,
                "activity_name": activity.get("activity_name"),
                "original_room_id": original_room,
                "impact_status": "potential_service_impact",
                "definitely_infeasible": False,
                "explanation": "At least one allowed mode and mapped room has a required service listed as directly affected or reachable through a service dependency. This does not prove infeasibility; alternate rooms, mode, timing, restoration, and other constraints require validation.",
                "evidence": evidence,
            })

    repair_records = []
    request_ids = {str(r.get("request_id", "")).strip() for r in _records(dataset, "work_requests.csv")}
    for repair in repairs:
        sid = str(repair.get("target_service_id", "")).strip()
        if sid in impacted_ids:
            request_id = str(repair.get("request_id", "")).strip()
            repair_records.append({**repair, "impact_kind": "direct" if sid in direct_ids else "dependency_risk"})
            if request_id not in request_ids:
                unknown.append({"type": "repair_mapping", "id": request_id, "service_id": sid, "detail": "Mapped request is absent from work_requests.csv."})
    mapped_services = {str(r.get("target_service_id", "")).strip() for r in repairs}
    for sid in service_effective:
        if sid not in mapped_services:
            unknown.append({"type": "repair_mapping", "id": sid, "detail": "No repair mapping was found for this impacted service; restoration path is unspecified in service_repair_map.csv."})

    for row in room_services:
        sid = str(row.get("service_id", "")).strip()
        if sid in impacted_ids and not str(row.get("room_id", "")).strip():
            unknown.append({"type": "room_service", "id": sid, "detail": "Impacted service mapping has no room ID."})
    return {
        "incident_id": incident.get("incident_id"),
        "scenario_name": incident.get("scenario_name"),
        "incident_start": incident.get("incident_start"),
        "directly_affected_services": direct_info,
        "indirectly_affected_services": indirect_info,
        "causal_paths": paths,
        "rooms": [
            {"room_id": rid, "linked_affected_services": sorted(sid for sid in impacted_ids if rid in rooms_by_service.get(sid, set()))}
            for rid in room_ids
        ],
        "activities": impacted_activities,
        "repairs": repair_records,
        "unknown_or_incomplete_information": unknown,
        "analysis_note": "Indirect dependency reachability is reported as a risk path. Service links require an upstream state; they do not alone establish that the target is unavailable. Activity entries are potential impacts, not feasibility conclusions.",
    }
