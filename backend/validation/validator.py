"""Read-only structural and domain validation for Ripple dataset inputs.

This module validates source-data consistency. It does not certify a revised
timetable or a complete recovery plan.
"""

from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Any


CSV_SCHEMAS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "activities.csv": (("activity_id", "activity_name", "original_room_id", "original_start", "original_end", "duration_minutes", "minimum_duration_minutes", "participants", "priority", "priority_weight", "required_features", "required_service_types", "earliest_start", "latest_start", "max_delay_minutes", "max_advance_minutes", "relocatable", "cancellation_allowed", "cancellation_penalty", "deferral_allowed", "remote_mode_allowed", "required_staff_role", "required_staff_count", "required_technical_support_count", "setup_minutes", "teardown_minutes", "dependency_policy", "activity_notes"), ("activity_id",)),
    "activity_modes.csv": (("mode_id", "activity_id", "mode_name", "duration_minutes", "required_features", "required_service_types", "allowed", "quality_penalty", "notes"), ("mode_id",)),
    "buildings.csv": (("building_id", "name", "building_type", "opening_time", "closing_time", "site_contact_id", "notes"), ("building_id",)),
    "constraints.csv": (("constraint_id", "level", "scope", "rule", "enforcement"), ("constraint_id",)),
    "dependencies.csv": (("dependency_id", "source_activity", "target_activity", "relationship", "constraint_type", "minimum_gap_minutes", "penalty_weight", "meaning"), ("dependency_id",)),
    "engineering_personnel.csv": (("person_id", "department", "skill", "secondary_skills", "work_units_per_hour", "available_from", "available_until", "maximum_parallel_jobs", "overtime_allowed", "verification_authorized", "staff_name", "notes"), ("person_id",)),
    "facility_contacts.csv": (("contact_id", "contact_name", "role", "primary_building_id", "contact_window", "notes"), ("contact_id",)),
    "parts.csv": (("part_id", "quantity_available", "storage_location", "reserved_quantity", "unit", "reorder_threshold", "compatible_job_ids", "notes"), ("part_id",)),
    "rooms.csv": (("room_id", "building_id", "room_name", "capacity", "features", "opening_time", "closing_time", "setup_buffer_minutes", "teardown_buffer_minutes", "accessibility", "facility_incharge_id", "requires_facilitator", "closure_windows"), ("room_id",)),
    "room_availability.csv": (("availability_id", "room_id", "day", "available_from", "available_until", "status", "reason"), ("availability_id",)),
    "room_features.csv": (("feature_id", "description", "mandatory_safety_feature"), ("feature_id",)),
    "room_services.csv": (("room_id", "service_id", "required_for_normal_operation", "service_type", "notes"), ("room_id", "service_id")),
    "services.csv": (("service_id", "service_type", "location_id", "initial_state", "state_from", "capacity_percent", "safety_critical", "restoration_rule", "incident_note"), ("service_id",)),
    "service_links.csv": (("source_service", "target_service", "relationship", "required_state", "description"), ("source_service", "target_service")),
    "service_repair_map.csv": (("request_id", "target_service_id", "restored_state", "effective_after", "requires_prerequisite_service", "notes"), ("request_id", "target_service_id")),
    "staff_assignments.csv": (("assignment_id", "activity_id", "staff_id", "assignment_role", "required", "assignment_type", "notes"), ("assignment_id",)),
    "staff_directory.csv": (("staff_id", "staff_name", "department", "primary_role", "qualifications", "available_from", "available_until", "break_start", "break_end", "max_concurrent_activities", "reassignable", "notes"), ("staff_id",)),
    "staff_roles.csv": (("role_id", "role_description", "required_qualification_tag", "can_substitute", "notes"), ("role_id",)),
    "work_requests.csv": (("request_id", "asset_id", "target_service_ids", "required_skill", "work_units", "prerequisite_request_id", "earliest_start", "latest_start", "part_required", "eligible_staff", "preemptible", "max_parallel_engineers", "estimated_handover_minutes", "verification_minutes", "status", "failure_mode", "restores_state", "work_notes"), ("request_id",)),
}

