from datetime import datetime
from unittest.mock import patch
from src.worker import is_surge_window


def test_is_surge_window_logic():
    # Calling is_surge_window returns a valid boolean
    result = is_surge_window()
    assert isinstance(result, bool)


def test_is_surge_window_boundaries():
    """Verify all minute and second condition branches in is_surge_window."""
    test_cases = [
        # (minute, second, expected_bool)
        (59, 30, True),
        (59, 45, True),
        (59, 29, False),
        (14, 30, True),
        (14, 20, False),
        (29, 35, True),
        (44, 40, True),
        (0, 5, True),
        (1, 59, True),
        (15, 10, True),
        (16, 45, True),
        (30, 0, True),
        (31, 30, True),
        (45, 12, True),
        (46, 59, True),
        # Non-surge times
        (2, 0, False),
        (10, 15, False),
        (17, 30, False),
        (28, 45, False),
        (32, 10, False),
        (47, 5, False),
        (58, 59, False),
    ]

    for minute, second, expected in test_cases:
        mock_dt = datetime(2026, 9, 3, 12, minute, second)
        with patch("src.worker.datetime") as mock_datetime:
            mock_datetime.now.return_value = mock_dt
            assert is_surge_window() is expected, f"Failed at {minute}:{second}"
