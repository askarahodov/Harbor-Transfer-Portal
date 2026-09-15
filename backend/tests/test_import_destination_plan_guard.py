from app.services.import_destination_plan import ImportDestinationPlanOrchestrator


def test_destination_plan_orchestrator_imports() -> None:
    assert ImportDestinationPlanOrchestrator.__name__ == "ImportDestinationPlanOrchestrator"
