"""Structural regression checks for the distributable Apple Health Shortcut."""

from __future__ import annotations

import plistlib
import unittest
from pathlib import Path


SHORTCUT_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "shortcuts"
    / "apple-health-sync.shortcut.plist"
)

# Find Health Samples picker labels differ from HealthKit SDK names; these are
# the labels the iOS picker actually shows (e.g. "Active Calories", not
# "Active Energy Burned"; "Sleep", not "Sleep Analysis").
EXPECTED_QUERY_TYPES = ["Steps", "Active Calories", "Sleep", "Heart Rate Variability SDNN"]

METRIC_DICTIONARY_NAMES = ["Step Metric", "Energy Metric", "Sleep Metric", "HRV Metric"]


def _text_value(field: object) -> str | None:
    if not isinstance(field, dict):
        return None
    value = field.get("Value")
    if not isinstance(value, dict):
        return None
    string = value.get("string")
    return string if isinstance(string, str) else None


def _load_workflow() -> dict:
    return plistlib.loads(SHORTCUT_SOURCE.read_bytes())


def _health_queries(workflow: dict) -> list[dict]:
    return [
        action
        for action in workflow["WFWorkflowActions"]
        if action["WFWorkflowActionIdentifier"]
        == "is.workflow.actions.filter.health.quantity"
    ]


def _filter_templates(query: dict) -> list[dict]:
    return query["WFWorkflowActionParameters"]["WFContentItemFilter"]["Value"][
        "WFActionParameterFilterTemplates"
    ]


def _query_type(query: dict) -> str:
    type_filter = next(
        template for template in _filter_templates(query) if template["Property"] == "Type"
    )
    return type_filter["Values"]["Enumeration"]["Value"]


