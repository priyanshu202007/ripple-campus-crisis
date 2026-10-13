import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from backend.validation import validate_dataset


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "backend" / "data"


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "data"
        shutil.copytree(DATA, self.data_dir)

    def tearDown(self):
        self.temp.cleanup()

    def read_rows(self, filename):
        with (self.data_dir / filename).open(newline="", encoding="utf-8-sig") as stream:
            return list(csv.DictReader(stream))

    def write_rows(self, filename, rows):
        with (self.data_dir / filename).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_valid_dataset(self):
        report = validate_dataset(self.data_dir)
        self.assertIn(report["status"], {"valid", "valid_with_warnings"})
        self.assertEqual(report["record_counts"]["activities.csv"], 35)
        self.assertEqual(report["errors"], [])

    def test_missing_required_file(self):
        (self.data_dir / "incident.json").unlink()
        report = validate_dataset(self.data_dir)
        self.assertEqual(report["status"], "invalid")
        self.assertIn("FILE_MISSING", {finding["code"] for finding in report["errors"]})

    def test_malformed_csv_and_json(self):
        (self.data_dir / "activities.csv").write_text('activity_id,activity_name\n"unterminated', encoding="utf-8")
        (self.data_dir / "incident.json").write_text("{broken", encoding="utf-8")
        report = validate_dataset(self.data_dir)
        codes = {finding["code"] for finding in report["errors"]}
        self.assertIn("CSV_MALFORMED", codes)
        self.assertIn("JSON_MALFORMED", codes)

    def test_duplicate_identifier(self):
        rows = self.read_rows("activities.csv")
        rows[1]["activity_id"] = rows[0]["activity_id"]
        self.write_rows("activities.csv", rows)
        report = validate_dataset(self.data_dir)
        self.assertIn("IDENTIFIER_DUPLICATE", {finding["code"] for finding in report["errors"]})

    def test_invalid_foreign_key(self):
        rows = self.read_rows("rooms.csv")
        rows[0]["building_id"] = "BAD-BUILDING"
        self.write_rows("rooms.csv", rows)
        report = validate_dataset(self.data_dir)
        self.assertIn("FOREIGN_KEY_INVALID", {finding["code"] for finding in report["errors"]})

    def test_unknown_service_state(self):
        rows = self.read_rows("services.csv")
        rows[0]["initial_state"] = "unknown-ish"
        self.write_rows("services.csv", rows)
        report = validate_dataset(self.data_dir)
        self.assertIn("SERVICE_STATE_UNKNOWN", {finding["code"] for finding in report["unknowns"]})

    def test_reserved_inventory_exceeds_available(self):
        rows = self.read_rows("parts.csv")
        rows[0]["reserved_quantity"] = str(int(rows[0]["quantity_available"]) + 1)
        self.write_rows("parts.csv", rows)
        report = validate_dataset(self.data_dir)
        self.assertIn("INVENTORY_RESERVED_EXCEEDS", {finding["code"] for finding in report["errors"]})

    def test_inconsistent_repair_mapping(self):
        rows = self.read_rows("service_repair_map.csv")
        rows[0]["target_service_id"] = "NET-CORE"
        self.write_rows("service_repair_map.csv", rows)
        report = validate_dataset(self.data_dir)
        codes = {finding["code"] for finding in report["errors"]}
        self.assertTrue({"REPAIR_MAPPING_INCOMPLETE", "REPAIR_TARGET_MISMATCH"} & codes)

    def codes(self, report, level="errors"):
        return {finding["code"] for finding in report[level]}

    def test_missing_required_column(self):
        rows = self.read_rows("services.csv")
        for row in rows:
            row.pop("safety_critical")
        self.write_rows("services.csv", rows)
        report = validate_dataset(self.data_dir)
        self.assertIn("SCHEMA_MISSING_COLUMNS", self.codes(report))
        self.assertEqual(report["status"], "invalid")

    def test_invalid_time_value(self):
        rows = self.read_rows("activities.csv")
        rows[0]["original_start"] = "25:70"
        self.write_rows("activities.csv", rows)
        self.assertIn("TIME_INVALID", self.codes(validate_dataset(self.data_dir)))

    def test_nonfinite_and_noninteger_numbers(self):
        rows = self.read_rows("parts.csv")
        rows[0]["quantity_available"] = "NaN"
        self.write_rows("parts.csv", rows)
        report = validate_dataset(self.data_dir)
        self.assertIn("FIELD_INTEGER_INVALID", self.codes(report))

        rows = self.read_rows("activities.csv")
        rows[0]["participants"] = "Infinity"
        self.write_rows("activities.csv", rows)
        self.assertIn("FIELD_INTEGER_INVALID", self.codes(validate_dataset(self.data_dir)))

    def test_invalid_boolean(self):
        rows = self.read_rows("services.csv")
        rows[0]["safety_critical"] = "sometimes"
        self.write_rows("services.csv", rows)
        self.assertIn("FIELD_BOOLEAN_INVALID", self.codes(validate_dataset(self.data_dir)))

    def test_missing_incident_fields(self):
        incident_path = self.data_dir / "incident.json"
        incident = json.loads(incident_path.read_text(encoding="utf-8"))
        del incident["affected_service_ids"]
        incident_path.write_text(json.dumps(incident), encoding="utf-8")
        self.assertIn("INCIDENT_FIELDS_MISSING", self.codes(validate_dataset(self.data_dir)))

    def test_unknown_required_room_feature(self):
        rows = self.read_rows("activities.csv")
        rows[0]["required_features"] = "made_up_feature"
        self.write_rows("activities.csv", rows)
        self.assertIn("FEATURE_UNKNOWN", self.codes(validate_dataset(self.data_dir)))

    def test_original_room_capacity_below_participants(self):
        rows = self.read_rows("activities.csv")
        rows[0]["participants"] = "999"
        self.write_rows("activities.csv", rows)
        self.assertIn("ROOM_CAPACITY_INSUFFICIENT", self.codes(validate_dataset(self.data_dir)))

    def test_invalid_prerequisite(self):
        rows = self.read_rows("work_requests.csv")
        rows[0]["prerequisite_request_id"] = "J-DOES-NOT-EXIST"
        self.write_rows("work_requests.csv", rows)
        self.assertIn("FOREIGN_KEY_INVALID", self.codes(validate_dataset(self.data_dir)))

    def test_prerequisite_cycle(self):
        rows = self.read_rows("work_requests.csv")
        rows[0]["prerequisite_request_id"] = rows[1]["request_id"]
        rows[1]["prerequisite_request_id"] = rows[0]["request_id"]
        self.write_rows("work_requests.csv", rows)
        self.assertIn("PREREQUISITE_CYCLE", self.codes(validate_dataset(self.data_dir)))

    def test_unknown_safety_critical_service_state_is_invalid(self):
        rows = self.read_rows("services.csv")
        critical = next(row for row in rows if row["safety_critical"].lower() == "true")
        critical["initial_state"] = "mystery"
        self.write_rows("services.csv", rows)
        report = validate_dataset(self.data_dir)
        self.assertIn("SAFETY_STATE_UNRESOLVED", self.codes(report))
        self.assertEqual(report["status"], "invalid")

    def test_missing_repair_mapping(self):
        rows = self.read_rows("service_repair_map.csv")
        rows = [row for row in rows if row["request_id"] != "J01"]
        self.write_rows("service_repair_map.csv", rows)
        self.assertIn("REPAIR_MAPPING_MISSING", self.codes(validate_dataset(self.data_dir)))

    def test_repair_restored_state_mismatch(self):
        rows = self.read_rows("service_repair_map.csv")
        rows[0]["restored_state"] = "degraded"
        self.write_rows("service_repair_map.csv", rows)
        self.assertIn("REPAIR_STATE_MISMATCH", self.codes(validate_dataset(self.data_dir)))


if __name__ == "__main__":
    unittest.main()
