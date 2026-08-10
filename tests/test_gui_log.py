from types import SimpleNamespace
from unittest.mock import Mock, patch

from minios_kernel_manager import KernelPackWindow


def test_log_message_delegates_timestamped_record_to_shared_log_view():
    log_view = Mock()
    window = SimpleNamespace(log_view=log_view)

    with patch("minios_kernel_manager.time.strftime", return_value="12:34:56"):
        KernelPackWindow._log_message(window, "Packaging kernel")

    log_view.append_line.assert_called_once_with(
        "Packaging kernel", timestamp="12:34:56")