REQUIRED_FILES = (*CSV_SCHEMAS, "dataset_manifest.json", "incident.json", "optimization_rules.md")
IDENTIFIERS = {name: fields[0] for name, (_, fields) in CSV_SCHEMAS.items() if len(fields) == 1}
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
BOOL_FIELDS = {
    "activities.csv": ("relocatable", "cancellation_allowed", "deferral_allowed", "remote_mode_allowed"),
    "activity_modes.csv": ("allowed",), "buildings.csv": (),
    "constraints.csv": (), "dependencies.csv": (),
    "engineering_personnel.csv": ("overtime_allowed", "verification_authorized"),
    "facility_contacts.csv": (), "parts.csv": (), "rooms.csv": ("requires_facilitator",),
    "room_availability.csv": (), "room_features.csv": ("mandatory_safety_feature",),
    "room_services.csv": ("required_for_normal_operation",),
    "services.csv": ("safety_critical",),
    "service_links.csv": (),
    "service_repair_map.csv": ("requires_prerequisite_service",),
    "staff_assignments.csv": ("required",),
    "staff_directory.csv": ("reassignable",), "staff_roles.csv": ("can_substitute",),
    "work_requests.csv": ("preemptible",),
}
INTEGER_FIELDS = {
    "activities.csv": ("duration_minutes", "minimum_duration_minutes", "participants", "priority_weight", "max_delay_minutes", "max_advance_minutes", "cancellation_penalty", "required_staff_count", "required_technical_support_count", "setup_minutes", "teardown_minutes"),
    "activity_modes.csv": ("duration_minutes", "quality_penalty"),
    "dependencies.csv": ("minimum_gap_minutes", "penalty_weight"),
    "engineering_personnel.csv": ("maximum_parallel_jobs",),
    "parts.csv": ("quantity_available", "reserved_quantity", "reorder_threshold"),
    "rooms.csv": ("capacity", "setup_buffer_minutes", "teardown_buffer_minutes"),
    "services.csv": ("capacity_percent",),
    "staff_directory.csv": ("max_concurrent_activities",),
    "work_requests.csv": ("max_parallel_engineers", "estimated_handover_minutes", "verification_minutes"),
}
NONNEGATIVE_FIELDS = {
    filename: fields for filename, fields in INTEGER_FIELDS.items()
}
POSITIVE_INTEGER_FIELDS = {
    "activities.csv": ("duration_minutes", "minimum_duration_minutes", "participants"),
    "activity_modes.csv": ("duration_minutes",),
    "engineering_personnel.csv": ("maximum_parallel_jobs",),
    "rooms.csv": ("capacity",),
    "staff_directory.csv": ("max_concurrent_activities",),
    "work_requests.csv": ("max_parallel_engineers",),
}
FLOAT_FIELDS = {"engineering_personnel.csv": ("work_units_per_hour",), "work_requests.csv": ("work_units",)}
TIME_FIELDS = {
    "activities.csv": ("original_start", "original_end", "earliest_start", "latest_start"),
    "buildings.csv": ("opening_time", "closing_time"),
    "engineering_personnel.csv": ("available_from", "available_until"),
    "facility_contacts.csv": (), "rooms.csv": ("opening_time", "closing_time"),
    "room_availability.csv": ("available_from", "available_until"),
    "staff_directory.csv": ("available_from", "available_until", "break_start", "break_end"),
    "work_requests.csv": ("earliest_start", "latest_start"),
}
ENUMS = {
    ("services.csv", "initial_state"): {"available", "degraded", "unavailable"},
    ("service_repair_map.csv", "restored_state"): {"available", "degraded", "unavailable"},
    ("service_links.csv", "required_state"): {"available", "degraded", "unavailable"},
    ("work_requests.csv", "restores_state"): {"available", "degraded", "unavailable"},
    ("dependencies.csv", "constraint_type"): {"hard", "soft"},
    ("room_availability.csv", "status"): {"available", "unavailable"},
    ("work_requests.csv", "status"): {"open"},
}


class _Reporter:
    def __init__(self) -> None:
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.unknowns: list[dict[str, Any]] = []
        self.checks: set[str] = set()

    def add(self, level: str, code: str, message: str, file: str | None = None,
            record_id: str | None = None, row_number: int | None = None) -> None:
        finding: dict[str, Any] = {"code": code, "message": message, "file": file}
        if record_id is not None:
            finding["record_id"] = record_id
        if row_number is not None:
            finding["row_number"] = row_number
        getattr(self, level).append(finding)
        self.checks.add(code)


def _minutes(value: str) -> int | None:
    if not TIME_RE.fullmatch(value or ""):
        return None
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(";") if part.strip() and part.strip().lower() != "none"]


def _record_id(filename: str, row: dict[str, str]) -> str | None:
    field = IDENTIFIERS.get(filename)
    if field:
        return row.get(field) or None
    fields = {"room_services.csv": ("room_id", "service_id"), "service_links.csv": ("source_service", "target_service"), "service_repair_map.csv": ("request_id", "target_service_id")}.get(filename, ())
    return ",".join(row.get(field, "") for field in fields) or None


def _read_csv(path: Path, filename: str, reporter: _Reporter) -> tuple[list[str], list[dict[str, str]]] | None:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if reader.fieldnames is None:
                reporter.add("errors", "CSV_MISSING_HEADER", "CSV has no header row.", filename)
                return None
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                reporter.add("errors", "CSV_DUPLICATE_COLUMN", "CSV header contains duplicate column names.", filename)
                return None
            header = reader.fieldnames
            records = []
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    reporter.add("errors", "CSV_EXTRA_FIELDS", "CSV record has more fields than its header.", filename, row_number=row_number)
                if any(value is None for key, value in row.items() if key is not None):
                    reporter.add("errors", "CSV_MISSING_FIELD_VALUE", "CSV record has fewer fields than its header.", filename, row_number=row_number)
                records.append({key: value or "" for key, value in row.items() if key is not None})
            return header, records
    except (OSError, UnicodeError, csv.Error) as exc:
        reporter.add("errors", "CSV_MALFORMED", f"Could not parse CSV: {exc}", filename)
        return None


