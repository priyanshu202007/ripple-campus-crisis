import unittest
from unittest.mock import patch

from backend import main


class ValidateRequestTests(unittest.TestCase):
    def setUp(self):
        self.response = {"overall_status": "verified", "summary": {"checks": 0}}
        self.dataset_patch = patch.object(main, "load_dataset", return_value={})
        self.validator_patch = patch.object(main, "validate_schedules", return_value=self.response)
        self.dataset_patch.start()
        self.validator_mock = self.validator_patch.start()
        self.addCleanup(self.dataset_patch.stop)
        self.addCleanup(self.validator_patch.stop)

    def test_valid_schedules_preserve_validator_response(self):
        repair = {
            "scheduled_jobs": [{"restored_services": [{"service_id": "PWR-CAB"}]}],
            "unscheduled_jobs": [],
            "service_restorations": [],
        }
        activity = {
            "scheduled_activities": [{
                "room_occupancy": {"from": "09:45", "until": "11:40"},
                "assigned_staff": [{"staff_id": "P01", "role": "faculty_exam"}],
            }],
            "deferred_activities": [],
            "cancelled_activities": [],
            "unscheduled_activities": [],
        }

        result = main.validate({"repair_schedule": repair, "activity_schedule": activity})

        self.assertIs(result, self.response)
        self.validator_mock.assert_called_once_with({}, repair_schedule=repair, activity_schedule=activity)

    def test_malformed_room_occupancy_returns_422(self):
        with self.assertRaises(main.HTTPException) as caught:
            main.validate({
                "activity_schedule": {
                    "scheduled_activities": [{"room_occupancy": []}],
                },
            })

        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.detail["code"], "INVALID_SCHEDULE_PAYLOAD")
        self.assertEqual(caught.exception.detail["field"], "activity_schedule.scheduled_activities[0].room_occupancy")
        self.validator_mock.assert_not_called()

    def test_omitted_schedules_keep_generated_schedule_behavior(self):
        result = main.validate(None)

        self.assertIs(result, self.response)
        self.validator_mock.assert_called_once_with({}, repair_schedule=None, activity_schedule=None)


if __name__ == "__main__":
    unittest.main()
