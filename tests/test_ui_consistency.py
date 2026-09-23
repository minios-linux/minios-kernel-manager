import ast
import configparser
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "lib/minios_kernel_manager.py").read_text(encoding="utf-8")
CSS = (ROOT / "share/styles/style.css").read_text(encoding="utf-8")


def test_release_metadata_is_synchronized():
    changelog = (ROOT / "debian/changelog").read_text(encoding="utf-8")
    version = re.search(
        r'^minios-kernel-manager \(([^)]+)\)', changelog).group(1)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.startswith("# MiniOS Kernel Manager {}\n".format(version))
    for name in ("minios-kernel.1", "minios-kernel-manager.1"):
        first_line = (ROOT / "manpages" / "en" / name).read_text(
            encoding="utf-8").splitlines()[0]
        assert '"MiniOS Kernel Manager"' in first_line


def test_split_packages_have_disjoint_payloads_and_exact_backend_dependency():
    backend = set((ROOT / "debian/minios-kernel.install").read_text(
        encoding="utf-8").splitlines())
    frontend = set((ROOT / "debian/minios-kernel-manager.install").read_text(
        encoding="utf-8").splitlines())
    control = (ROOT / "debian/control").read_text(encoding="utf-8")

    assert backend.isdisjoint(frontend)
    assert "usr/bin/minios-kernel" in backend
    assert "usr/bin/minios-kernel-manager" in frontend
    assert "minios-kernel (= ${binary:Version})" in control
    assert "Breaks: minios-kernel-manager (<< 1.4.0)" in control
    assert "Replaces: minios-kernel-manager (<< 1.4.0)" in control
    assert "         util-linux\n" in control
    assert "         build-essential" not in control


def kernel_status_branch():
    start = SOURCE.index("# Add CSS classes based on kernel status")
    end = SOURCE.index("main_box = Gtk.Box", start)
    return SOURCE[start:end]


def test_kernel_rows_use_shared_content_and_status_classes():
    assert SOURCE.count("add_class('manager-state-row-content')") == 2
    assert SOURCE.count("add_class('kernel-row-content')") == 2
    assert SOURCE.count("add_class('kernel-row')") == 2
    for status in ("active", "running", "available"):
        assert f"add_class('row-status-{status}')" in kernel_status_branch()

    assert "kernel-item" not in SOURCE
    assert "kernel-status-" not in SOURCE
    assert "min-height: 80px" not in CSS
    assert "padding: 12px 16px" not in CSS
    assert "border-left" not in CSS
    assert '.minios-list row.kernel-row' in CSS
    assert 'min-height: 0' in CSS


def test_available_status_is_plain_text_not_a_button_like_badge():
    repository_status = SOURCE.index("# Repository kernels are always available for download")
    repository_end = SOURCE.index("main_box.pack_start(status_box", repository_status)
    repository_branch = SOURCE[repository_status:repository_end]
    assert "add_class('row-meta')" in repository_branch
    assert "add_class('badge')" not in repository_branch

    packaged_available = SOURCE.index("status_text = _('AVAILABLE')", SOURCE.index("# Primary status badge"))
    packaged_end = SOURCE.index('status_label.set_markup', packaged_available)
    packaged_branch = SOURCE[packaged_available:packaged_end]
    assert "add_class('badge')" not in packaged_branch
    assert "add_class('row-meta')" in packaged_branch


def test_lists_use_shared_semantic_classes_without_button_backdrop():
    assert SOURCE.count("add_class('minios-list')") == 2
    assert 'add_class("minios-footer")' not in SOURCE
    assert 'listbox {' not in CSS
    assert 'listbox row' not in CSS
    assert '.content-card' not in CSS


def test_repository_kernel_search_filters_names_and_clears_hidden_selection():
    assert 'self.kernel_search_entry = Gtk.SearchEntry()' in SOURCE
    assert 'self.kernel_list.set_filter_func(' in SOURCE
    assert 'def _filter_repository_kernel_row(self, row):' in SOURCE
    assert 'query in searchable_name' in SOURCE
    assert 'row.kernel_search_name = "{} {}".format(' in SOURCE
    assert 'self.kernel_list.unselect_all()' in SOURCE


