"""Entirely synthetic records: no real athlete identifiers or health data."""
import copy
import unittest
from continuity import (RecordError, contract_digest, evidence_known_by,
                        progression_record_issues, require_same_contract, timestamp,
                        validate_contract)


def sample():
    contract = {
        "decision_id": "synthetic-session", "version": 1, "athlete_key": "demo",
        "created_at": "2030-01-01T08:00:00+08:00",
        "evidence_cutoff": "2030-01-01T07:55:00+08:00",
        "session_date": "2030-01-01", "status": "active",
        "purpose": "Synthetic integrity example, not a workout.",
        "dose": {"duration_seconds": 60, "optional_work": []},
        "adaptation_hypothesis": "Demonstrate a record comparison.",
        "execution_rules": ["Do not execute this example as training."],
        "expected_response": "A correctly linked record.",
        "stop_rules": ["Stop this example on a validation error."],
        "review_fields": ["identity", "actual dose", "matched response"],
    }
    delivery = {"athlete_key": "demo", "decision_id": "synthetic-session", "version": 1,
                "session_date": "2030-01-01", "activity_id": "demo-only",
                "reviewed": True, "dose_matches": True,
                "stop_outcome": "no_trigger_reported", "lever_outcome": "positive",
                "reviewed_lever": "example-lever",
                "observed_at": "2030-01-01T10:00:00+08:00",
                "known_at": "2030-01-01T11:00:00+08:00"}
    response = {"athlete_key": "demo", "activity_id": "demo-only",
                "response_date": "2030-01-02", "reviewed": True,
                "absorption": "positive", "observed_at": "2030-01-02T07:00:00+08:00",
                "known_at": "2030-01-02T08:00:00+08:00"}
    return contract, delivery, response


class ContinuityTests(unittest.TestCase):
    def audit(self, c, d, r, levers=("example-lever",)):
        return progression_record_issues(c, d, r, decision_cutoff="2030-01-02T09:00:00+08:00",
                                         proposed_levers=levers)

    def test_matching_records(self):
        self.assertEqual(self.audit(*sample()), ())

    def test_round_trip_is_order_independent(self):
        c, _, _ = sample()
        self.assertEqual(require_same_contract(c, dict(reversed(list(c.items())))), contract_digest(c))

    def test_hidden_optional_work_rejected(self):
        c, _, _ = sample()
        saved = copy.deepcopy(c)
        saved["dose"]["optional_work"].append("undisclosed add-on")
        with self.assertRaises(RecordError): require_same_contract(c, saved)

    def test_changed_stop_rule_rejected(self):
        c, _, _ = sample()
        saved = copy.deepcopy(c); saved["stop_rules"] = ["different"]
        with self.assertRaises(RecordError): require_same_contract(c, saved)

    def test_unknown_fields_are_not_success(self):
        c, d, r = sample(); d.pop("stop_outcome"); r.pop("absorption")
        self.assertEqual(len(self.audit(c, d, r)), 2)

    def test_wrong_activity_rejected(self):
        c, d, r = sample(); r["activity_id"] = "another-session"
        self.assertTrue(self.audit(c, d, r))

    def test_wrong_athlete_rejected(self):
        c, d, r = sample(); r["athlete_key"] = "another-person"
        self.assertTrue(self.audit(c, d, r))

    def test_two_days_later_is_not_next_day(self):
        c, d, r = sample(); r["response_date"] = "2030-01-03"
        self.assertTrue(self.audit(c, d, r))

    def test_later_good_news_cannot_backdate_decision(self):
        c, d, r = sample(); r["known_at"] = "2030-01-02T12:00:00+08:00"
        self.assertTrue(self.audit(c, d, r))

    def test_candidate_is_not_prescription(self):
        c, d, r = sample(); c["status"] = "candidate"
        self.assertTrue(self.audit(c, d, r))

    def test_stopping_and_continuing_do_not_earn_promotion(self):
        for outcome in ("triggered_stopped", "triggered_continued", "unknown"):
            with self.subTest(outcome=outcome):
                c, d, r = sample(); d["stop_outcome"] = outcome
                self.assertTrue(self.audit(c, d, r))

    def test_mixed_response_not_erased_or_promoted(self):
        c, d, r = sample(); r["absorption"] = "mixed"
        r["components"] = {"technical": "positive", "recovery": "unresolved"}
        before = copy.deepcopy(r)
        self.assertTrue(self.audit(c, d, r)); self.assertEqual(r, before)

    def test_dose_drift_rejected(self):
        c, d, r = sample(); d["dose_matches"] = False
        self.assertTrue(self.audit(c, d, r))

    def test_multiple_or_missing_levers_rejected(self):
        for levers in ((), ("pace", "volume"), ("",)):
            self.assertTrue(self.audit(*sample(), levers=levers))

    def test_naive_timestamps_rejected(self):
        with self.assertRaises(RecordError): timestamp("2030-01-02T08:00:00")

    def test_equivalent_timezones_compare_correctly(self):
        self.assertTrue(evidence_known_by({"observed_at": "2030-01-02T07:00:00+08:00",
            "known_at": "2030-01-02T08:00:00+08:00"}, "2030-01-02T00:00:00Z"))

    def test_future_evidence_in_contract_rejected(self):
        c, _, _ = sample(); c["evidence_cutoff"] = "2030-01-01T09:00:00+08:00"
        with self.assertRaises(RecordError): validate_contract(c)

    def test_missing_required_fields_rejected(self):
        c, _, _ = sample(); c.pop("purpose")
        with self.assertRaises(RecordError): validate_contract(c)

    def test_nonfinite_json_rejected(self):
        c, _, _ = sample(); c["dose"]["duration_seconds"] = float("nan")
        with self.assertRaises(RecordError): contract_digest(c)

    def test_missing_timestamp_fails_closed(self):
        c, d, r = sample(); r.pop("known_at")
        self.assertTrue(self.audit(c, d, r))

    def test_impossible_observation_order_rejected(self):
        with self.assertRaises(RecordError):
            evidence_known_by({"observed_at": "2030-01-02T09:00:00+08:00",
                "known_at": "2030-01-02T08:00:00+08:00"}, "2030-01-02T10:00:00+08:00")

    def test_incorrect_version_rejected(self):
        c, d, r = sample(); d["version"] = 2
        self.assertTrue(self.audit(c, d, r))

    def test_review_requires_actual_boolean(self):
        c, d, r = sample(); d["reviewed"] = "true"
        self.assertTrue(self.audit(c, d, r))

    def test_wrong_lever_outcome_rejected(self):
        c, d, r = sample(); d["reviewed_lever"] = "another-lever"
        self.assertTrue(self.audit(c, d, r))

    def test_future_contract_cannot_support_past_decision(self):
        c, d, r = sample(); c["created_at"] = "2030-01-03T08:00:00+08:00"
        self.assertTrue(self.audit(c, d, r))

    def test_records_are_not_mutated(self):
        records = sample(); before = copy.deepcopy(records)
        self.audit(*records); self.assertEqual(records, before)


if __name__ == "__main__": unittest.main()
