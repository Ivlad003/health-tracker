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

    def test_payload_uses_set_dictionary_value_for_repeat_results(self) -> None:
        """Avoid an invalid array/text-token combination that crashes Shortcuts on import."""
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

        post = next(
            action
            for action in actions
            if action["WFWorkflowActionIdentifier"] == "is.workflow.actions.downloadurl"
        )["WFWorkflowActionParameters"]
        self.assertEqual(post["WFHTTPBodyType"], "JSON")
        self.assertEqual(post["WFJSONValues"]["WFSerializationType"], "WFTextTokenAttachment")
        self.assertEqual(post["WFJSONValues"]["Value"]["OutputName"], "Sync Payload")


if __name__ == "__main__":
    unittest.main()
