def test_destination_mapping_precedence_documented() -> None:
    # The behavioral precedence itself is covered in test_import_destination_plan.py.
    # This sentinel keeps the contract visible when focused test selection is used.
    precedence = ("artifact_override", "source_project_mapping", "kind_default")
    assert precedence[0] == "artifact_override"
