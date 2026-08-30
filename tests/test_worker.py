from datetime import datetime
from src.worker import is_surge_window


def test_is_surge_window_logic():
    # Calling is_surge_window returns a valid boolean
    result = is_surge_window()
    assert isinstance(result, bool)
