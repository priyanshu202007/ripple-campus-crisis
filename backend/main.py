from typing import Any

import pandas as pd
from fastapi import FastAPI, Body, HTTPException
from fastapi.middleware.cors import CORSMiddleware

try:  # Supports both `uvicorn main:app` from backend/ and package imports.
    from .data_loader import get_dataset_summary, load_dataset
    from .impact_analyzer import analyze_impacts
    from .repair_scheduler import schedule_repairs
    from .activity_scheduler import schedule_activities
    from .validator import validate_schedules
    from .strategy_comparator import compare_strategies
except ImportError:
    from data_loader import get_dataset_summary, load_dataset
    from impact_analyzer import analyze_impacts
    from repair_scheduler import schedule_repairs
    from activity_scheduler import schedule_activities
    from validator import validate_schedules
    from strategy_comparator import compare_strategies

app = FastAPI(
    title="Ripple Campus Crisis API",
    description="Backend for the Ripple Campus in Crisis Datathon",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _dataset_error(status_code: int, code: str, message: str, files: list[dict[str, str]] | None = None) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    if files:
        detail["files"] = files
    return HTTPException(status_code=status_code, detail=detail)


def _public_records(dataset: dict[str, Any], filename: str) -> list[dict[str, str]]:
    frame = dataset.get("_csv_data", {}).get(filename)
    if not isinstance(frame, pd.DataFrame):
        recorded = [
            {"file": filename, "error": str(row.get("error", "Required table is missing or unreadable."))}
            for row in dataset.get("errors", []) if row.get("file") == filename
        ]
        if not recorded:
            recorded = [{"file": filename, "error": "Required table is missing or unreadable."}]
        raise _dataset_error(500, "DATASET_READ_ERROR", "A required dataset table could not be loaded.", recorded)
    return frame.to_dict(orient="records")


def _resource_response(resource: str, primary_file: str, related_files: dict[str, str]) -> dict[str, Any]:
    try:
        dataset = load_dataset()
        data = _public_records(dataset, primary_file)
        related_data = {name: _public_records(dataset, filename) for name, filename in related_files.items()}
        return {"resource": resource, "data": data, "related_data": related_data}
    except HTTPException:
        raise
    except Exception as exc:
        # Do not expose absolute paths or internal exception details to clients.
        raise _dataset_error(500, "INTERNAL_DATA_ERROR", f"Unexpected failure while preparing the {resource} resource.") from exc


def _validate_schedule_payload(value: Any, schedule_name: str) -> None:
    """Reject malformed nested structures before the feasibility validator reads them."""
    if value is None:  # Existing behavior: null is equivalent to an omitted schedule.
        return
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail={
            "code": "INVALID_SCHEDULE_PAYLOAD",
            "message": f"{schedule_name} must be a JSON object or null.",
            "field": schedule_name,
            "expected": "object",
        })

    collections = (
        ("scheduled_jobs", "unscheduled_jobs", "service_restorations")
        if schedule_name == "repair_schedule" else
        ("scheduled_activities", "deferred_activities", "cancelled_activities", "unscheduled_activities")
    )
    for key in collections:
        if key not in value:
            continue  # Keep partial/legacy schedule objects compatible.
        records = value[key]
        field_path = f"{schedule_name}.{key}"
        if not isinstance(records, list):
            raise HTTPException(status_code=422, detail={
                "code": "INVALID_SCHEDULE_PAYLOAD",
                "message": f"{field_path} must be an array of objects.",
                "field": field_path,
                "expected": "array",
            })
        for index, record in enumerate(records):
            record_path = f"{field_path}[{index}]"
            if not isinstance(record, dict):
                raise HTTPException(status_code=422, detail={
                    "code": "INVALID_SCHEDULE_PAYLOAD",
                    "message": f"{record_path} must be an object.",
                    "field": record_path,
                    "expected": "object",
                })
            nested_fields = {"scheduled_jobs": ("restored_services",), "scheduled_activities": ("room_occupancy", "assigned_staff")}.get(key, ())
            for nested in nested_fields:
                if nested not in record:
                    continue
                nested_value = record[nested]
                expected_type = list if nested == "assigned_staff" or nested == "restored_services" else dict
                if not isinstance(nested_value, expected_type):
                    expected = "array" if expected_type is list else "object"
                    nested_path = f"{record_path}.{nested}"
                    raise HTTPException(status_code=422, detail={
                        "code": "INVALID_SCHEDULE_PAYLOAD",
                        "message": f"{nested_path} must be a JSON {expected}.",
                        "field": nested_path,
                        "expected": expected,
                    })
                if expected_type is list:
                    for nested_index, nested_record in enumerate(nested_value):
                        if not isinstance(nested_record, dict):
                            nested_path = f"{record_path}.{nested}[{nested_index}]"
                            raise HTTPException(status_code=422, detail={
                                "code": "INVALID_SCHEDULE_PAYLOAD",
                                "message": f"{nested_path} must be an object.",
                                "field": nested_path,
                                "expected": "object",
                            })