class AppleHealthShortcutArtifactTests(unittest.TestCase):
    def test_source_uses_current_export_root_keys(self) -> None:
        """Keep the source compatible with the current Shortcuts signing service."""
        workflow = _load_workflow()

        self.assertIn("WFWorkflowInputContentItemClasses", workflow)
        self.assertIn("WFWorkflowOutputContentItemClasses", workflow)
        self.assertIsInstance(workflow["WFWorkflowInputContentItemClasses"], list)
        self.assertIsInstance(workflow["WFWorkflowOutputContentItemClasses"], list)

    def test_shortcut_queries_all_supported_health_types(self) -> None:
        """One Find Health Samples query per synced metric, in a stable order."""
        workflow = _load_workflow()

        self.assertEqual(
            [_query_type(query) for query in _health_queries(workflow)],
            EXPECTED_QUERY_TYPES,
        )

    def test_point_in_time_samples_are_limited_to_the_current_calendar_day(self) -> None:
        """Avoid a rolling or unbounded Apple Health export."""
        workflow = _load_workflow()

        for query in _health_queries(workflow):
            if _query_type(query) == "Sleep":
                continue
            start_date_filter = next(
                template
                for template in _filter_templates(query)
                if template["Property"] == "Start Date"
            )
            # In Find Health Samples, 1002 is Shortcuts' native "Start Date is today".
            self.assertEqual(start_date_filter["Operator"], 1002)

    def test_sleep_samples_cover_the_last_two_days(self) -> None:
        """A night usually starts before midnight; "is today" would drop it."""
        workflow = _load_workflow()
        sleep_query = next(
            query for query in _health_queries(workflow) if _query_type(query) == "Sleep"
        )
        start_date_filter = next(
            template
            for template in _filter_templates(sleep_query)
            if template["Property"] == "Start Date"
        )

        # 1001 is "Start Date is in the last", unit 16384 is days.
        self.assertEqual(start_date_filter["Operator"], 1001)
        self.assertEqual(start_date_filter["Values"]["Number"], "2")
        self.assertEqual(start_date_filter["Values"]["Unit"], 16384)

    def test_each_query_loops_and_appends_one_metric_to_the_metrics_variable(self) -> None:
        """Every query gets repeat → dictionary → Add to Variable "Metrics" wiring."""
        workflow = _load_workflow()
        actions = workflow["WFWorkflowActions"]

        query_uuids = [
            query["WFWorkflowActionParameters"]["UUID"] for query in _health_queries(workflow)
        ]
        repeat_starts = [
            action["WFWorkflowActionParameters"]
            for action in actions
            if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.repeat.each"
            and action["WFWorkflowActionParameters"]["WFControlFlowMode"] == 0
        ]
        repeat_ends = [
            action["WFWorkflowActionParameters"]
            for action in actions
            if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.repeat.each"
            and action["WFWorkflowActionParameters"]["WFControlFlowMode"] == 2
        ]
        appends = [
            action["WFWorkflowActionParameters"]
            for action in actions
            if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.appendvariable"
        ]
        metric_dictionaries = {
            action["WFWorkflowActionParameters"]["CustomOutputName"]: action[
                "WFWorkflowActionParameters"
            ]
            for action in actions
            if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.dictionary"
            and action["WFWorkflowActionParameters"].get("CustomOutputName")
            in METRIC_DICTIONARY_NAMES
        }

        self.assertEqual(len(repeat_starts), len(EXPECTED_QUERY_TYPES))
        self.assertEqual(len(repeat_ends), len(EXPECTED_QUERY_TYPES))
        self.assertEqual(len(appends), len(EXPECTED_QUERY_TYPES))
        self.assertEqual(sorted(metric_dictionaries), sorted(METRIC_DICTIONARY_NAMES))

        # Each repeat loop iterates over its own query output.
        self.assertEqual(
            [start["WFInput"]["Value"]["OutputUUID"] for start in repeat_starts],
            query_uuids,
        )
        # Start/end pairs must share grouping identifiers.
        self.assertEqual(
            [start["GroupingIdentifier"] for start in repeat_starts],
            [end["GroupingIdentifier"] for end in repeat_ends],
        )
        # Every append pushes its loop's metric dictionary into "Metrics".
        self.assertEqual(
            [append["WFVariableName"] for append in appends],
            ["Metrics"] * len(EXPECTED_QUERY_TYPES),
        )
        self.assertEqual(
            [append["WFInput"]["Value"]["OutputUUID"] for append in appends],
            [metric_dictionaries[name]["UUID"] for name in METRIC_DICTIONARY_NAMES],
        )

    def test_import_question_targets_the_post_url(self) -> None:
        """Keep the per-user webhook question attached after action insertions."""
        workflow = _load_workflow()
        actions = workflow["WFWorkflowActions"]
        questions = workflow["WFWorkflowImportQuestions"]

        self.assertEqual(len(questions), 1)
        question = questions[0]
        self.assertEqual(question["ParameterKey"], "WFURL")
        target = actions[question["ActionIndex"]]
        self.assertEqual(
            target["WFWorkflowActionIdentifier"],
            "is.workflow.actions.downloadurl",
        )

    def test_payload_posts_accumulated_metrics_as_a_json_file(self) -> None:
        """Avoid the invalid Dictionary attachment that yields a zero-byte JSON body."""
        workflow = _load_workflow()
        actions = workflow["WFWorkflowActions"]

        set_value_actions = [
            action
            for action in actions
            if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.setvalueforkey"
        ]
        self.assertEqual(len(set_value_actions), 1)

        set_value = set_value_actions[0]["WFWorkflowActionParameters"]
        self.assertEqual(_text_value(set_value["WFDictionaryKey"]), "metrics")
        self.assertEqual(
            set_value["WFDictionaryValue"]["WFSerializationType"],
            "WFTextTokenString",
        )
        self.assertEqual(
            _text_value(set_value["WFDictionaryValue"]),
            "￼",
        )
        # The metrics list is the "Metrics" variable accumulated across all
        # four repeat loops, not a single loop's Repeat Results.
        attachment = set_value["WFDictionaryValue"]["Value"]["attachmentsByRange"]["{0, 1}"]
        self.assertEqual(attachment["Type"], "Variable")
        self.assertEqual(attachment["VariableName"], "Metrics")

        file_type_actions = [
            action["WFWorkflowActionParameters"]
            for action in actions
            if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.gettypeaction"
        ]
        self.assertEqual(len(file_type_actions), 2)
        plist_file = next(
            action
            for action in file_type_actions
            if action["CustomOutputName"] == "Payload Plist"
        )
        json_file = next(
            action
            for action in file_type_actions
            if action["CustomOutputName"] == "Payload JSON"
        )
        self.assertEqual(plist_file["WFFileType"], "com.apple.plist")
        self.assertEqual(
            plist_file["WFInput"]["Value"]["OutputUUID"],
            set_value["UUID"],
        )
        self.assertEqual(json_file["WFFileType"], "public.json")
        self.assertEqual(
            json_file["WFInput"]["Value"]["OutputUUID"],
            plist_file["UUID"],
        )

        post = next(
            action
            for action in actions
            if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
        )["WFWorkflowActionParameters"]
        self.assertEqual(post["WFHTTPBodyType"], "File")
        self.assertNotIn("WFJSONValues", post)
        request_variable = post["WFRequestVariable"]
        self.assertEqual(
            request_variable["WFSerializationType"],
            "WFTextTokenAttachment",
        )
        attachment = request_variable["Value"]
        self.assertEqual(attachment["OutputName"], "Payload JSON")
        self.assertEqual(attachment["OutputUUID"], json_file["UUID"])
        self.assertEqual(attachment["Type"], "ActionOutput")


if __name__ == "__main__":
    unittest.main()
