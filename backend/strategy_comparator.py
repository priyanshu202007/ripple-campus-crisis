"""Run and compare two reproducible schedule construction strategies."""
from __future__ import annotations

from typing import Any

import pandas as pd

try:
    from .repair_scheduler import schedule_repairs
    from .activity_scheduler import schedule_activities
    from .validator import validate_schedules
except ImportError:
    from repair_scheduler import schedule_repairs
    from activity_scheduler import schedule_activities
    from validator import validate_schedules


def _int(value: Any) -> int | None:
    try: return int(value)
    except (TypeError, ValueError): return None


def _time(value: Any) -> int | None:
    try:
        hour, minute = str(value).strip().split(":", 1)
        return int(hour) * 60 + int(minute)
    except (ValueError, AttributeError): return None


def _rows(dataset: dict[str, Any], filename: str) -> list[dict[str, Any]]:
    frame = dataset.get("_csv_data", {}).get(filename)
    return frame.to_dict(orient="records") if isinstance(frame, pd.DataFrame) else []


def _metrics(repair: dict[str, Any], activity: dict[str, Any], validation: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    scheduled = activity.get("scheduled_activities", [])
    deferred = activity.get("deferred_activities", [])
    cancelled = activity.get("cancelled_activities", [])
    unscheduled = activity.get("unscheduled_activities", [])
    obj = activity.get("objective_summary", {})
    confirmed = [r for r in repair.get("service_restorations", []) if r.get("status") == "confirmed_restoration" and _time(r.get("restoration_time")) is not None]
    repair_jobs = repair.get("scheduled_jobs", [])
    finish_minutes = max((_time(r.get("engineer_occupied_until")) for r in repair_jobs if _time(r.get("engineer_occupied_until")) is not None), default=None)
    finish = f"{finish_minutes // 60:02d}:{finish_minutes % 60:02d}" if finish_minutes is not None else None
    repairs = repair.get("scheduled_jobs", [])
    engineers = sorted({str(j.get("engineer_id")) for j in repairs if j.get("engineer_id")})
    parts = [str(j.get("part_id")) for j in repairs if j.get("part_consumed") and j.get("part_id")]
    validation_summary = validation.get("summary", {})
    hard_failures = len(validation.get("hard_constraint_violations", []))
    unknown = len(validation.get("unknown_or_unverifiable", []))
    warnings = validation.get("warnings", [])
    priorities = {str(r.get("activity_id")): _int(r.get("priority_weight")) for r in _rows(dataset, "activities.csv")}
    if not priorities:
        # pandas table absent: metric cannot be derived and must not be guessed.
        undelivered_weight = None
    else:
        ids = {str(r.get("activity_id")) for r in deferred + cancelled + unscheduled}
        undelivered_weight = sum(priorities[aid] for aid in ids if aid in priorities and priorities[aid] is not None)
        if any(aid not in priorities or priorities[aid] is None for aid in ids): undelivered_weight = None
    cancellation_costs = {str(r.get("activity_id")): _int(r.get("cancellation_penalty")) for r in _rows(dataset, "activities.csv")}
    cancellation_cost = None if any(cancellation_costs.get(str(r.get("activity_id"))) is None for r in cancelled) else sum(cancellation_costs[str(r.get("activity_id"))] for r in cancelled)
    relocations = obj.get("relocations")
    delay = obj.get("priority_weighted_delay_minutes")
    soft_penalty = obj.get("soft_dependency_penalty_total")
    return {
        "delivered_activities": {"count": len(scheduled), "ids": sorted(str(r.get("activity_id")) for r in scheduled)},
        "deferred_activities": {"count": len(deferred), "ids": sorted(str(r.get("activity_id")) for r in deferred)},
        "cancelled_activities": {"count": len(cancelled), "ids": sorted(str(r.get("activity_id")) for r in cancelled)},
        "unscheduled_activities": {"count": len(unscheduled), "ids": sorted(str(r.get("activity_id")) for r in unscheduled)},
        "priority_weighted_undelivered": {"value": undelivered_weight, "supported": undelivered_weight is not None, "unit": "sum of supplied activity priority_weight"},
        "priority_weighted_delay": {"value": delay, "supported": delay is not None, "unit": "priority_weight times delayed minutes"},
        "cancellation_penalty_total": {"value": cancellation_cost, "supported": cancellation_cost is not None, "unit": "dataset cancellation_penalty values"},
        "relocations": {"value": relocations, "supported": relocations is not None, "unit": "count of scheduled activities outside original room"},
        "soft_dependency_penalties": {"value": soft_penalty, "supported": soft_penalty is not None, "unit": "sum of declared dependency penalty_weight"},
        "engineering_completion_time": {"value": finish, "supported": finish is not None, "unit": "latest scheduled job completion including verification", "confirmed_restorations": len(confirmed)},
        "resource_usage": {"scheduled_repair_jobs": len(repairs), "unique_engineers": len(engineers), "engineer_ids": engineers, "parts_consumed": len(parts), "part_units_by_id": {part: parts.count(part) for part in sorted(set(parts))}, "monetary_cost": {"value": None, "supported": False, "reason": "The dataset supplies no monetary or comparable resource cost rates."}},
        "validation": {"overall_status": validation.get("overall_status"), "hard_constraint_violations": hard_failures, "unknown_or_unverifiable": unknown, "warnings": len(warnings), "passed_checks": validation_summary.get("passed"), "failed_checks": validation_summary.get("failed"), "unknown_checks": validation_summary.get("unknown"), "warning_details": warnings, "unknown_details": validation.get("unknown_or_unverifiable", [])},
    }


def _objective_score(result: dict[str, Any]) -> tuple[Any, ...] | None:
    """Lexicographic score; uncertified schedules are never eligible to win."""
    validation = result["metrics"]["validation"]
    if validation["hard_constraint_violations"] or validation["unknown_or_unverifiable"]:
        return None
    metrics = result["metrics"]
    values = [metrics["priority_weighted_undelivered"]["value"], metrics["priority_weighted_delay"]["value"], metrics["relocations"]["value"], metrics["soft_dependency_penalties"]["value"]]
    if any(value is None for value in values): return None
    finish = _time(metrics["engineering_completion_time"]["value"])
    return (*values, finish if finish is not None else 10**6)


def compare_strategies(dataset: dict[str, Any]) -> dict[str, Any]:
    """Compare the named strategies using shared schedulers and hard rules."""
    strategies = {
        "A": {"name": "Early service restoration", "repair_order": "earliest projected finish, then request window and ID", "activity_order": "earliest original start, then supplied priority weight"},
        "B": {"name": "Preservation of high-priority activities", "repair_order": "descending sum of supplied priority weights for activities requiring mapped service types; prerequisite order preserved", "activity_order": "descending supplied priority weight, then original start"},
    }
    runs = {}
    for key, config in (("A", "early_service"), ("B", "high_priority")):
        repair = schedule_repairs(dataset, strategy=config)
        activity = schedule_activities(dataset, strategy=config, repair_plan=repair)
        validation = validate_schedules(dataset, repair, activity)
        runs[key] = {"strategy": strategies[key], "repair_schedule": repair, "activity_schedule": activity, "validation": validation, "metrics": _metrics(repair, activity, validation, dataset)}

    # Hard violations or unresolved checks make a strategy ineligible to win.
    for result in runs.values():
        v = result["metrics"]["validation"]
        result["eligible_for_objective_comparison"] = v["hard_constraint_violations"] == 0 and v["unknown_or_unverifiable"] == 0
    scores = {key: _objective_score(value) for key, value in runs.items()}
    winner = None
    if scores["A"] is not None and scores["B"] is not None:
        winner = "A" if scores["A"] < scores["B"] else "B" if scores["B"] < scores["A"] else "tie"
    return {
        "comparator": "deterministic lexicographic strategy comparison",
        "optimality": "Neither strategy proves global optimality or infeasibility.",
        "objective_order": ["hard feasibility and safety", "minimize priority-weighted undelivered activities", "minimize priority-weighted delay", "minimize relocations", "minimize soft-dependency penalties", "minimize engineering completion time"],
        "objective_limits": ["No monetary/resource cost rates or formal definition of unnecessary resource use are supplied; resource use is reported as counts and is not used to break ties.", "A strategy with hard violations or unknown checks is ineligible for a favorable comparison.", "Warnings are retained and shown; soft dependency penalties are compared using the dataset's declared weights."],
        "winner": winner,
        "scores": {key: list(value) if value is not None else None for key, value in scores.items()},
        "strategies": runs,
    }
