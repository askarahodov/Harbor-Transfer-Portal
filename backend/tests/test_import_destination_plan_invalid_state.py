from app.domain.imports import ImportPreviewState


def test_unresolved_destination_states_remain_non_importable() -> None:
    assert ImportPreviewState.UNKNOWN not in {
        ImportPreviewState.NEW,
        ImportPreviewState.SAME,
        ImportPreviewState.CONFLICT,
    }
    assert ImportPreviewState.ERROR not in {
        ImportPreviewState.NEW,
        ImportPreviewState.SAME,
        ImportPreviewState.CONFLICT,
    }
