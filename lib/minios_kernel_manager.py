#!/usr/bin/env python3
# -*- coding: utf-8 -*- 
"""
MiniOS Kernel Manager
A graphical tool for managing kernel modules in MiniOS Linux distribution.
It packages kernels into SquashFS images, generates initramfs, and automatically
installs them to MiniOS directories.

Usage:
    main_kernel_manager.py

Copyright (C) 2025 MiniOS Linux

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.


You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
import gi
import os
import sys
import gettext
import locale
import threading
import tempfile
import shutil
import time
import subprocess
import fcntl
import json
import re

# Use only system installed modules
from minios_utils import (
    find_minios_directory, get_kernel_info,
    get_currently_running_kernel, is_kernel_currently_running, get_system_type
)
from kernel_utils import get_repository_kernels, get_manual_packages, _format_size
from compression_utils import get_available_compressions

gi.require_version('Gtk', '3.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, GLib, Gio, Pango

# ──────────────────────────────────────────────────────────────────────────────
# CLI Interface Functions
# ──────────────────────────────────────────────────────────────────────────────
def run_minios_kernel(args):
    """Execute minios-kernel command with pkexec for administrative privileges"""
    # Always use pkexec for kernel operations to ensure proper privileges
    cmd = ['pkexec', 'minios-kernel'] + args
    
    return subprocess.run(cmd, capture_output=True, text=True)

def activate_kernel_cli(kernel_version):
    """Activate kernel using minios-kernel CLI with JSON output"""
    try:
        # Use pkexec to execute the script with JSON output
        result = run_minios_kernel(['--json', 'activate', kernel_version])
        
        if result.returncode == 0:
            # Parse JSON response
            data = json.loads(result.stdout)
            
            if data.get('success'):
                return True, data.get('message', 'Kernel activated successfully')
            else:
                return False, data.get('error', 'Unknown error')
        else:
            # Try to parse JSON error response
            try:
                error_data = json.loads(result.stderr)
                return False, error_data.get('error', result.stderr)
            except json.JSONDecodeError:
                return False, result.stderr or f"Command failed with return code {result.returncode}"
            
    except json.JSONDecodeError as e:
        # Fallback to text parsing if JSON fails
        try:
            result = run_minios_kernel(['activate', kernel_version])
            if result.returncode == 0:
                return True, result.stdout
            else:
                return False, result.stderr or f"Command failed with return code {result.returncode}"
        except Exception as fallback_e:
            return False, str(fallback_e)
    except Exception as e:
        return False, str(e)

def list_kernels_cli():
    """List available kernels using minios-kernel CLI with JSON output"""
    try:
        # Use pkexec to execute the script with JSON output
        result = run_minios_kernel(['--json', 'list'])
        
        if result.returncode == 0:
            # Parse JSON response
            data = json.loads(result.stdout)
            
            kernels = []
            active_kernel = data.get('active_kernel')
            
            for kernel_info in data.get('kernels', []):
                kernels.append(kernel_info['version'])
            
            return kernels, active_kernel
        else:
            return [], None
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as e:
        # Fallback to text parsing if JSON fails
        try:
            cmd = ['minios-kernel', 'list']
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            # Parse the output to extract kernel list
            kernels = []
            active_kernel = None
            
            lines = result.stdout.strip().split('\n')
            in_kernel_list = False
            
            for line in lines:
                line = line.strip()
                if line.startswith('Available kernels:'):
                    in_kernel_list = True
                    continue
                elif line.startswith('Currently active kernel:'):
                    if ':' in line:
                        active_kernel = line.split(':', 1)[1].strip()
                    in_kernel_list = False
                    continue
                elif in_kernel_list and line.startswith('- '):
                    kernel_info = line[2:].strip()  # Remove '- ' prefix
                    if ' (active)' in kernel_info:
                        kernel = kernel_info.replace(' (active)', '')
                        active_kernel = kernel
                    else:
                        kernel = kernel_info
                    kernels.append(kernel)
            
            return kernels, active_kernel
        except Exception:
            print(f"Error listing kernels: {str(e)}")
            return [], None
    except Exception as e:
        print(f"Error listing kernels: {str(e)}")
        return [], None

def package_kernel_cli(source_type, source_path, output_dir, squashfs_comp="zstd", initrd_comp="zstd"):
    """Package kernel using minios-kernel CLI with JSON output"""
    try:
        # Build command arguments
        cmd_args = ['--json', 'package', '-o', output_dir, '--sqfs-comp', squashfs_comp]
        
        if source_type == 'repo':
            cmd_args.extend(['--repo', source_path])
        else:  # deb
            cmd_args.extend(['--deb', source_path])
        
        # Use pkexec to execute the script
        result = run_minios_kernel(cmd_args)
        
        if result.returncode == 0:
            return True, result.stdout
        else:
            # Try to parse JSON error response
            try:
                error_data = json.loads(result.stderr)
                return False, error_data.get('error', result.stderr)
            except json.JSONDecodeError:
                return False, result.stderr or f"Command failed with return code {result.returncode}"
    except Exception as e:
        return False, str(e)

def update_package_lists_gui():
    """Update package lists directly via pkexec apt update"""
    try:
        result = subprocess.run([
            'pkexec', 'apt', 'update'
        ], capture_output=True, text=True)
        
        return result.returncode == 0, result.stderr if result.returncode != 0 else "Package lists updated"
    except Exception as e:
        return False, str(e)

def delete_kernel_cli(kernel_version):
    """Delete kernel using minios-kernel CLI with administrative privileges"""
    try:
        # Use pkexec to execute the script with JSON output
        result = run_minios_kernel(['--json', 'delete', kernel_version])
        
        if result.returncode == 0:
            response_data = json.loads(result.stdout)
            success = response_data.get('success', False)
            message = response_data.get('message', '')
            error = response_data.get('error', '')
            
            if success:
                return True, message
            else:
                return False, error
        else:
            # Try to parse error from stderr or stdout
            error_msg = result.stderr.strip() or result.stdout.strip()
            return False, f"Command failed with exit code {result.returncode}: {error_msg}"
            
    except json.JSONDecodeError as e:
        return False, f"Failed to parse command output: {e}"
    except Exception as e:
        return False, str(e)

def check_minios_status_cli():
    """Check MiniOS directory status using minios-kernel CLI with JSON output"""
    try:
        result = run_minios_kernel(['status', '--json'])
        if result.returncode == 0:
            status_data = json.loads(result.stdout)
            return status_data
        else:
            return {
                'success': False,
                'found': False,
                'writable': False,
                'error': f'CLI command failed: {result.stderr}'
            }
    except json.JSONDecodeError as e:
        return {
            'success': False,
            'found': False, 
            'writable': False,
            'error': f'Failed to parse JSON response: {e}'
        }
    except Exception as e:
        return {
            'success': False,
            'found': False,
            'writable': False,
            'error': str(e)
        }

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
APPLICATION_ID   = 'dev.minios.kernel-manager'
APP_NAME         = 'minios-kernel-manager'
APP_TITLE        = 'MiniOS Kernel Manager'
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_FILE_PATH = '/usr/share/minios-kernel-manager/style.css'

# Icons
ICON_WINDOW = 'system-software-install'
ICON_BUILD = 'document-send'
ICON_CANCEL = 'process-stop'

# Set up translations
try:
    locale.setlocale(locale.LC_ALL, '')
    # Use system locale directory for installed version, local for source tree
    if os.path.basename(script_dir) == 'lib':
        # Running from source tree
        locale_dir = os.path.join(os.path.dirname(script_dir), 'po')
    else:
        # Running from installed location
        locale_dir = '/usr/share/locale'
    
    gettext.bindtextdomain(APP_NAME, locale_dir)
    gettext.textdomain(APP_NAME)
    _ = gettext.gettext
except Exception as e:
    print(f"Could not set up translation: {e}")
    _ = lambda s: s

# ──────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ──────────────────────────────────────────────────────────────────────────────
def apply_css_if_exists():
    """Apply CSS styling to the application if the file exists."""
    if os.path.exists(CSS_FILE_PATH):
        provider = Gtk.CssProvider()
        provider.load_from_path(CSS_FILE_PATH)
        Gtk.StyleContext.add_provider_for_screen(
            Gtk.Widget.get_screen(Gtk.Window()),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

# ──────────────────────────────────────────────────────────────────────────────
# Main Application Window
# ──────────────────────────────────────────────────────────────────────────────
class KernelPackWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application):
        super().__init__(application=application)
        self.set_default_size(750, 550)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_icon_name(ICON_WINDOW)

        self.selected_kernel = None
        self.kernel_source = "manual"
        self.sqfs_compression = "zstd"
        self.is_building = False
        self.cancel_requested = False
        self.minios_path = None
        self.minios_writable = False
        self.system_type = get_system_type()
        self.active_pid = None

        # UI components
        self.main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for m in ("set_margin_top", "set_margin_bottom", "set_margin_start", "set_margin_end"):
            getattr(self.main_vbox, m)(12)
        self.add(self.main_vbox)

        # Apply CSS if available
        apply_css_if_exists()

        # Build the user interface
        self._build_header_bar()
        self._detect_minios_directory()
        self._build_main_ui()
        self._update_buttons_state()  # Update button states after UI is built

        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, widget):
        # widget parameter is not used
        self.get_application().quit()

    def _detect_minios_directory(self):
        """Detect MiniOS directory and check write permissions"""
        # Use CLI command to check status
        status_data = check_minios_status_cli()
        
        if status_data.get('success', False) and status_data.get('found', False):
            self.minios_path = status_data.get('minios_path')
            self.minios_writable = status_data.get('writable', False)
        else:
            # Fallback to direct detection if CLI fails
            self.minios_path = find_minios_directory()
            self.minios_writable = False  # Assume not writable if CLI check fails

    def _build_header_bar(self):
        """Build the header bar"""
        header = Gtk.HeaderBar(show_close_button=True)
        header.props.title = _(APP_TITLE)
        self.set_titlebar(header)

    def _build_system_status_info(self):
        """Build system status information panel"""
        # MiniOS directory status - simplified
        minios_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        minios_hbox.set_margin_bottom(12)
        
        if self.minios_path and self.minios_writable:
            minios_icon_name = "emblem-default"  # Green checkmark
            minios_color = "#2E7D32"  # Green
            status_text = _("MiniOS directory is writable")
        else:
            minios_icon_name = "dialog-error"  # Error icon
            minios_color = "#D32F2F"  # Red
            if self.minios_path:
                status_text = _("MiniOS directory is read-only")
            else:
                status_text = _("MiniOS directory not found")
        
        # Status icon
        status_icon = Gtk.Image.new_from_icon_name(minios_icon_name, Gtk.IconSize.MENU)
        minios_hbox.pack_start(status_icon, False, False, 0)
        
        # Status text - clean and simple
        minios_status_label = Gtk.Label()
        minios_status_label.set_markup(f'<span color="{minios_color}"><b>{status_text}</b></span>')
        minios_status_label.set_halign(Gtk.Align.START)
        minios_hbox.pack_start(minios_status_label, False, False, 0)
        
        self.main_vbox.pack_start(minios_hbox, False, False, 0)

    def _build_main_ui(self):
        """Build main interface with tabs"""
        # Clear existing UI
        for child in self.main_vbox.get_children():
            self.main_vbox.remove(child)
            
        # Clear references to progress UI overlays
        if hasattr(self, 'cancel_loading_box'):
            delattr(self, 'cancel_loading_box')
        if hasattr(self, 'cancel_loading_spinner'):
            delattr(self, 'cancel_loading_spinner')
        if hasattr(self, 'cancel_loading_label'):
            delattr(self, 'cancel_loading_label')

        # System status info
        self._build_system_status_info()
        
        # Create notebook (tabs)
        self.notebook = Gtk.Notebook()
        self.notebook.set_tab_pos(Gtk.PositionType.TOP)
        self.main_vbox.pack_start(self.notebook, True, True, 0)
        
        # Activate tab (first)
        activate_tab = self._build_activate_tab()
        activate_label = Gtk.Label(label=_("Manage Kernels"))
        self.notebook.append_page(activate_tab, activate_label)
        
        # Install tab (second)
        install_tab = self._build_install_tab()
        install_label = Gtk.Label(label=_("Package Kernel"))
        self.notebook.append_page(install_tab, install_label)
        
        # Show everything first, then apply initial visibility logic
        self.show_all()
        
        # Apply initial UI state (this must be after show_all)
        self._initialize_loading_overlays()
        self._update_kernel_selection_ui()

    def _build_selection_ui(self):
        """Return to main selection interface"""
        self._build_main_ui()
        # Switch to Package Kernel tab (index 1, since Manage Kernels is now index 0)
        self.notebook.set_current_page(1)
        # Restore previous state
        self._restore_ui_state()

    def _build_install_tab(self):
        """Build kernel installation tab"""
        tab_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        tab_box.set_margin_top(12)
        tab_box.set_margin_bottom(12)
        tab_box.set_margin_start(12)
        tab_box.set_margin_end(12)
        
        return self._build_selection_ui_content(tab_box)

    def _build_activate_tab(self):
        """Build kernel activation tab"""
        tab_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        tab_box.set_margin_top(12)
        tab_box.set_margin_bottom(12)
        tab_box.set_margin_start(12)
        tab_box.set_margin_end(12)
        
        # Instruction label
        lbl = Gtk.Label(label=_("Select a packaged kernel to activate or delete:"), xalign=0)
        lbl.set_margin_bottom(8)
        tab_box.pack_start(lbl, False, False, 0)
        
        # Packaged kernels list
        self.packaged_kernel_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.packaged_kernel_list.connect("row-selected", self._on_packaged_kernel_selected)
        self.packaged_kernel_list.connect("button-press-event", self._on_list_button_press)
        
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_width(400)
        sw.set_min_content_height(200)
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.packaged_kernel_list)
        
        # Loading overlay components
        self.activate_loading_spinner = Gtk.Spinner()
        self.activate_loading_label = Gtk.Label(label=_("Activating kernel..."))
        self.activate_loading_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.activate_loading_box.pack_start(self.activate_loading_spinner, False, False, 0)
        self.activate_loading_box.pack_start(self.activate_loading_label, False, False, 0)
        self.activate_loading_box.set_halign(Gtk.Align.CENTER)
        self.activate_loading_box.set_valign(Gtk.Align.CENTER)
        self.activate_loading_box.get_style_context().add_class('loading-overlay')
        
        # Create overlay
        overlay = Gtk.Overlay()
        overlay.add(sw)
        overlay.add_overlay(self.activate_loading_box)
        self.activate_loading_box.set_visible(False)
        
        tab_box.pack_start(overlay, True, True, 0)
        
        # Populate packaged kernels
        self._populate_packaged_kernels()
        
        # Create context menu
        self._create_context_menu()
        
        return tab_box

    def _build_selection_ui_content(self, container):
        """Build kernel selection interface content"""
        
        # Kernel source selection
        source_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        source_box.set_margin_top(6)
        self.local_radio = Gtk.RadioButton.new_with_label_from_widget(None, _("Manual Package"))
        self.local_radio.set_active(True)
        self.local_radio.connect("toggled", self._on_kernel_source_changed)
        source_box.pack_start(self.local_radio, False, False, 0)
        
        self.repo_radio = Gtk.RadioButton.new_with_label_from_widget(self.local_radio, _("Repository"))
        self.repo_radio.connect("toggled", self._on_kernel_source_changed)
        source_box.pack_start(self.repo_radio, False, False, 0)
        
        # Add compression selection aligned to the right (reverse order for pack_end)
        self.sqfs_combo = Gtk.ComboBoxText()
        self.sqfs_combo.set_size_request(120, -1)  # Set minimum width to 120 pixels
        compressions = get_available_compressions()
        for comp in compressions:
            self.sqfs_combo.append_text(comp)
        # Set default to zstd
        try:
            default_index = compressions.index("zstd")
        except ValueError:
            default_index = 0
        self.sqfs_combo.set_active(default_index)
        self.sqfs_combo.connect("changed", self._on_sqfs_compression_changed)
        source_box.pack_end(self.sqfs_combo, False, False, 0)
        
        compression_label = Gtk.Label(label=_("SquashFS Compression:"))
        compression_label.set_margin_end(6)
        source_box.pack_end(compression_label, False, False, 0)
        
        container.pack_start(source_box, False, False, 0)
        
        # Main content area
        vb_kernel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vb_kernel.set_margin_top(12)
        container.pack_start(vb_kernel, True, True, 0)
        
        # Kernel selection area
        kernel_selection_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        
        # Manual package selection (only shown when Manual Package is selected)
        self.manual_selection_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        
        file_selection_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        self.selected_file_label = Gtk.Label(label=_("No file selected"), xalign=0)
        self.selected_file_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.selected_file_label.get_style_context().add_class('dim-label')
        file_selection_box.pack_start(self.selected_file_label, True, True, 0)
        
        self.browse_button = Gtk.Button.new_with_label(_("Browse..."))
        self.browse_button.set_image(Gtk.Image.new_from_icon_name("document-open", Gtk.IconSize.BUTTON))
        self.browse_button.set_always_show_image(True)
        self.browse_button.get_style_context().add_class('suggested-action')
        self.browse_button.connect("clicked", self._on_browse_clicked)
        file_selection_box.pack_start(self.browse_button, False, False, 0)
        
        self.manual_selection_box.pack_start(file_selection_box, False, False, 0)
        
        # Package info area (hidden by default)
        self.package_info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.package_info_box.set_margin_top(10)
        
        info_label = Gtk.Label(label=_("Package Information:"), xalign=0)
        info_label.set_markup(f'<b>{_("Package Information:")}</b>')
        self.package_info_box.pack_start(info_label, False, False, 0)
        
        # Create info display frame
        info_frame = Gtk.Frame()
        info_frame.set_shadow_type(Gtk.ShadowType.IN)
        
        self.package_info_label = Gtk.Label()
        self.package_info_label.set_selectable(True)
        self.package_info_label.set_halign(Gtk.Align.START)
        self.package_info_label.set_valign(Gtk.Align.START)
        self.package_info_label.set_margin_start(10)
        self.package_info_label.set_margin_end(10)
        self.package_info_label.set_margin_top(8)
        self.package_info_label.set_margin_bottom(8)
        self.package_info_label.set_line_wrap(True)
        
        info_frame.add(self.package_info_label)
        self.package_info_box.pack_start(info_frame, False, False, 0)
        
        self.manual_selection_box.pack_start(self.package_info_box, False, False, 0)
        self.package_info_box.hide()  # Hidden by default
        
        kernel_selection_box.pack_start(self.manual_selection_box, False, False, 0)
        self.manual_selection_box.show_all()  # Show by default (Manual Package is selected)
        
        # Repository kernel list (shown when Repository is selected)
        self.repo_selection_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        
        kernel_list_label = Gtk.Label(label=_("Available Kernels:"), xalign=0)
        self.repo_selection_box.pack_start(kernel_list_label, False, False, 0)

        self.kernel_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.kernel_list.connect("row-selected", self._on_kernel_selected)

        sw = Gtk.ScrolledWindow()
        sw.set_min_content_width(650)
        sw.set_min_content_height(200)
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.kernel_list)
        self.repo_selection_box.pack_start(sw, True, True, 0)
        
        kernel_selection_box.pack_start(self.repo_selection_box, True, True, 0)
        self.repo_selection_box.hide()  # Hidden by default (Manual Package is selected)
        
        vb_kernel.pack_start(kernel_selection_box, True, True, 0)
        


        # Bottom buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)
        button_box.set_margin_top(12)
        container.pack_start(button_box, False, False, 0)

        button_text = _("Package Kernel")
        button_tooltip = _("Package kernel and add to repository")
        
        self.build_button = Gtk.Button.new_with_label(button_text)
        self.build_button.get_style_context().add_class('suggested-action')
        self.build_button.connect("clicked", self._on_build_clicked)
        self.build_button.set_sensitive(False)  # Disabled until kernel selected
        self.build_button.set_tooltip_text(button_tooltip)
        button_box.pack_start(self.build_button, False, False, 0)

        return container

    def _update_buttons_state(self):
        """Update buttons state based on MiniOS directory writeability and selection"""
        # Check if build button should be enabled
        if hasattr(self, 'build_button'):
            can_build = (self.selected_kernel and 
                        self.minios_path and 
                        self.minios_writable and 
                        not self.is_building)
            self.build_button.set_sensitive(can_build)
            
            # Update tooltip
            if not self.minios_path:
                tooltip = _("MiniOS directory not found")
            elif not self.minios_writable:
                tooltip = _("MiniOS directory is read-only")
            elif not self.selected_kernel:
                tooltip = _("Please select a kernel first")
            elif self.is_building:
                tooltip = _("Build in progress...")
            else:
                tooltip = _("Package kernel and add to repository")
            self.build_button.set_tooltip_text(tooltip)

    def _populate_packaged_kernels(self):
        """Populate list of packaged kernels"""
        # Clear existing kernels
        for child in self.packaged_kernel_list.get_children():
            self.packaged_kernel_list.remove(child)
        
        if not self.minios_path:
            return
        
        all_kernels, active_kernel = list_kernels_cli()
        
        if not all_kernels:
            # Show no kernels message
            no_kernels_row = Gtk.ListBoxRow()
            no_kernels_row.set_sensitive(False)
            
            main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            main_box.set_halign(Gtk.Align.CENTER)
            
            icon = Gtk.Image.new_from_icon_name("dialog-information", Gtk.IconSize.DND)
            main_box.pack_start(icon, False, False, 0)
            
            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            
            title_label = Gtk.Label()
            title_label.set_markup(f'<b>{_("No kernels packaged")}</b>')
            title_label.set_halign(Gtk.Align.START)
            info_box.pack_start(title_label, False, False, 0)
            
            detail_label = Gtk.Label()
            detail_label.set_markup(f'<span size="small" color="#666666">{_("Package a kernel first using the Package Kernel tab")}</span>')
            detail_label.set_halign(Gtk.Align.START)
            info_box.pack_start(detail_label, False, False, 0)
            
            main_box.pack_start(info_box, False, False, 0)
            no_kernels_row.add(main_box)
            self.packaged_kernel_list.add(no_kernels_row)
            
        else:
            for kernel in all_kernels:
                # For CLI compatibility, create simplified kernel_info
                is_active = kernel == active_kernel
                is_running = is_kernel_currently_running(kernel)
                
                kernel_info = {
                    'display_name': kernel,
                    'description': f"Kernel version {kernel}",
                    'is_active': is_active,
                    'is_running': is_running,
                    'icon_name': 'package-x-generic',
                    'status': 'active' if is_active else 'available'
                }

                row = Gtk.ListBoxRow()
                
                # Add CSS classes based on kernel status
                if kernel_info.get('is_running'):
                    row.get_style_context().add_class('kernel-status-running')
                elif kernel_info.get('is_active'):
                    row.get_style_context().add_class('kernel-status-active')
                else:
                    row.get_style_context().add_class('kernel-status-available')
                
                main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
                main_box.get_style_context().add_class('kernel-item')
                
                # Use the new icon from kernel_info
                img = Gtk.Image.new_from_icon_name(kernel_info.get('icon_name', 'package-x-generic'), Gtk.IconSize.DND)
                main_box.pack_start(img, False, False, 0)
                
                info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                info_box.set_hexpand(True)
                
                # Main kernel name with better formatting
                kernel_label = Gtk.Label()
                kernel_label.set_markup(f'<b><span size="large">{GLib.markup_escape_text(kernel_info["display_name"])}</span></b>')
                kernel_label.set_halign(Gtk.Align.START)
                kernel_label.set_ellipsize(Pango.EllipsizeMode.END)
                info_box.pack_start(kernel_label, False, False, 0)
                
                # Description with kernel type and size
                desc_label = Gtk.Label()
                desc_label.set_markup(f'<span size="small" color="#555555">{GLib.markup_escape_text(kernel_info["description"])}</span>')
                desc_label.set_halign(Gtk.Align.START)
                desc_label.set_ellipsize(Pango.EllipsizeMode.END)
                info_box.pack_start(desc_label, False, False, 0)
                
                main_box.pack_start(info_box, True, True, 0)
                
                # Status badges on the right - in horizontal line
                status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
                status_box.set_valign(Gtk.Align.CENTER)
                status_box.set_halign(Gtk.Align.END)
                
                # Primary status badge
                status_label = Gtk.Label()
                if kernel_info.get('is_active'):
                    status_text = _('ACTIVE')
                    status_label.get_style_context().add_class('active-kernel-badge')
                else:
                    status_text = _('AVAILABLE')
                    status_label.get_style_context().add_class('available-kernel-badge')
                
                status_label.set_markup(f'<span size="small" weight="bold">{GLib.markup_escape_text(status_text)}</span>')
                status_label.set_halign(Gtk.Align.CENTER)
                status_box.pack_start(status_label, False, False, 0)
                
                # Running badge (secondary) - in same line
                if kernel_info.get('is_running'):
                    running_label = Gtk.Label()
                    running_text = _('RUNNING')
                    running_label.get_style_context().add_class('running-kernel-badge')
                    running_label.set_markup(f'<span size="small" weight="bold">{GLib.markup_escape_text(running_text)}</span>')
                    running_label.set_halign(Gtk.Align.CENTER)
                    status_box.pack_start(running_label, False, False, 0)
                
                main_box.pack_start(status_box, False, False, 0)
                
                row.add(main_box)
                row.kernel_version = kernel
                self.packaged_kernel_list.add(row)
        
        self.packaged_kernel_list.show_all()

    def _on_packaged_kernel_selected(self, listbox, row):
        """Handle packaged kernel selection"""
        if row and hasattr(row, 'kernel_version'):
            self.selected_packaged_kernel = row.kernel_version
            
            kernel_info = get_kernel_info(self.minios_path, self.selected_packaged_kernel)
            if not kernel_info:
                return

            # Kernel status tracking (buttons removed, only context menu now)
            is_active = kernel_info['status'] == 'active'
            is_running = is_kernel_currently_running(self.selected_packaged_kernel)
            # Context menu will handle sensitivity based on kernel status
        else:
            self.selected_packaged_kernel = None
            # No buttons to update - using context menu only

    def _on_activate_clicked(self, button):
        """Handle activate kernel button click"""
        if not hasattr(self, 'selected_packaged_kernel') or not self.selected_packaged_kernel:
            self._show_error(_("Please select a kernel to activate"))
            return
        
        if not self.minios_path or not self.minios_writable:
            self._show_error(_("MiniOS directory is not writable"))
            return
        
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=_("Activate Kernel")
        )
        dialog.format_secondary_text(
            _("Are you sure you want to activate kernel {}?\n\n" 
              "This will deactivate the current kernel and update the bootloader configuration.").format(
                self.selected_packaged_kernel)
        )
        
        response = dialog.run()
        dialog.destroy()
        
        if response == Gtk.ResponseType.YES:
            self._activate_kernel()

    def _activate_kernel(self):
        """Activate the selected kernel with loading overlay"""
        # Show loading overlay
        self._show_activate_loading(True, _("Activating kernel, please wait..."))
        
        # Activate kernel in background thread
        def activate_kernel_bg():
            try:
                success, output = activate_kernel_cli(self.selected_packaged_kernel)
                
                # Update UI in main thread
                if success:
                    GLib.idle_add(self._on_kernel_activation_complete, True, None, self.selected_packaged_kernel)
                else:
                    GLib.idle_add(self._on_kernel_activation_complete, False, output, self.selected_packaged_kernel)
                
            except Exception as e:
                GLib.idle_add(self._on_kernel_activation_complete, False, str(e), self.selected_packaged_kernel)
        
        thread = threading.Thread(target=activate_kernel_bg, daemon=True)
        thread.start()
    
    def _on_kernel_activation_complete(self, success, error, kernel_version):
        """Handle kernel activation completion"""
        # Hide loading overlay
        self._show_activate_loading(False)
        
        if success:
            # Refresh kernel list - no dialog needed, user can see the result in badges
            self._populate_packaged_kernels()
        else:
            error_message = f"Failed to activate kernel"
            if error:
                error_message += f": {error}"
            self._show_error(error_message)
        
        return False  # Don't repeat this idle callback

    def _on_delete_clicked(self, button):
        """Handle delete kernel button click"""
        if not hasattr(self, 'selected_packaged_kernel') or not self.selected_packaged_kernel:
            self._show_error(_("Please select a kernel to delete"))
            return
        
        if not self.minios_path or not self.minios_writable:
            self._show_error(_("MiniOS directory is not writable"))
            return
        
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=_("Delete Kernel")
        )
        dialog.format_secondary_text(
            _("Are you sure you want to delete kernel '{}'?\n\nThis action cannot be undone.").format(self.selected_packaged_kernel)
        )
        
        response = dialog.run()
        dialog.destroy()
        
        if response == Gtk.ResponseType.YES:
            self._delete_kernel()

    def _delete_kernel(self):
        """Delete the selected kernel"""
        try:
            success, message = delete_kernel_cli(self.selected_packaged_kernel)
            
            if success:
                self._populate_packaged_kernels()
            else:
                self._show_error(f"Failed to delete kernel")
                
        except Exception as e:
            self._show_error(f"Error deleting kernel: {str(e)}")


    def _on_browse_clicked(self, button):
        """Handle browse button click for manual package selection"""
        dialog = Gtk.FileChooserDialog(
            title=_("Select Kernel Package"),
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        
        cancel_button = dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        open_button = dialog.add_button(_("Open"), Gtk.ResponseType.OK)
        
        open_button.set_can_default(True)
        open_button.grab_default()
        
        dialog.set_default_size(650, 450)
        dialog.set_modal(True)
        
        downloads_dir = os.path.expanduser("~/Downloads")
        if os.path.exists(downloads_dir):
            dialog.set_current_folder(downloads_dir)
        else:
            dialog.set_current_folder(os.path.expanduser("~"))
        
        filter_deb = Gtk.FileFilter()
        filter_deb.set_name(_("Debian Package Files (*.deb)"))
        filter_deb.add_pattern("*.deb")
        dialog.add_filter(filter_deb)
        
        filter_all = Gtk.FileFilter()
        filter_all.set_name(_("All Files"))
        filter_all.add_pattern("*" )
        dialog.add_filter(filter_all)
        
        response = dialog.run()
        
        if response == Gtk.ResponseType.OK:
            selected_file = dialog.get_filename()
            if selected_file:
                self.selected_kernel = selected_file
                filename = os.path.basename(selected_file)
                self.selected_file_label.set_text(filename)
                self._update_buttons_state()  # Use centralized button state update
                
                # Show package information
                self._show_package_info(selected_file)
        
        dialog.destroy()

    def _show_package_info(self, package_path):
        """Show information about selected package"""
        try:
            # Get basic file info
            file_stat = os.stat(package_path)
            file_size = file_stat.st_size
            file_size_text = self._format_file_size(file_size)
            
            # Try to get package info using dpkg-deb
            info_lines = []
            info_lines.append(f"<b>File:</b> {os.path.basename(package_path)}")
            info_lines.append(f"<b>Size:</b> {file_size_text}")
            
            try:
                # Get package control info
                result = subprocess.run(['dpkg-deb', '-I', package_path], 
                                      capture_output=True, text=True, check=True)
                
                # Parse control information
                control_info = result.stdout
                package_name = ""
                version = ""
                architecture = ""
                description = ""
                maintainer = ""
                depends = ""
                
                for line in control_info.split('\n'):
                    line = line.strip()
                    if line.startswith('Package: '):
                        package_name = line.split(':', 1)[1].strip()
                    elif line.startswith('Version: '):
                        version = line.split(':', 1)[1].strip()
                    elif line.startswith('Architecture: '):
                        architecture = line.split(':', 1)[1].strip()
                    elif line.startswith('Description: '):
                        description = line.split(':', 1)[1].strip()
                    elif line.startswith('Maintainer: '):
                        maintainer = line.split(':', 1)[1].strip()
                        # Extract just name part before email
                        if '<' in maintainer:
                            maintainer = maintainer.split('<')[0].strip()
                    elif line.startswith('Depends: '):
                        depends = line.split(':', 1)[1].strip()
                
                if package_name:
                    info_lines.append(f"<b>Package:</b> {package_name}")
                if version:
                    info_lines.append(f"<b>Version:</b> {version}")
                if architecture:
                    info_lines.append(f"<b>Architecture:</b> {architecture}")
                if description:
                    info_lines.append(f"<b>Description:</b> {description}")
                    
                # Detect kernel type from package name
                if package_name:
                    pkg_lower = package_name.lower()
                    kernel_types = []
                    if 'rt' in pkg_lower:
                        kernel_types.append("Real-time")
                    if 'cloud' in pkg_lower:
                        kernel_types.append("Cloud-optimized")
                    if 'lowlatency' in pkg_lower:
                        kernel_types.append("Low-latency")
                    if 'generic' in pkg_lower:
                        kernel_types.append("Generic")
                    
                    if kernel_types:
                        info_lines.append(f"<b>Type:</b> {', '.join(kernel_types)}")
                
            except subprocess.CalledProcessError:
                info_lines.append("<i>Could not read package metadata</i>")
            
            # Set the info text
            info_text = '\n'.join(info_lines)
            self.package_info_label.set_markup(info_text)
            self.package_info_box.show_all()
            
        except Exception as e:
            self.package_info_label.set_markup(f"<i>Error reading package: {str(e)}</i>")
            self.package_info_box.show_all()

    def _format_file_size(self, size_bytes):
        """Format file size in human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def _populate_kernels(self):
        """Populate kernel list for manual packages (quick operation)"""
        try:
            # Manual packages - should be fast
            kernels = get_manual_packages()
            self._populate_kernels_with_data(kernels, "manual")
        except Exception as e:
            self._show_kernel_fetch_error(str(e))

    def _show_kernel_loading(self):
        """Show loading indicator in kernel list"""
        # Clear existing kernels
        for child in self.kernel_list.get_children():
            self.kernel_list.remove(child)
        
        loading_row = Gtk.ListBoxRow()
        loading_row.set_sensitive(False)
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        main_box.set_halign(Gtk.Align.CENTER)
        
        spinner = Gtk.Spinner()
        spinner.start()
        main_box.pack_start(spinner, False, False, 0)
        
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        
        if self.repo_radio.get_active():
            status_text = _("Fetching kernel list from repository...")
        else:
            status_text = _("Scanning for manual packages...")
        
        status_label = Gtk.Label()
        status_label.set_markup(f'<b>{status_text}</b>')
        status_label.set_halign(Gtk.Align.START)
        info_box.pack_start(status_label, False, False, 0)
        
        main_box.pack_start(info_box, False, False, 0)
        
        loading_row.add(main_box)
        self.kernel_list.add(loading_row)
        self.kernel_list.show_all()

    def _fetch_repository_kernels_threaded(self):
        """Fetch repository kernels in background thread"""
        try:
            kernels = get_repository_kernels()
            
            # Check if kernels list is empty (may indicate outdated package cache)
            if not kernels:
                GLib.idle_add(self._show_package_cache_outdated_dialog)
                return
                
            GLib.idle_add(self._populate_kernels_with_data, kernels, "repository")
        except Exception as e:
            GLib.idle_add(self._show_kernel_fetch_error, str(e))

    def _populate_kernels_with_data(self, kernels, source_type):
        """Populate kernel list with pre-fetched data"""
        for child in self.kernel_list.get_children():
            self.kernel_list.remove(child)
        
        self.kernel_source = source_type
        
        if not kernels:
            self._show_no_kernels_found()
            return
        
        for kernel_data in kernels:
            # Handle both old format (strings) and new format (dicts)
            if isinstance(kernel_data, dict):
                kernel_name = kernel_data['package']
                kernel_info = kernel_data
            else:
                kernel_name = kernel_data
                kernel_info = None
                
            row = Gtk.ListBoxRow()
            
            main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
            main_box.get_style_context().add_class('kernel-item')
            
            # Use unified icon for all kernels
            icon_name = "package-x-generic"
            img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DND)
            main_box.pack_start(img, False, False, 0)
            
            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            info_box.set_hexpand(True)
            
            # Main kernel name
            kernel_label = Gtk.Label()
            display_name = kernel_name.replace('linux-image-', '') if kernel_name.startswith('linux-image-') else kernel_name
            kernel_label.set_markup(f'<b><span size="large">{GLib.markup_escape_text(display_name)}</span></b>')
            kernel_label.set_halign(Gtk.Align.START)
            kernel_label.set_ellipsize(Pango.EllipsizeMode.END)
            info_box.pack_start(kernel_label, False, False, 0)
            
            # Description and details
            if kernel_info and source_type == "repository":
                desc_parts = []
                if kernel_info.get('description'):
                    desc_parts.append(kernel_info['description'])
                if kernel_info.get('architecture'):
                    desc_parts.append(f"({kernel_info['architecture']})")
                if kernel_info.get('size_text'):
                    desc_parts.append(f"• {kernel_info['size_text']}")
                
                desc_text = " ".join(desc_parts)
            else:
                desc_text = _("Manual package") if source_type == "manual" else _("Repository kernel")
            
            desc_label = Gtk.Label()
            desc_label.set_markup(f'<span size="small" color="#555555">{GLib.markup_escape_text(desc_text)}</span>')
            desc_label.set_halign(Gtk.Align.START)
            desc_label.set_ellipsize(Pango.EllipsizeMode.END)
            info_box.pack_start(desc_label, False, False, 0)
            
            # Technical info for repository packages
            if kernel_info and source_type == "repository":
                tech_parts = []
                
                # Add kernel type detection from package name
                pkg_name = kernel_info['package'].lower()
                if 'rt' in pkg_name:
                    tech_parts.append("Real-time")
                elif 'cloud' in pkg_name:
                    tech_parts.append("Cloud-optimized")
                elif 'lowlatency' in pkg_name:
                    tech_parts.append("Low-latency")
                
                if tech_parts:
                    version_label = Gtk.Label()
                    version_text = " • ".join(tech_parts)
                    version_label.set_markup(f'<span size="x-small" color="#777777">{GLib.markup_escape_text(version_text)}</span>')
                    version_label.set_halign(Gtk.Align.START)
                    version_label.set_ellipsize(Pango.EllipsizeMode.END)
                    info_box.pack_start(version_label, False, False, 0)
            
            main_box.pack_start(info_box, True, True, 0)
            
            # Status badges on the right - repository kernels are always available
            status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            status_box.set_valign(Gtk.Align.CENTER)
            status_box.set_halign(Gtk.Align.END)
            
            # Repository kernels are always available for download
            status_label = Gtk.Label()
            status_text = _('AVAILABLE')
            status_label.get_style_context().add_class('available-kernel-badge')
            status_label.set_markup(f'<span size="small" weight="bold">{GLib.markup_escape_text(status_text)}</span>')
            status_label.set_halign(Gtk.Align.CENTER)
            status_box.pack_start(status_label, False, False, 0)
            
            main_box.pack_start(status_box, False, False, 0)
            
            row.add(main_box)
            row.kernel_version = kernel_name
            row.kernel_info = kernel_info
            self.kernel_list.add(row)
            
        self.kernel_list.show_all()

    def _show_package_cache_outdated_dialog(self):
        """Show dialog when package cache appears to be outdated"""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=_("Package database outdated")
        )
        dialog.format_secondary_text(_("The repository kernel list is empty. This may indicate an outdated package database. Update package lists now?"))
        
        def on_response(dialog, response_id):
            dialog.destroy()
            if response_id == Gtk.ResponseType.YES:
                self._update_package_lists_with_progress()
            else:
                # Show empty list message
                self._show_no_kernels_found()
        
        dialog.connect('response', on_response)
        dialog.show()

    def _update_package_lists_with_progress(self):
        """Update package lists with progress indication"""
        # Show loading message
        for child in self.kernel_list.get_children():
            self.kernel_list.remove(child)
        
        loading_row = Gtk.ListBoxRow()
        loading_row.set_sensitive(False)
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        main_box.set_halign(Gtk.Align.CENTER)
        
        spinner = Gtk.Spinner()
        spinner.start()
        main_box.pack_start(spinner, False, False, 0)
        
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        
        title_label = Gtk.Label()
        title_label.set_markup(f'<b>{_("Updating package lists...")}</b>')
        title_label.set_halign(Gtk.Align.START)
        info_box.pack_start(title_label, False, False, 0)
        
        main_box.pack_start(info_box, True, True, 0)
        loading_row.add(main_box)
        self.kernel_list.add(loading_row)
        self.kernel_list.show_all()
        
        # Run update in background thread
        def update_thread():
            success, message = update_package_lists_gui()
            GLib.idle_add(self._on_package_lists_updated, success, message)
        
        threading.Thread(target=update_thread, daemon=True).start()

    def _on_package_lists_updated(self, success, message):
        """Handle package lists update completion"""
        if success:
            # Refresh repository kernels list
            self._show_kernel_loading()
            thread = threading.Thread(target=self._fetch_repository_kernels_threaded)
            thread.daemon = True
            thread.start()
        else:
            # Show error and empty list
            for child in self.kernel_list.get_children():
                self.kernel_list.remove(child)
            
            error_dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=_("Failed to update package lists")
            )
            error_dialog.format_secondary_text(message)
            error_dialog.run()
            error_dialog.destroy()
            
            self._show_no_kernels_found()

    def _show_no_kernels_found(self):
        """Show message when no kernels are found"""
        no_kernels_row = Gtk.ListBoxRow()
        no_kernels_row.set_sensitive(False)
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        main_box.set_halign(Gtk.Align.CENTER)
        
        icon = Gtk.Image.new_from_icon_name("dialog-warning", Gtk.IconSize.DND)
        main_box.pack_start(icon, False, False, 0)
        
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        
        if self.kernel_source == "repository":
            title_text = _("No repository kernels found")
        else:
            title_text = _("No manual packages found")
        
        title_label = Gtk.Label()
        title_label.set_markup(f'<b>{title_text}</b>')
        title_label.set_halign(Gtk.Align.START)
        info_box.pack_start(title_label, False, False, 0)
        
        main_box.pack_start(info_box, False, False, 0)
        
        no_kernels_row.add(main_box)
        self.kernel_list.add(no_kernels_row)
        self.kernel_list.show_all()

    def _show_kernel_fetch_error(self, error_msg):
        """Show error when kernel fetching fails"""
        error_row = Gtk.ListBoxRow()
        error_row.set_sensitive(False)
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        main_box.set_halign(Gtk.Align.CENTER)
        
        icon = Gtk.Image.new_from_icon_name("dialog-error", Gtk.IconSize.DND)
        main_box.pack_start(icon, False, False, 0)
        
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        
        title_label = Gtk.Label()
        title_label.set_markup(f'<b>{_("Failed to fetch repository kernels")}</b>')
        title_label.set_halign(Gtk.Align.START)
        info_box.pack_start(title_label, False, False, 0)
        
        detail_label = Gtk.Label()
        detail_label.set_markup(f'<span size="small" color="#666666">{GLib.markup_escape_text(error_msg)}</span>')
        detail_label.set_halign(Gtk.Align.START)
        info_box.pack_start(detail_label, False, False, 0)
        
        main_box.pack_start(info_box, False, False, 0)
        
        error_row.add(main_box)
        self.kernel_list.add(error_row)
        self.kernel_list.show_all()

    def _on_kernel_source_changed(self, radio_button):
        """Handle kernel source change"""
        if radio_button.get_active():
            self._update_kernel_selection_ui()

    def _update_kernel_selection_ui(self):
        """Update kernel selection UI based on selected source"""
        if self.repo_radio.get_active():
            # Show repository kernel list, hide manual selection
            self.repo_selection_box.show()
            self.manual_selection_box.hide()
            self._show_kernel_loading()
            thread = threading.Thread(target=self._fetch_repository_kernels_threaded)
            thread.daemon = True
            thread.start()
        elif self.local_radio.get_active():
            # Show manual selection, hide repository kernel list
            self.manual_selection_box.show()
            self.repo_selection_box.hide()
            # Reset file selection
            self.selected_file_label.set_text(_("No file selected"))
            self.selected_kernel = None
            self._update_buttons_state()  # Use centralized button state update

    def _on_kernel_selected(self, listbox, row):
        """Handle kernel selection change"""
        # listbox parameter is not used
        if row:
            self.selected_kernel = row.kernel_version
            self._update_buttons_state()  # Use centralized button state update
        else:
            self.selected_kernel = None
            self._update_buttons_state()  # Use centralized button state update

    def _on_sqfs_compression_changed(self, combo):
        """Handle SquashFS compression change"""
        self.sqfs_compression = combo.get_active_text()


    def _on_build_clicked(self, button):
        """Start build process"""
        # button parameter is not used
        if not self.selected_kernel:
            self._show_error(_("Please select a kernel first"))
            return

        if not self.minios_path or not self.minios_writable:
            self._show_error(_("MiniOS directory is not writable"))
            return

        if self.is_building:
            return

        # Save current UI state before switching to progress
        self._save_ui_state()
        
        # Switch to progress UI
        self._build_progress_ui()
        
        self.is_building = True
        self.cancel_requested = False

        # Run the packaging CLI tool asynchronously
        self._run_package_cli_async()

    def _run_package_cli_async(self):
        """Run the minios-kernel CLI tool asynchronously."""
        try:
            self.temp_output_dir = tempfile.mkdtemp(prefix="minios-kernel-")
            
            # Build command arguments for CLI
            cmd_args = ['--json', 'package']
            
            if self.kernel_source == 'repository':
                cmd_args.extend(['--repo', self.selected_kernel])
            else:
                cmd_args.extend(['--deb', self.selected_kernel])
            
            # The kernel version is not known before packaging, so we pass a placeholder
            # The CLI tool will determine the actual version
            cmd_args.extend([
                '-o', self.temp_output_dir,
                '--sqfs-comp', self.sqfs_compression
            ])

            # Build pkexec command
            cmd = ['pkexec', 'minios-kernel'] + cmd_args

            # Log the command being executed
            self._log_message(_("Executing command: {}").format(" ".join(cmd)))
            
            # Set environment for unbuffered output
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            env['PYTHONIOENCODING'] = 'utf-8'
            
            # Use stdbuf to disable all buffering, then run our CLI command
            stdbuf_cmd = ['stdbuf', '-oL', '-eL'] + cmd  # Line buffered for stdout/stderr
            
            self.process = subprocess.Popen(
                stdbuf_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,  # Line buffered
                env=env
            )
            self.active_pid = self.process.pid
            self._partial_line = ''
            
            # Set up a timer to read output periodically
            GLib.timeout_add(10, self._read_cli_output)  # Check every 10ms for responsiveness
            
            # Watch for process exit using polling
            GLib.timeout_add(500, self._check_process_exit)  # Check every 500ms

        except Exception as e:
            # Log detailed error information
            import traceback
            error_details = f"Failed to start packaging process: {str(e)}\n\nTraceback:\n{traceback.format_exc()}"
            self._log_message(error_details)
            
            # Show user-friendly error message
            self._show_error(_("Failed to start packaging process. Please check the log for detailed error information.") + f"\n\nError: {str(e)}")
            self._build_finished()

    def _on_cli_output(self, source, condition):
        """Callback to handle CLI output in real-time."""
        if condition == GLib.IO_IN:
            # Read all available data, not just one line
            try:
                data = source.read(1024)  # Read up to 1KB at a time
                if data:
                    # Split into lines and process each
                    lines = data.split('\n')
                    
                    # Handle partial line from previous read
                    if hasattr(self, '_partial_line'):
                        lines[0] = self._partial_line + lines[0]
                    
                    # Save incomplete last line
                    if not data.endswith('\n'):
                        self._partial_line = lines.pop()
                    else:
                        self._partial_line = ''
                    
                    # Process complete lines
                    for line in lines:
                        line_text = line.strip()
                        if line_text:  # Skip empty lines
                            # Try to update progress based on JSON output
                            is_json_processed = self._update_progress_from_cli_output(line_text)
                            
                            # Only log non-JSON messages to keep log readable
                            if not is_json_processed:
                                # Remove log prefixes (I:, E:, W:) for cleaner output
                                clean_message = line_text
                                for prefix in ['I: ', 'E: ', 'W: ']:
                                    if clean_message.startswith(prefix):
                                        clean_message = clean_message[len(prefix):]
                                        break
                                self._log_message(clean_message)
                    
                    return True
            except Exception as e:
                print(f"Error reading CLI output: {e}", flush=True)
        return False

    def _read_cli_output(self):
        """Read CLI output in real-time using subprocess"""
        if not hasattr(self, 'process') or self.process is None:
            return False
        
        try:
            # Try to read a line without blocking using poll
            import os
            import fcntl
            
            # Set stdout to non-blocking
            fd = self.process.stdout.fileno()
            fl = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
            
            try:
                line = self.process.stdout.readline()
                if line:
                    line_text = line.strip()
                    if line_text:
                        # Update progress based on CLI output (JSON format)
                        self._update_progress_from_cli_output(line_text)
                        
                        # Only log non-JSON messages to keep log readable
                        if not (line_text.strip().startswith('{') and line_text.strip().endswith('}')):
                            # Remove log prefixes (I:, E:, W:) for cleaner output
                            clean_message = line_text
                            for prefix in ['I: ', 'E: ', 'W: ']:
                                if clean_message.startswith(prefix):
                                    clean_message = clean_message[len(prefix):]
                                    break
                            self._log_message(clean_message)
                        
                        # Force GUI update
                        while Gtk.events_pending():
                            Gtk.main_iteration()
                        
                        return True
            except IOError:
                # No data available, that's okay
                pass
            
            return True  # Continue timer
            
        except Exception as e:
            print(f"Error reading CLI output: {e}", flush=True)
            return True  # Continue trying
    
    def _check_process_exit(self):
        """Check if process has exited"""
        if not hasattr(self, 'process') or self.process is None:
            return False
        
        poll = self.process.poll()
        if poll is not None:
            # Process has exited
            self._on_cli_exit(self.active_pid, poll)
            return False  # Stop timer
        
        return True  # Continue timer

    def _update_progress_from_cli_output(self, line_text):
        """Update progress bar based on CLI output. Returns True if line was processed as JSON."""
        # Try to parse JSON progress output first
        if line_text.strip().startswith('{') and line_text.strip().endswith('}'):
            try:
                data = json.loads(line_text)
                if data.get('type') == 'progress':
                    percent = data.get('percent', 0)
                    message = data.get('message', '')
                    progress = percent / 100.0
                    
                    # Translate the message 
                    if message:
                        translated_message = _(message)
                        self._update_progress(progress, translated_message)
                    else:
                        self._update_progress(progress, "")
                    return True
                elif data.get('type') == 'success':
                    self._update_progress(1.0, _("Kernel packaging completed successfully!"))
                    return True
                elif data.get('type') == 'error':
                    error_msg = data.get('error', 'Unknown error')
                    self._log_message(f"E: {error_msg}")
                    return True
            except (json.JSONDecodeError, KeyError):
                # Not JSON or invalid JSON, continue with legacy parsing
                pass
        
        return False

    def _on_cli_exit(self, pid, status):
        """Callback for when the CLI process finishes."""
        if hasattr(self, 'process') and self.process:
            # Using subprocess.Popen
            self.process = None
        else:
            # Using GLib.spawn_async (fallback)
            GLib.spawn_close_pid(pid)
        self.active_pid = None

        if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
            
            # Pre-define translatable messages
            MSG_CLI_SUCCESS = _("CLI tool finished successfully, installing to repository...")
            MSG_INSTALLING_TO_REPO = _("Installing kernel to repository...")
            MSG_COMPLETED = _("Kernel packaging completed successfully!")
            
            self._update_progress(0.95, MSG_CLI_SUCCESS)
            self._log_message(_("CLI tool finished successfully."))
            # Move the packaged files to the repository
            try:
                self._update_progress(0.98, MSG_INSTALLING_TO_REPO)
                # When using CLI, files should already be in the right place
                # Just verify they exist and refresh the kernel list
                packaged_files = os.listdir(self.temp_output_dir)
                vmlinuz_file = next((f for f in packaged_files if f.startswith('vmlinuz-')), None)
                if not vmlinuz_file:
                    raise Exception("Could not find vmlinuz file in package output")
                
                kernel_version = vmlinuz_file.replace('vmlinuz-','')
                self._log_message(f"Kernel {kernel_version} packaged successfully to {self.temp_output_dir}")
                
                self._update_progress(1.0, MSG_COMPLETED)
                GLib.idle_add(self._populate_packaged_kernels)
                GLib.idle_add(self._show_completion_message)
            except Exception as e:
                GLib.idle_add(self._show_error, f"Failed to process package output: {str(e)}")
        else:
            
            # Log the error with exit status
            error_msg = _("Kernel packaging failed with exit code: {}").format(status)
            self._log_message(error_msg)
            
            # Show error dialog with more information
            full_error = _("Kernel packaging failed with exit code: {}. Please check the log above for detailed error information.").format(status)
            self._show_error(full_error)

        # Temporary output directory cleanup is handled by CLI via signal handlers
        # GUI should not clean up CLI temporary files
        
        self._build_finished()

    def _build_progress_ui(self):
        """Build progress interface"""
        # Clear existing UI
        for child in self.main_vbox.get_children():
            self.main_vbox.remove(child)

        # Status label (moved above progress bar)
        self.status_label = Gtk.Label(label=_("Starting packaging process..."))
        self.status_label.set_halign(Gtk.Align.START)
        self.main_vbox.pack_start(self.status_label, False, False, 0)

        # Progress bar
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.main_vbox.pack_start(self.progress_bar, False, False, 0)

        # Log output
        log_frame = Gtk.Frame()
        log_frame.set_label(_("Packaging Log"))
        
        scrolled_log = Gtk.ScrolledWindow()
        scrolled_log.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        
        self.log_textview = Gtk.TextView()
        self.log_textview.set_editable(False)
        self.log_buffer = self.log_textview.get_buffer()
        scrolled_log.add(self.log_textview)
        
        # Create cancellation overlay components
        self.cancel_loading_spinner = Gtk.Spinner()
        self.cancel_loading_label = Gtk.Label(label=_("Cancelling and cleaning up..."))
        self.cancel_loading_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.cancel_loading_box.pack_start(self.cancel_loading_spinner, False, False, 0)
        self.cancel_loading_box.pack_start(self.cancel_loading_label, False, False, 0)
        self.cancel_loading_box.set_halign(Gtk.Align.CENTER)
        self.cancel_loading_box.set_valign(Gtk.Align.CENTER)
        self.cancel_loading_box.get_style_context().add_class('loading-overlay')
        
        # Create overlay for log area
        log_overlay = Gtk.Overlay()
        log_overlay.add(scrolled_log)
        log_overlay.add_overlay(self.cancel_loading_box)
        self.cancel_loading_box.set_visible(False)  # Initially hidden
        
        log_frame.add(log_overlay)
        self.main_vbox.pack_start(log_frame, True, True, 0)

        # Bottom buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)
        self.main_vbox.pack_start(button_box, False, False, 0)

        self.cancel_button = Gtk.Button.new_with_label(_("Cancel"))
        self.cancel_button.connect("clicked", self._on_cancel_clicked)
        self.cancel_button.get_style_context().add_class('destructive-action')
        button_box.pack_start(self.cancel_button, False, False, 0)

        self.show_all()
        
        # Initialize overlays after show_all()
        self._initialize_loading_overlays()

    def _save_ui_state(self):
        """Save current UI state before build"""
        self.saved_state = {
            'selected_kernel': self.selected_kernel,
            'kernel_source': self.kernel_source,
            'sqfs_compression': self.sqfs_compression,
            'selected_file_path': getattr(self, 'selected_file_path', None)
        }

    def _restore_ui_state(self):
        """Restore UI state after build completion/cancellation"""
        if hasattr(self, 'saved_state') and self.saved_state:
            # Restore data
            self.selected_kernel = self.saved_state['selected_kernel']
            self.kernel_source = self.saved_state['kernel_source']
            self.sqfs_compression = self.saved_state['sqfs_compression']
            if self.saved_state['selected_file_path']:
                self.selected_file_path = self.saved_state['selected_file_path']
            
            # Restore UI elements
            if hasattr(self, 'sqfs_combo'):
                compressions = get_available_compressions()
                if self.sqfs_compression in compressions:
                    self.sqfs_combo.set_active(compressions.index(self.sqfs_compression))
                    
                    
            # Restore radio buttons
            if hasattr(self, 'local_radio') and hasattr(self, 'repo_radio'):
                if self.kernel_source == 'manual':
                    self.local_radio.set_active(True)
                else:
                    self.repo_radio.set_active(True)
                    
            # Restore selected file label
            if hasattr(self, 'selected_file_label') and self.saved_state['selected_file_path']:
                import os
                filename = os.path.basename(self.saved_state['selected_file_path'])
                self.selected_file_label.set_text(filename)

    def _show_back_button(self):
        """Replace cancel button with back button after cancellation"""
        # Find and remove the current button box
        for child in self.main_vbox.get_children():
            if isinstance(child, Gtk.Box) and any(isinstance(grandchild, Gtk.Button) for grandchild in child.get_children()):
                self.main_vbox.remove(child)
                break
        
        # Create new button box with Back button
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)
        
        back_button = Gtk.Button.new_with_label(_("Back to Selection"))
        back_button.connect("clicked", lambda _: self._build_selection_ui())
        back_button.get_style_context().add_class('suggested-action')
        button_box.pack_start(back_button, False, False, 0)
        
        self.main_vbox.pack_start(button_box, False, False, 0)
        self.show_all()
        
        # Ensure overlays remain hidden after show_all()
        self._initialize_loading_overlays()

    def _on_cancel_clicked(self, button):
        """Cancel build process and return to selection"""
        # button parameter is not used
        
        # Show cancellation overlay immediately
        self._show_cancel_overlay()
        
        # Disable the cancel button to prevent multiple clicks
        self.cancel_button.set_sensitive(False)
        
        self.cancel_requested = True
        self.is_building = False
        self._log_message(_("Cancelling packaging..."))
        
        # Use GLib.timeout_add to handle process termination without freezing UI
        def handle_cancellation():
            if self.active_pid:
                # First try graceful termination (SIGTERM)
                try:
                    os.kill(self.active_pid, 15)  # SIGTERM
                    # Wait a bit for graceful shutdown
                    time.sleep(2)
                    # If still running, force kill
                    try:
                        os.kill(self.active_pid, 0)  # Check if process still exists
                        os.kill(self.active_pid, 9)  # SIGKILL if still alive
                    except ProcessLookupError:
                        # Process already terminated gracefully
                        pass
                except ProcessLookupError:
                    # Process already dead
                    pass
            
            # CLI handles all cleanup when it receives termination signal
            
            # Hide cancellation overlay first, then proceed with finishing
            self._hide_cancel_overlay()
            
            # Force GUI update to ensure overlay is hidden before showing result
            while Gtk.events_pending():
                Gtk.main_iteration()
            
            # Complete the build process
            self._build_finished()
            
            return False  # Don't repeat this timeout
        
        # Run cancellation in a brief timeout to allow UI to update
        GLib.timeout_add(100, handle_cancellation)

    def _show_cancel_overlay(self):
        """Show cancellation overlay with spinner"""
        if hasattr(self, 'cancel_loading_box'):
            self.cancel_loading_box.set_visible(True)
            self.cancel_loading_spinner.start()
            
            # Force GUI update
            while Gtk.events_pending():
                Gtk.main_iteration()

    def _hide_cancel_overlay(self):
        """Hide cancellation overlay"""
        if hasattr(self, 'cancel_loading_box'):
            self.cancel_loading_box.set_visible(False)
            self.cancel_loading_spinner.stop()

    def _build_finished(self):
        """Called when build is finished"""
        self.is_building = False
        
        # Ensure cancel overlay is hidden
        self._hide_cancel_overlay()
        
        if self.cancel_requested:
            # Don't hide the log on cancel, just change the buttons
            self._show_back_button()
            return
            
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)
        
        back_button = Gtk.Button.new_with_label(_("Back to Selection"))
        back_button.connect("clicked", lambda _: self._build_selection_ui())
        back_button.get_style_context().add_class('suggested-action')
        button_box.pack_start(back_button, False, False, 0)
        
        for child in self.main_vbox.get_children():
            if isinstance(child, Gtk.Box):
                for btn_child in child.get_children():
                    if isinstance(btn_child, Gtk.Button) and btn_child.get_label() == _("Cancel"):
                        self.main_vbox.remove(child)
                        self.main_vbox.pack_start(button_box, False, False, 0)
                        self.show_all()
                        # Ensure overlays remain hidden after show_all()
                        self._initialize_loading_overlays()
                        return

    def _update_progress(self, fraction, text):
        """Update progress bar and status"""
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text("")
        self.status_label.set_text(text)
        # Only log progress text if it's meaningful and not empty
        if text and text.strip():
            self._log_message(text)

    def _log_message(self, message):
        """Add message to log"""
        timestamp = time.strftime("%H:%M:%S")
        full_message = f"[{timestamp}] {message}\n"
        
        end_iter = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end_iter, full_message)
        
        # Scroll to end safely
        if hasattr(self, 'log_textview') and self.log_textview.get_buffer() == self.log_buffer:
            end_iter = self.log_buffer.get_end_iter()
            self.log_textview.scroll_to_iter(end_iter, 0.0, False, 0.0, 0.0)

    def _show_error(self, message):
        """Show error dialog"""
        # Also log the error message
        self._log_message(message)
        
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=_("Error")
        )
        dialog.format_secondary_text(message)
        
        dialog.run()
        dialog.destroy()

    def _show_completion_message(self):
        """Show completion message with instructions"""
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=_("Packaging Complete")
        )
        
        kernel_version = self.selected_kernel or "unknown"
        
        instructions = (
            _("Kernel packaging completed successfully!") + "\n\n" +
            _("The new kernel is now available in the 'Manage Kernels' tab.")
        )
        
        dialog.format_secondary_text(instructions)
        dialog.run()
        dialog.destroy()

    def _show_activate_loading(self, show, text=None):
        """Show or hide kernel activation loading indicator"""
        if show:
            if text:
                self.activate_loading_label.set_text(text)
            # Ensure CSS class is applied every time we show the loading overlay
            self.activate_loading_box.get_style_context().add_class('loading-overlay')
            self.activate_loading_box.set_visible(True)
            self.activate_loading_spinner.start()
            # Context menu handles the interaction during loading
        else:
            self.activate_loading_box.set_visible(False)
            self.activate_loading_spinner.stop()
            # Reset to default text
            self.activate_loading_label.set_text(_("Activating kernel..."))
            # No buttons to re-enable - using context menu only
    
    def _initialize_loading_overlays(self):
        """Initialize loading overlays visibility after show_all()"""
        # Hide activation loading overlay (this must be called after show_all)
        if hasattr(self, 'activate_loading_box'):
            self.activate_loading_box.set_visible(False)
            self.activate_loading_spinner.stop()
            
        # Hide cancellation loading overlay (only exists in progress UI)
        if hasattr(self, 'cancel_loading_box'):
            self.cancel_loading_box.set_visible(False)
            self.cancel_loading_spinner.stop()
    
    def _create_context_menu(self):
        """Create context menu for kernel items"""
        self.context_menu = Gtk.Menu()
        self.context_menu.get_style_context().add_class('kernel-context-menu')
        
        # Activate menu item
        activate_item = Gtk.MenuItem(label=_("Activate Kernel"))
        activate_item.get_style_context().add_class('context-menu-activate')
        activate_item.connect("activate", self._on_context_activate)
        self.context_menu.append(activate_item)
        
        # Separator
        separator = Gtk.SeparatorMenuItem()
        self.context_menu.append(separator)
        
        # Delete menu item
        delete_item = Gtk.MenuItem(label=_("Delete Kernel"))
        delete_item.get_style_context().add_class('context-menu-delete')
        delete_item.connect("activate", self._on_context_delete)
        self.context_menu.append(delete_item)
        
        self.context_menu.show_all()
    
    def _on_list_button_press(self, widget, event):
        """Handle button press on kernel list"""
        if event.button == 3:  # Right click
            # Get the row under cursor
            row = self.packaged_kernel_list.get_row_at_y(int(event.y))
            
            if row:
                # Select the row
                self.packaged_kernel_list.select_row(row)
                self.selected_packaged_kernel = row.kernel_version
                
                # Update menu items based on kernel status
                activate_item = self.context_menu.get_children()[0]
                delete_item = self.context_menu.get_children()[2]
                
                # Get kernel info to check status
                kernel_info = get_kernel_info(self.minios_path, row.kernel_version)
                
                if kernel_info:
                    is_active = kernel_info.get('is_active', False)
                    is_running = kernel_info.get('is_running', False)
                    
                    # Check if MiniOS directory is writable
                    minios_writable = self.minios_writable
                    
                    # Disable activate if already active or directory not writable
                    if is_active:
                        activate_item.set_sensitive(False)
                    elif not minios_writable:
                        activate_item.set_sensitive(False)
                    else:
                        activate_item.set_sensitive(True)
                    
                    # Disable delete if active or running or directory not writable
                    if is_active or is_running:
                        delete_item.set_sensitive(False)
                    elif not minios_writable:
                        delete_item.set_sensitive(False)
                    else:
                        delete_item.set_sensitive(True)
                else:
                    # If we can't get kernel info, check writeability
                    activate_item.set_sensitive(self.minios_writable)
                    delete_item.set_sensitive(self.minios_writable)
                
                # Show context menu
                self.context_menu.popup_at_pointer(event)
                return True
        return False
    
    def _on_context_activate(self, menu_item):
        """Handle activate from context menu"""
        if hasattr(self, 'selected_packaged_kernel') and self.selected_packaged_kernel:
            # Trigger the same action as the activate button
            self._on_activate_clicked(None)
    
    def _on_context_delete(self, menu_item):
        """Handle delete from context menu"""
        if hasattr(self, 'selected_packaged_kernel') and self.selected_packaged_kernel:
            # Trigger the same action as the delete button
            self._on_delete_clicked(None)

# ──────────────────────────────────────────────────────────────────────────────
# Application Definition
# ──────────────────────────────────────────────────────────────────────────────
class MiniOSKernelManager(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APPLICATION_ID,
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = KernelPackWindow(application=self)
        win.present()

# ──────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────────────────────────────────────
def main():
    """Main entry point"""
    import signal
    
    def signal_handler(signum, frame):
        """Handle SIGINT (Ctrl+C) gracefully"""
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        app = MiniOSKernelManager()
        exit_status = app.run(sys.argv)
        sys.exit(exit_status)
    except KeyboardInterrupt:
        sys.exit(0)

if __name__ == "__main__":
    main()