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


def _text_value(field: object) -> str | None:
    if not isinstance(field, dict):
        return None
    value = field.get("Value")
    if not isinstance(value, dict):
        return None
    string = value.get("string")
    return string if isinstance(string, str) else None


class AppleHealthShortcutArtifactTests(unittest.TestCase):
    def test_source_uses_current_export_root_keys(self) -> None:
        """Keep the source compatible with the current Shortcuts signing service."""
        workflow = plistlib.loads(SHORTCUT_SOURCE.read_bytes())

        self.assertIn("WFWorkflowInputContentItemClasses", workflow)
        self.assertIn("WFWorkflowOutputContentItemClasses", workflow)
        self.assertIsInstance(workflow["WFWorkflowInputContentItemClasses"], list)
        self.assertIsInstance(workflow["WFWorkflowOutputContentItemClasses"], list)

    def test_health_samples_are_limited_to_the_current_calendar_day(self) -> None:
        """Avoid a rolling or unbounded Apple Health export."""
        workflow = plistlib.loads(SHORTCUT_SOURCE.read_bytes())
        health_action = next(
            action
            for action in workflow["WFWorkflowActions"]
            if action["WFWorkflowActionIdentifier"]
            == "is.workflow.actions.filter.health.quantity"
        )
        filter_templates = health_action["WFWorkflowActionParameters"][
            "WFContentItemFilter"
        ]["Value"]["WFActionParameterFilterTemplates"]
        start_date_filter = next(
            template
            for template in filter_templates
            if template["Property"] == "Start Date"
        )

        # In Find Health Samples, 1002 is Shortcuts' native "Start Date is today".
        self.assertEqual(start_date_filter["Operator"], 1002)

    def test_import_question_targets_the_post_url(self) -> None:
        """Keep the per-user webhook question attached after action insertions."""
        workflow = plistlib.loads(SHORTCUT_SOURCE.read_bytes())
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

    def test_payload_posts_native_dictionary_as_a_json_file(self) -> None:
        """Avoid the invalid Dictionary attachment that yields a zero-byte JSON body."""
        workflow = plistlib.loads(SHORTCUT_SOURCE.read_bytes())
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
            "\ufffc",
        )
        attachment = set_value["WFDictionaryValue"]["Value"]["attachmentsByRange"]["{0, 1}"]
        self.assertEqual(attachment["OutputName"], "Repeat Results")
        self.assertEqual(attachment["Type"], "ActionOutput")

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
