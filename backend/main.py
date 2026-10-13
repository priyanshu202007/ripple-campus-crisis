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
    body = body or {}
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