def test_repository_kernel_filter_is_case_insensitive():
    from minios_kernel_manager import KernelPackWindow

    window = SimpleNamespace(
        kernel_search_entry=SimpleNamespace(get_text=lambda: 'CLOUD-AMD64'))
    matching = SimpleNamespace(
        kernel_search_name='linux-image-6.12-cloud-amd64 6.12-cloud-amd64')
    other = SimpleNamespace(
        kernel_search_name='linux-image-6.12-rt-amd64 6.12-rt-amd64')
    placeholder = SimpleNamespace()

    assert KernelPackWindow._filter_repository_kernel_row(window, matching)
    assert not KernelPackWindow._filter_repository_kernel_row(window, other)
    assert KernelPackWindow._filter_repository_kernel_row(window, placeholder)


def test_repository_search_clears_filtered_selection():
    from minios_kernel_manager import KernelPackWindow

    selected = SimpleNamespace(kernel_search_name='linux-image-rt')
    kernel_list = Mock()
    kernel_list.get_selected_row.return_value = selected
    window = SimpleNamespace(
        kernel_search_entry=SimpleNamespace(get_text=lambda: 'cloud'),
        kernel_list=kernel_list,
        selected_kernel='linux-image-rt',
        _update_buttons_state=Mock(),
    )
    window._filter_repository_kernel_row = lambda row: (
        KernelPackWindow._filter_repository_kernel_row(window, row))

    KernelPackWindow._on_kernel_search_changed(window, None)

    kernel_list.invalidate_filter.assert_called_once_with()
    kernel_list.unselect_all.assert_called_once_with()
    assert window.selected_kernel is None
    window._update_buttons_state.assert_called_once_with()


def test_standard_presentation_helpers_are_shared():
    assert 'format_bytes(total_size)' in SOURCE
    assert 'def _format_file_size' not in SOURCE
    assert 'Gtk.MessageDialog' not in SOURCE


def test_privileged_commands_skip_pkexec_for_root():
    from minios_kernel_manager import _privileged_command

    with patch('minios_kernel_manager.os.geteuid', return_value=0):
        assert _privileged_command(['minios-kernel', 'list']) == [
            'minios-kernel', 'list']
    with patch('minios_kernel_manager.os.geteuid', return_value=1000):
        assert _privileged_command(['minios-kernel', 'list']) == [
            'pkexec', 'minios-kernel', 'list']


def test_packaging_uses_shared_command_lifecycle_and_choosers():
    assert 'CommandRunner(' in SOURCE
    assert 'stderr_callback=self._on_package_stderr' in SOURCE
    assert 'choose_open_files(' in SOURCE
    assert 'Gtk.FileChooserDialog' not in SOURCE
    assert 'Gtk.main_iteration()' not in SOURCE
    assert 'subprocess.Popen(' not in SOURCE
    assert 'GLib.timeout_add(' not in SOURCE


def test_each_curated_driver_has_an_independent_gui_checkbox():
    assert 'self.driver_checks = {}' in SOURCE
    assert "check = Gtk.CheckButton(label=driver['label'])" in SOURCE
    assert "cmd_args.extend(['--dkms-driver', driver_id])" in SOURCE
    assert 'self.driver_frame.set_sensitive(False)' in SOURCE
    assert 'get_compatible_driver_ids(' in SOURCE


def test_packaging_log_matches_installer_details_layout():
    start = SOURCE.index('    def _build_progress_ui(self):')
    end = SOURCE.index('    def _save_ui_state(self):', start)
    progress_ui = SOURCE[start:end]

    assert 'Gtk.Expander(label=_("Show Details"))' in progress_ui
    assert 'details_frame = Gtk.Frame()' in progress_ui
    assert 'self.log_view.set_size_request(-1, 220)' in progress_ui
    assert 'log_frame.set_label(_("Packaging Log"))' not in progress_ui


def test_kernel_list_states_use_shared_placeholder():
    assert 'StatePlaceholder(' in SOURCE


def test_kernel_list_loading_uses_shared_operation_overlay():
    assert 'self.kernel_loading_box = OperationView(' in SOURCE
    assert "self.kernel_loading_box.set_state('running')" in SOURCE
    assert "self.kernel_loading_box.set_state('idle')" in SOURCE
    assert "icon_name='view-refresh-symbolic'" not in SOURCE