@app.get("/")
def home():
    return {
        "status": "success",
        "message": "Ripple Campus Crisis Backend is running!"
    }


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy"
    }


@app.get("/api/dataset/summary")
def dataset_summary():
    return get_dataset_summary()


@app.get("/api/incident")
def incident():
    try:
        dataset = load_dataset()
        incident_data = dataset.get("documents", {}).get("incident.json")
        if incident_data is None:
            files = [{"file": "incident.json", "error": str(row.get("error", "Missing or unreadable."))}
                     for row in dataset.get("errors", []) if row.get("file") == "incident.json"]
            raise _dataset_error(500, "DATASET_READ_ERROR", "incident.json could not be loaded.", files or [{"file": "incident.json", "error": "Missing or unreadable."}])
        return incident_data
    except HTTPException:
        raise
    except Exception as exc:
        raise _dataset_error(500, "INTERNAL_DATA_ERROR", "Unexpected failure while loading the incident resource.") from exc


@app.get("/api/impacts")
def impacts():
    return analyze_impacts(load_dataset())


@app.post("/api/repairs/schedule")
def repair_schedule():
    return schedule_repairs(load_dataset())


@app.post("/api/activities/schedule")
def activity_schedule():
    return schedule_activities(load_dataset())


@app.post("/api/validate")
def validate(body: dict | None = Body(default=None)):
    """Validate supplied schedule payloads or freshly generated schedules."""
    if body is not None and not isinstance(body, dict):
        raise HTTPException(status_code=422, detail={
            "code": "INVALID_SCHEDULE_PAYLOAD",
            "message": "Request body must be a JSON object or null.",
            "field": "body",
            "expected": "object",
        })
    body = body or {}
    _validate_schedule_payload(body.get("repair_schedule"), "repair_schedule")
    _validate_schedule_payload(body.get("activity_schedule"), "activity_schedule")
    return validate_schedules(
        load_dataset(),
        repair_schedule=body.get("repair_schedule"),
        activity_schedule=body.get("activity_schedule"),
    )


@app.post("/api/strategies/compare")
def strategy_comparison():
    return compare_strategies(load_dataset())


@app.get("/api/buildings")
def buildings():
    return _resource_response("buildings", "buildings.csv", {})


@app.get("/api/rooms")
def rooms():
    return _resource_response("rooms", "rooms.csv", {
        "room_availability": "room_availability.csv",
        "room_features": "room_features.csv",
        "room_services": "room_services.csv",
    })


@app.get("/api/services")
def services():
    return _resource_response("services", "services.csv", {
        "service_links": "service_links.csv",
        "service_repair_map": "service_repair_map.csv",
    })


@app.get("/api/activities")
def activities():
    return _resource_response("activities", "activities.csv", {
        "activity_modes": "activity_modes.csv",
        "dependencies": "dependencies.csv",
    })


@app.get("/api/work-requests")
def work_requests():
    return _resource_response("work_requests", "work_requests.csv", {
        "service_repair_map": "service_repair_map.csv",
        "parts": "parts.csv",
        "engineering_personnel": "engineering_personnel.csv",
    })