def _valid_incident(data: Any, reporter: _Reporter) -> None:
    filename = "incident.json"
    required = ("incident_id", "scenario_name", "incident_start", "simulation_start", "simulation_end", "time_step_minutes", "description", "affected_service_ids", "rules", "scenario_variants")
    if not isinstance(data, dict):
        reporter.add("errors", "INCIDENT_ROOT_INVALID", "Incident JSON root must be an object.", filename)
        return
    missing = [field for field in required if field not in data]
    if missing:
        reporter.add("errors", "INCIDENT_FIELDS_MISSING", f"Missing required incident fields: {', '.join(missing)}.", filename)
    for field in ("incident_id", "scenario_name", "description"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            reporter.add("errors", "INCIDENT_FIELD_INVALID", f"{field} must be a nonempty string.", filename, data.get("incident_id"))
    for field in ("incident_start", "simulation_start", "simulation_end"):
        if field in data and (not isinstance(data[field], str) or _minutes(data[field]) is None):
            reporter.add("errors", "INCIDENT_TIME_INVALID", f"{field} must use 24-hour HH:MM format.", filename, data.get("incident_id"))
    if "time_step_minutes" in data and (type(data["time_step_minutes"]) is not int or data["time_step_minutes"] <= 0):
        reporter.add("errors", "INCIDENT_STEP_INVALID", "time_step_minutes must be a positive integer.", filename, data.get("incident_id"))
    affected = data.get("affected_service_ids")
    if "affected_service_ids" in data and (not isinstance(affected, list) or not affected or any(not isinstance(item, str) or not item.strip() for item in affected)):
        reporter.add("errors", "INCIDENT_SERVICES_INVALID", "affected_service_ids must be a nonempty list of service IDs.", filename, data.get("incident_id"))
    rules = data.get("rules")
    rule_fields = ("unknown_service_state", "degraded_service", "restoration", "time_zone", "time_format", "activity_time_granularity_minutes")
    if not isinstance(rules, dict):
        reporter.add("errors", "INCIDENT_RULES_INVALID", "rules must be an object.", filename, data.get("incident_id"))
    else:
        absent = [field for field in rule_fields if field not in rules]
        if absent:
            reporter.add("errors", "INCIDENT_RULE_FIELDS_MISSING", f"Missing incident rule fields: {', '.join(absent)}.", filename, data.get("incident_id"))
        step = rules.get("activity_time_granularity_minutes")
        if step is not None and (type(step) is not int or step <= 0):
            reporter.add("errors", "INCIDENT_GRANULARITY_INVALID", "activity_time_granularity_minutes must be a positive integer.", filename, data.get("incident_id"))
    variants = data.get("scenario_variants")
    if not isinstance(variants, list) or not variants:
        reporter.add("errors", "INCIDENT_VARIANTS_INVALID", "scenario_variants must be a nonempty list.", filename, data.get("incident_id"))
    else:
        seen: set[str] = set()
        enabled_count = 0
        for index, variant in enumerate(variants):
            if not isinstance(variant, dict):
                reporter.add("errors", "INCIDENT_VARIANT_INVALID", "Each scenario variant must be an object.", filename, row_number=index + 1)
                continue
            missing_variant = [field for field in ("variant_id", "description", "enabled_by_default") if field not in variant]
            if missing_variant:
                reporter.add("errors", "INCIDENT_VARIANT_FIELDS_MISSING", f"Missing variant fields: {', '.join(missing_variant)}.", filename, row_number=index + 1)
            variant_id = variant.get("variant_id")
            if isinstance(variant_id, str) and variant_id in seen:
                reporter.add("errors", "INCIDENT_VARIANT_DUPLICATE", f"Duplicate variant_id {variant_id}.", filename, variant_id, index + 1)
            if isinstance(variant_id, str):
                seen.add(variant_id)
            if type(variant.get("enabled_by_default")) is not bool:
                reporter.add("errors", "INCIDENT_VARIANT_BOOLEAN_INVALID", "enabled_by_default must be a JSON boolean.", filename, str(variant_id) if variant_id else None, index + 1)
            elif variant["enabled_by_default"]:
                enabled_count += 1
        if enabled_count != 1:
            reporter.add("errors", "INCIDENT_DEFAULT_VARIANT_INVALID", "Exactly one scenario variant must be enabled by default.", filename)


def validate_dataset(data_dir: str | Path) -> dict[str, Any]:
    """Load official CSV/JSON inputs and return a JSON-serializable report."""
    root = Path(data_dir)
    reporter = _Reporter()
    tables: dict[str, list[dict[str, str]]] = {}
    headers: dict[str, list[str]] = {}
    files_checked: list[str] = []
    parsed_json: dict[str, Any] = {}

    for filename in REQUIRED_FILES:
        path = root / filename
        if not path.is_file():
            reporter.add("errors", "FILE_MISSING", f"Required file is missing: {filename}.", filename)
            continue
        files_checked.append(filename)
        if filename in CSV_SCHEMAS:
            parsed = _read_csv(path, filename, reporter)
            if parsed is None:
                continue
            header, records = parsed
            headers[filename] = header
            tables[filename] = records
            expected, key_fields = CSV_SCHEMAS[filename]
            missing = set(expected) - set(header)
            if missing:
                reporter.add("errors", "SCHEMA_MISSING_COLUMNS", f"Missing required columns: {', '.join(sorted(missing))}.", filename)
            unexpected = set(header) - set(expected)
            if unexpected:
                reporter.add("warnings", "SCHEMA_UNEXPECTED_COLUMNS", f"Unexpected columns: {', '.join(sorted(unexpected))}.", filename)
            if not missing:
                _validate_rows(filename, records, reporter)
        elif filename.endswith(".json"):
            try:
                parsed_json[filename] = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(parsed_json[filename], dict):
                    reporter.add("errors", "JSON_ROOT_INVALID", "JSON root must be an object.", filename)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                reporter.add("errors", "JSON_MALFORMED", f"Could not parse JSON: {exc}", filename)
        # optimization_rules.md is a required reference, not an operational table.

    if "incident.json" in parsed_json:
        _valid_incident(parsed_json["incident.json"], reporter)
    manifest = parsed_json.get("dataset_manifest.json")
    if isinstance(manifest, dict):
        declared = manifest.get("row_counts")
        if not isinstance(declared, dict):
            reporter.add("warnings", "MANIFEST_ROW_COUNTS_INVALID", "Manifest row_counts is not an object.", "dataset_manifest.json")
        else:
            for filename, rows in tables.items():
                expected_count = declared.get(filename)
                if isinstance(expected_count, int) and not isinstance(expected_count, bool) and expected_count != len(rows):
                    reporter.add("warnings", "ROW_COUNT_MISMATCH", f"Manifest declares {expected_count} rows; found {len(rows)}.", filename)
    elif "dataset_manifest.json" in files_checked:
        reporter.add("unknowns", "MANIFEST_UNAVAILABLE", "Manifest could not be used; row-count checks are incomplete.", "dataset_manifest.json")

    # A table with a missing header is not a valid target for referential checks.
    usable = {name: rows for name, rows in tables.items() if not (set(CSV_SCHEMAS[name][0]) - set(headers.get(name, [])))}
    _validate_references(usable, set(tables), headers, reporter)
    _validate_domains(usable, reporter)
    if "incident.json" in parsed_json and isinstance(parsed_json["incident.json"], dict):
        _validate_incident_services(parsed_json["incident.json"], usable, set(tables), reporter)

    counts = {filename: len(rows) for filename, rows in tables.items()}
    # An unresolved safety critical state is never a successful certification.
    status = "invalid" if reporter.errors else ("valid_with_warnings" if reporter.warnings or reporter.unknowns else "valid")
    return {"status": status, "files_checked": files_checked, "record_counts": counts,
            "checks_passed": sorted(reporter.checks), "errors": reporter.errors,
            "warnings": reporter.warnings, "unknowns": reporter.unknowns}


def _validate_rows(filename: str, rows: list[dict[str, str]], reporter: _Reporter) -> None:
    _, key_fields = CSV_SCHEMAS[filename]
    seen: set[tuple[str, ...]] = set()
    for row_number, row in enumerate(rows, start=2):
        label = _record_id(filename, row)
        key = tuple(row.get(field, "").strip() for field in key_fields)
        if not all(key):
            reporter.add("errors", "IDENTIFIER_EMPTY", f"Required identifier {', '.join(key_fields)} is empty.", filename, label, row_number)
        elif key in seen:
            reporter.add("errors", "IDENTIFIER_DUPLICATE", f"Duplicate identifier key ({', '.join(key_fields)})={','.join(key)}.", filename, label, row_number)
        seen.add(key)
        for field in INTEGER_FIELDS.get(filename, ()):
            value = row.get(field, "").strip()
            try:
                if not re.fullmatch(r"[+-]?\d+", value):
                    raise ValueError
                number = int(value)
            except ValueError:
                reporter.add("errors", "FIELD_INTEGER_INVALID", f"{field} must be an integer.", filename, label, row_number)
                continue
            if number < 0:
                reporter.add("errors", "FIELD_RANGE_INVALID", f"{field} cannot be negative.", filename, label, row_number)
            if field in POSITIVE_INTEGER_FIELDS.get(filename, ()) and number <= 0:
                reporter.add("errors", "FIELD_RANGE_INVALID", f"{field} must be positive.", filename, label, row_number)
        for field in FLOAT_FIELDS.get(filename, ()):
            value = row.get(field, "").strip()
            try:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError
            except ValueError:
                reporter.add("errors", "FIELD_NUMBER_INVALID", f"{field} must be a finite number.", filename, label, row_number)
                continue
            if number < 0 or (field == "work_units_per_hour" and number == 0):
                reporter.add("errors", "FIELD_RANGE_INVALID", f"{field} must be nonnegative and positive when used as a rate.", filename, label, row_number)
        for field in BOOL_FIELDS.get(filename, ()):
            if row.get(field, "").strip().lower() not in {"true", "false"}:
                reporter.add("errors", "FIELD_BOOLEAN_INVALID", f"{field} must be true or false.", filename, label, row_number)
        for field in TIME_FIELDS.get(filename, ()):
            value = row.get(field, "").strip()
            optional = filename == "staff_directory.csv" and field in {"break_start", "break_end"}
            if not value and optional:
                continue
            if _minutes(value) is None:
                reporter.add("errors", "TIME_INVALID", f"{field} must contain a valid 24-hour HH:MM time.", filename, label, row_number)
        if filename == "facility_contacts.csv":
            window = row.get("contact_window", "").strip()
            match = re.fullmatch(r"((?:[01]\d|2[0-3]):[0-5]\d)-((?:[01]\d|2[0-3]):[0-5]\d)", window)
            if not match or _minutes(match.group(1)) >= _minutes(match.group(2)):
                reporter.add("errors", "TIME_WINDOW_INVALID", "contact_window must be an increasing HH:MM-HH:MM interval.", filename, label, row_number)
        for (enum_file, field), allowed in ENUMS.items():
            if enum_file == filename and row.get(field, "").strip().lower() not in allowed:
                level = "unknowns" if filename == "services.csv" and field == "initial_state" else "errors"
                reporter.add(level, "SERVICE_STATE_UNKNOWN" if level == "unknowns" else "ENUM_INVALID", f"{field} has unrecognized value {row.get(field, '')!r}.", filename, label, row_number)


def _validate_references(tables: dict[str, list[dict[str, str]]], present_tables: set[str], headers: dict[str, list[str]], reporter: _Reporter) -> None:
    def check(source: str, field: str, target: str, target_field: str, *, optional: bool = False, list_field: bool = False) -> None:
        if source not in tables:
            return
        if target not in tables or target not in present_tables or target_field not in headers.get(target, []):
            reporter.add("unknowns", "REFERENCE_TARGET_UNAVAILABLE", f"Cannot validate {field}: target table/column {target}.{target_field} is unavailable.", source)
            return
        target_ids = {row.get(target_field, "").strip() for row in tables[target] if row.get(target_field, "").strip()}
        for row_number, row in enumerate(tables[source], start=2):
            value = row.get(field, "").strip()
            if optional and value.lower() in {"", "none"}:
                continue
            values = _split(value) if list_field else ([value] if value and value.lower() != "none" else [])
            if not values and not optional:
                reporter.add("unknowns", "REFERENCE_VALUE_MISSING", f"Required reference {field} is blank.", source, _record_id(source, row), row_number)
            for item in values:
                if item not in target_ids:
                    reporter.add("errors", "FOREIGN_KEY_INVALID", f"{field} references unknown {target}.{target_field} value {item}.", source, _record_id(source, row), row_number)

    pairs = (
        ("rooms.csv", "building_id", "buildings.csv", "building_id", False, False),
        ("rooms.csv", "facility_incharge_id", "facility_contacts.csv", "contact_id", True, False),
        ("buildings.csv", "site_contact_id", "facility_contacts.csv", "contact_id", True, False),
        ("facility_contacts.csv", "primary_building_id", "buildings.csv", "building_id", False, False),
        ("activities.csv", "original_room_id", "rooms.csv", "room_id", False, False),
        ("activities.csv", "required_staff_role", "staff_roles.csv", "role_id", False, False),
        ("activity_modes.csv", "activity_id", "activities.csv", "activity_id", False, False),
        ("dependencies.csv", "source_activity", "activities.csv", "activity_id", False, False),
        ("dependencies.csv", "target_activity", "activities.csv", "activity_id", False, False),
        ("room_availability.csv", "room_id", "rooms.csv", "room_id", False, False),
        ("room_services.csv", "room_id", "rooms.csv", "room_id", False, False),
        ("room_services.csv", "service_id", "services.csv", "service_id", False, False),
        ("service_links.csv", "source_service", "services.csv", "service_id", False, False),
        ("service_links.csv", "target_service", "services.csv", "service_id", False, False),
        ("service_repair_map.csv", "request_id", "work_requests.csv", "request_id", False, False),
        ("service_repair_map.csv", "target_service_id", "services.csv", "service_id", False, False),
        ("work_requests.csv", "asset_id", "services.csv", "service_id", False, False),
        ("work_requests.csv", "target_service_ids", "services.csv", "service_id", False, True),
        ("work_requests.csv", "prerequisite_request_id", "work_requests.csv", "request_id", True, False),
        ("work_requests.csv", "part_required", "parts.csv", "part_id", True, False),
        ("staff_assignments.csv", "activity_id", "activities.csv", "activity_id", False, False),
        ("staff_assignments.csv", "staff_id", "staff_directory.csv", "staff_id", False, False),
        ("work_requests.csv", "eligible_staff", "engineering_personnel.csv", "person_id", False, True),
        ("parts.csv", "compatible_job_ids", "work_requests.csv", "request_id", False, True),
        ("staff_directory.csv", "primary_role", "staff_roles.csv", "role_id", False, False),
    )
    for source, field, target, target_field, optional, list_field in pairs:
        check(source, field, target, target_field, optional=optional, list_field=list_field)

    feature_ids = {row.get("feature_id", "") for row in tables.get("room_features.csv", []) if row.get("feature_id")}
    for filename, field in (("rooms.csv", "features"), ("activities.csv", "required_features"), ("activity_modes.csv", "required_features")):
        if filename not in tables:
            continue
        if "room_features.csv" not in tables:
            reporter.add("unknowns", "REFERENCE_TARGET_UNAVAILABLE", f"Cannot validate {field}: room_features.csv is unavailable.", filename)
            continue
        for row_number, row in enumerate(tables[filename], start=2):
            values = _split(row.get(field, ""))
            if not values:
                reporter.add("unknowns", "FEATURE_REQUIREMENTS_MISSING", f"{field} has no declared feature values.", filename, _record_id(filename, row), row_number)
            for feature in values:
                if feature not in feature_ids:
                    reporter.add("errors", "FEATURE_UNKNOWN", f"{field} contains unknown feature {feature}.", filename, _record_id(filename, row), row_number)

    for filename, field, target_values in (
        ("activities.csv", "required_service_types", {"power", "network", "water", "av", "access"}),
        ("activity_modes.csv", "required_service_types", {"power", "network", "water", "av", "access"}),
        ("room_services.csv", "service_type", {"power", "network", "water", "av", "access"}),
    ):
        for row_number, row in enumerate(tables.get(filename, []), start=2):
            values = _split(row.get(field, "")) if field != "service_type" else [row.get(field, "").strip()]
            for value in values:
                if value and value.lower() != "none" and value not in target_values:
                    reporter.add("errors", "SERVICE_TYPE_INVALID", f"Unrecognized {field} value {value!r}.", filename, _record_id(filename, row), row_number)


def _validate_incident_services(incident: dict[str, Any], tables: dict[str, list[dict[str, str]]], present_tables: set[str], reporter: _Reporter) -> None:
    if "affected_service_ids" not in incident or not isinstance(incident["affected_service_ids"], list):
        return
    if "services.csv" not in tables:
        reporter.add("unknowns", "INCIDENT_SERVICE_TARGET_UNAVAILABLE", "Cannot validate incident affected_service_ids because services.csv is unavailable.", "incident.json")
        return
    ids = {row.get("service_id") for row in tables["services.csv"]}
    for service_id in incident["affected_service_ids"]:
        if service_id not in ids:
            reporter.add("errors", "INCIDENT_SERVICE_REFERENCE_INVALID", f"Incident references unknown service {service_id!r}.", "incident.json", str(service_id))


def _validate_domains(tables: dict[str, list[dict[str, str]]], reporter: _Reporter) -> None:
    for row_number, row in enumerate(tables.get("services.csv", []), start=2):
        state = row.get("initial_state", "").strip().lower()
        critical = row.get("safety_critical", "").strip().lower() == "true"
        if state not in {"available", "degraded", "unavailable"}:
            reporter.add("unknowns", "SERVICE_STATE_UNKNOWN", f"Unrecognized or missing service state {state!r}; it cannot be treated as usable.", "services.csv", _record_id("services.csv", row), row_number)
            if critical:
                reporter.add("errors", "SAFETY_STATE_UNRESOLVED", "Safety-critical service has an unresolved state.", "services.csv", _record_id("services.csv", row), row_number)

    for filename in ("room_services.csv", "service_links.csv", "service_repair_map.csv"):
        keys = CSV_SCHEMAS[filename][1]
        seen: set[tuple[str, ...]] = set()
        for row_number, row in enumerate(tables.get(filename, []), start=2):
            key = tuple(row.get(field, "").strip() for field in keys)
            if key in seen:
                reporter.add("errors", "COMPOSITE_KEY_DUPLICATE", f"Duplicate composite key ({', '.join(keys)})={','.join(key)}.", filename, _record_id(filename, row), row_number)
            seen.add(key)

    features = {row.get("feature_id", "") for row in tables.get("room_features.csv", [])}
    rooms = {row.get("room_id", ""): row for row in tables.get("rooms.csv", [])}
    for row_number, activity in enumerate(tables.get("activities.csv", []), start=2):
        aid = activity.get("activity_id") or None
        room_id = activity.get("original_room_id", "")
        room = rooms.get(room_id)
        if room is None:
            continue
        try:
            capacity = _int_value(room.get("capacity", ""))
            participants = _int_value(activity.get("participants", ""))
            if capacity < participants:
                reporter.add("errors", "ROOM_CAPACITY_INSUFFICIENT", "Original room capacity is below activity participant count.", "activities.csv", aid, row_number)
        except ValueError:
            reporter.add("errors", "ROOM_CAPACITY_DATA_INVALID", "Cannot compare room capacity and participant count because a value is invalid.", "activities.csv", aid, row_number)
        room_features = set(_split(room.get("features", "")))
        required = set(_split(activity.get("required_features", "")))
        if required and not required.issubset(room_features):
            reporter.add("errors", "ROOM_FEATURE_MISMATCH", f"Original room lacks required features: {', '.join(sorted(required - room_features))}.", "activities.csv", aid, row_number)
        elif not required:
            reporter.add("unknowns", "ACTIVITY_FEATURES_UNKNOWN", "Activity has no declared required features.", "activities.csv", aid, row_number)
        values = {field: _minutes(activity.get(field, "")) for field in ("original_start", "original_end", "earliest_start", "latest_start")}
        duration = _int_or_none(activity.get("duration_minutes"))
        minimum = _int_or_none(activity.get("minimum_duration_minutes"))
        start, end = values["original_start"], values["original_end"]
        if duration is not None and duration <= 0:
            reporter.add("errors", "ACTIVITY_DURATION_INVALID", "Activity duration must be positive.", "activities.csv", aid, row_number)
        if minimum is not None and minimum <= 0:
            reporter.add("errors", "ACTIVITY_DURATION_INVALID", "Minimum activity duration must be positive.", "activities.csv", aid, row_number)
        if minimum is not None and duration is not None and minimum > duration:
            reporter.add("errors", "ACTIVITY_DURATION_BOUNDS", "minimum_duration_minutes exceeds duration_minutes.", "activities.csv", aid, row_number)
        if None not in (start, end) and end <= start:
            reporter.add("errors", "ACTIVITY_INTERVAL_INVALID", "original_end must be later than original_start.", "activities.csv", aid, row_number)
        if None not in (start, end, duration) and end - start != duration:
            reporter.add("warnings", "BASELINE_DURATION_MISMATCH", "Original interval length differs from duration_minutes; this is a baseline consistency warning.", "activities.csv", aid, row_number)
        earliest, latest = values["earliest_start"], values["latest_start"]
        if None not in (earliest, latest) and earliest > latest:
            reporter.add("errors", "ACTIVITY_WINDOW_INVALID", "earliest_start is later than latest_start.", "activities.csv", aid, row_number)
        if None not in (start, earliest, latest) and not earliest <= start <= latest:
            reporter.add("warnings", "BASELINE_OUTSIDE_WINDOW", "Original start is outside the permitted revised start window.", "activities.csv", aid, row_number)
        cancel = activity.get("cancellation_allowed", "").strip().lower()
        if activity.get("priority", "").strip().lower() in {"critical", "mandatory"} and cancel == "true":
            reporter.add("errors", "CANCELLATION_POLICY_CONFLICT", "Critical or mandatory activity is marked cancellation_allowed=true.", "activities.csv", aid, row_number)

    modes = tables.get("activity_modes.csv", [])
    activities = {r.get("activity_id", ""): r for r in tables.get("activities.csv", [])}
    mode_ids = {r.get("mode_id", "") for r in modes}
    seen_modes: set[str] = set()
    for row_number, mode in enumerate(modes, start=2):
        mode_id = mode.get("mode_id", "")
        activity_id = mode.get("activity_id", "")
        if mode_id in seen_modes:
            reporter.add("errors", "IDENTIFIER_DUPLICATE", f"Duplicate mode identifier {mode_id}.", "activity_modes.csv", mode_id, row_number)
        seen_modes.add(mode_id)
        match = re.fullmatch(r"(.+)-M\d+", mode_id)
        if match and match.group(1) != activity_id:
            reporter.add("errors", "ACTIVITY_MODE_ID_MISMATCH", "mode_id prefix does not match activity_id.", "activity_modes.csv", mode_id, row_number)
        duration = _int_or_none(mode.get("duration_minutes"))
        if duration is not None and duration <= 0:
            reporter.add("errors", "MODE_DURATION_INVALID", "Mode duration must be positive.", "activity_modes.csv", mode_id, row_number)
        allowed = mode.get("allowed", "").strip().lower()
        if allowed == "false":
            reporter.add("warnings", "ACTIVITY_MODE_DISALLOWED", "Mode is declared but not selectable (allowed=false).", "activity_modes.csv", mode_id, row_number)
        if activity_id in activities and duration is not None:
            minimum = _int_or_none(activities[activity_id].get("minimum_duration_minutes"))
            maximum = _int_or_none(activities[activity_id].get("duration_minutes"))
            if minimum is not None and maximum is not None and not minimum <= duration <= maximum:
                reporter.add("errors", "MODE_DURATION_BOUNDS", "Mode duration is outside the activity's minimum-to-standard duration bounds.", "activity_modes.csv", mode_id, row_number)
    for activity_id, activity in activities.items():
        if not any(mode.get("activity_id") == activity_id for mode in modes):
            reporter.add("unknowns", "ACTIVITY_MODES_MISSING", "Activity has no declared mode; allowed-mode validation is incomplete.", "activities.csv", activity_id)

    for row_number, part in enumerate(tables.get("parts.csv", []), start=2):
        available = _int_or_none(part.get("quantity_available"))
        reserved = _int_or_none(part.get("reserved_quantity"))
        if available is not None and reserved is not None and reserved > available:
            reporter.add("errors", "INVENTORY_RESERVED_EXCEEDS", "reserved_quantity exceeds quantity_available.", "parts.csv", _record_id("parts.csv", part), row_number)

    requests = {row.get("request_id", ""): row for row in tables.get("work_requests.csv", [])}
    prereq_graph: dict[str, str] = {}
    for row_number, request in enumerate(tables.get("work_requests.csv", []), start=2):
        request_id = request.get("request_id", "")
        prereq = request.get("prerequisite_request_id", "").strip()
        if prereq and prereq.lower() != "none":
            if prereq not in requests:
                reporter.add("errors", "PREREQUISITE_INVALID", f"Unknown prerequisite request {prereq}.", "work_requests.csv", request_id or None, row_number)
            elif prereq == request_id:
                reporter.add("errors", "PREREQUISITE_SELF_REFERENCE", "Work request cannot depend on itself.", "work_requests.csv", request_id or None, row_number)
            else:
                prereq_graph[request_id] = prereq
    for start_id in prereq_graph:
        visited: set[str] = set()
        current = start_id
        while current in prereq_graph:
            if current in visited:
                reporter.add("errors", "PREREQUISITE_CYCLE", f"Prerequisite cycle includes {current}.", "work_requests.csv", start_id)
                break
            visited.add(current)
            current = prereq_graph[current]

    target_map: dict[str, set[str]] = {}
    for request in tables.get("work_requests.csv", []):
        target_map[request.get("request_id", "")] = set(_split(request.get("target_service_ids", "")))
    map_by_request: dict[str, list[dict[str, str]]] = {}
    for row in tables.get("service_repair_map.csv", []):
        map_by_request.setdefault(row.get("request_id", ""), []).append(row)
    for row_number, request in enumerate(tables.get("work_requests.csv", []), start=2):
        request_id = request.get("request_id", "")
        targets = target_map.get(request_id, set())
        mappings = map_by_request.get(request_id, [])
        mapped_targets = {mapping.get("target_service_id", "") for mapping in mappings}
        if not mappings and targets:
            reporter.add("errors", "REPAIR_MAPPING_MISSING", "Work request declares target service(s) but has no repair mapping.", "work_requests.csv", request_id or None, row_number)
        for missing in sorted(targets - mapped_targets):
            reporter.add("errors", "REPAIR_MAPPING_INCOMPLETE", f"No repair mapping covers target service {missing}.", "work_requests.csv", request_id or None, row_number)
        for extra in sorted(mapped_targets - targets):
            reporter.add("errors", "REPAIR_MAPPING_UNDECLARED_TARGET", f"Repair map covers undeclared target service {extra}.", "work_requests.csv", request_id or None, row_number)
        for mapping in mappings:
            restored = mapping.get("restored_state", "").strip().lower()
            restores = request.get("restores_state", "").strip().lower()
            if restored and restores and restored != restores:
                reporter.add("errors", "REPAIR_STATE_MISMATCH", f"Mapping restored_state {restored!r} differs from work request restores_state {restores!r}.", "service_repair_map.csv", _record_id("service_repair_map.csv", mapping))

    for row_number, dep in enumerate(tables.get("dependencies.csv", []), start=2):
        kind = dep.get("constraint_type", "").strip().lower()
        if kind not in {"hard", "soft"}:
            reporter.add("errors", "DEPENDENCY_CLASS_INVALID", f"constraint_type must be hard or soft, got {kind!r}.", "dependencies.csv", _record_id("dependencies.csv", dep), row_number)


def _int_value(value: str) -> int:
    if not re.fullmatch(r"[+-]?\d+", value.strip()):
        raise ValueError(value)
    return int(value)


def _int_or_none(value: str) -> int | None:
    try:
        return _int_value(value)
    except (TypeError, ValueError):
        return None

