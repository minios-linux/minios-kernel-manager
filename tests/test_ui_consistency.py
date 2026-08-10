from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "lib/minios_kernel_manager.py").read_text(encoding="utf-8")
CSS = (ROOT / "share/styles/style.css").read_text(encoding="utf-8")


def kernel_status_branch():
    start = SOURCE.index("# Add CSS classes based on kernel status")
    end = SOURCE.index("main_box = Gtk.Box", start)
    return SOURCE[start:end]


def test_kernel_rows_use_shared_content_and_status_classes():
    assert SOURCE.count("add_class('manager-state-row-content')") == 2
    for status in ("active", "running", "available"):
        assert f"add_class('row-status-{status}')" in kernel_status_branch()

    assert "kernel-item" not in SOURCE
    assert "kernel-status-" not in SOURCE
    assert "min-height: 80px" not in CSS
    assert "padding: 12px 16px" not in CSS
    assert "border-left" not in CSS


def test_active_kernel_status_has_precedence_over_running():
    branch = kernel_status_branch()

    assert branch.index("if kernel_info.get('is_active'):") < branch.index(
        "elif kernel_info.get('is_running'):")
    assert branch.index("add_class('row-status-active')") < branch.index(
        "add_class('row-status-running')") < branch.index(
        "add_class('row-status-available')")


def test_running_badge_remains_independently_warning_colored():
    assert "running_label.get_style_context().add_class('badge-warning')" in SOURCE


def test_action_sensitivity_uses_backend_booleans_not_status_text():
    from minios_kernel_manager import KernelPackWindow

    window = SimpleNamespace(
        selected_packaged_kernel=None,
        minios_path='/minios',
        minios_writable=True,
        activate_kernel_button=Mock(),
        delete_kernel_button=Mock(),
    )
    row = SimpleNamespace(kernel_version='test')
    kernel_info = {
        'status': 'Active & Running',
        'is_active': True,
        'is_running': True,
    }
    with patch('minios_kernel_manager.get_kernel_info',
               return_value=kernel_info):
        KernelPackWindow._on_packaged_kernel_selected(window, None, row)

    window.activate_kernel_button.set_sensitive.assert_called_once_with(False)
    window.delete_kernel_button.set_sensitive.assert_called_once_with(False)


def test_running_only_kernel_can_activate_but_cannot_delete():
    from minios_kernel_manager import KernelPackWindow

    window = SimpleNamespace(
        selected_packaged_kernel=None,
        minios_path='/minios',
        minios_writable=True,
        activate_kernel_button=Mock(),
        delete_kernel_button=Mock(),
    )
    row = SimpleNamespace(kernel_version='test')
    with patch('minios_kernel_manager.get_kernel_info', return_value={
            'status': 'Running Available',
            'is_active': False,
            'is_running': True}):
        KernelPackWindow._on_packaged_kernel_selected(window, None, row)

    window.activate_kernel_button.set_sensitive.assert_called_once_with(True)
    window.delete_kernel_button.set_sensitive.assert_called_once_with(False)