def test_healthy_status_banner_is_hidden_but_errors_remain_visible():
    from minios_kernel_manager import KernelPackWindow

    for writable, expected_visible in ((True, False), (False, True)):
        window = SimpleNamespace(
            minios_path='/minios', minios_writable=writable,
            main_vbox=Mock())
        banner = Mock()
        banner.label = Mock()
        with patch('minios_kernel_manager.StatusBanner', return_value=banner):
            KernelPackWindow._build_system_status_info(window)

        banner.set_no_show_all.assert_called_once_with(True)
        banner.set_visible.assert_called_once_with(expected_visible)


def test_loading_overlays_disable_bottom_actions():
    assert "is_loading = getattr(self, '_kernel_loading_visible', False)" in SOURCE
    assert "not bool(is_loading)" in SOURCE
    assert "self.activate_kernel_button.set_sensitive(False)" in SOURCE
    assert "self.delete_kernel_button.set_sensitive(False)" in SOURCE
    assert "'_activation_loading_visible', False" in SOURCE

    build_window = SimpleNamespace(
        selected_kernel='linux-image-test', selected_deb_files=[],
        minios_path='/minios', minios_writable=True, is_building=False,
        _kernel_loading_visible=True, build_button=Mock(),
        repo_radio=SimpleNamespace(get_active=lambda: True),
    )
    from minios_kernel_manager import KernelPackWindow
    KernelPackWindow._update_buttons_state(build_window)
    build_window.build_button.set_sensitive.assert_called_once_with(False)

    activation_window = SimpleNamespace(
        selected_packaged_kernel=None, minios_writable=True,
        _activation_loading_visible=True,
        activate_kernel_button=Mock(), delete_kernel_button=Mock(),
    )
    row = SimpleNamespace(kernel_version='test', kernel_info={
        'is_active': False, 'is_running': False,
    })
    KernelPackWindow._on_packaged_kernel_selected(
        activation_window, None, row)
    activation_window.activate_kernel_button.set_sensitive.assert_called_once_with(
        False)
    activation_window.delete_kernel_button.set_sensitive.assert_called_once_with(
        False)


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
    row = SimpleNamespace(kernel_version='test', kernel_info={
        'status': 'Active & Running',
        'is_active': True,
        'is_running': True,
    })
    KernelPackWindow._on_packaged_kernel_selected(window, None, row)

    window.activate_kernel_button.set_sensitive.assert_called_once_with(False)
    window.delete_kernel_button.set_sensitive.assert_called_once_with(False)


def test_packaged_actions_reuse_privileged_list_status():
    assert 'row.kernel_info = kernel_info' in SOURCE
    assert 'kernel_info = row.kernel_info' in SOURCE
    assert "kernel_info = getattr(row, 'kernel_info', None)" in SOURCE
    assert 'get_kernel_info(' not in SOURCE


def test_running_only_kernel_can_activate_but_cannot_delete():
    from minios_kernel_manager import KernelPackWindow

    window = SimpleNamespace(
        selected_packaged_kernel=None,
        minios_path='/minios',
        minios_writable=True,
        activate_kernel_button=Mock(),
        delete_kernel_button=Mock(),
    )
    row = SimpleNamespace(kernel_version='test', kernel_info={
        'status': 'Running Available',
        'is_active': False,
        'is_running': True,
    })
    KernelPackWindow._on_packaged_kernel_selected(window, None, row)

    window.activate_kernel_button.set_sensitive.assert_called_once_with(True)
    window.delete_kernel_button.set_sensitive.assert_called_once_with(False)


def test_window_icon_matches_the_desktop_launcher():
    desktop = configparser.ConfigParser(interpolation=None)
    desktop.read(str(ROOT / "share/applications/minios-kernel-manager.desktop"))
    for node in ast.parse(SOURCE).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'ICON_WINDOW'
                for target in node.targets):
            assert ast.literal_eval(node.value) == desktop['Desktop Entry']['Icon']
            break
    else:
        raise AssertionError('Window icon is not defined')
    assert 'self.set_icon_name(ICON_WINDOW)' in SOURCE
