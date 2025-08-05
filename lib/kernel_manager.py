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

# If running from source, add the parent directory of 'lib' to the Python path
if not os.path.exists('/usr/lib/minios-kernel-manager'):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.dirname(script_dir))
    from lib.minios_utils import (
        find_minios_directory, list_all_kernels, get_kernel_info,
        get_currently_running_kernel, is_kernel_currently_running, get_system_type,
        package_kernel_to_repository, activate_kernel, delete_packaged_kernel
    )
    from lib.kernel_utils import get_repository_kernels, get_manual_packages, _format_size
    from lib.compression_utils import get_available_compressions
else:
    from minios_utils import (
        find_minios_directory, list_all_kernels, get_kernel_info,
        get_currently_running_kernel, is_kernel_currently_running, get_system_type,
        package_kernel_to_repository, activate_kernel, delete_packaged_kernel
    )
    from kernel_utils import get_repository_kernels, get_manual_packages, _format_size
    from compression_utils import get_available_compressions

gi.require_version('Gtk', '3.0')
gi.require_version('Gio', '2.0')
from gi.repository import Gtk, GLib, Gio, Pango

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
APPLICATION_ID   = 'dev.minios.kernel-manager'
APP_NAME         = 'minios-kernel-manager'
APP_TITLE        = 'MiniOS Kernel Manager'
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALE_DIRECTORY = os.path.join(BASE_DIR, 'po')
CSS_FILE_PATH = '/usr/share/minios-kernel-manager/style.css'

# Icons
ICON_WINDOW = 'system-software-install'
ICON_BUILD = 'document-send'
ICON_CANCEL = 'process-stop'

# Set up translations
try:
    locale.setlocale(locale.LC_ALL, '')
    gettext.bindtextdomain(APP_NAME, LOCALE_DIRECTORY)
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
        self.initrd_compression = "zstd"
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

        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, widget):
        _ = widget
        self.get_application().quit()

    def _detect_minios_directory(self):
        """Detect MiniOS directory"""
        self.minios_path = find_minios_directory()
        # Always set as writable since we'll use pkexec for privileged operations
        self.minios_writable = self.minios_path is not None

    def _build_header_bar(self):
        """Build the header bar"""
        header = Gtk.HeaderBar(show_close_button=True)
        header.props.title = _(APP_TITLE)
        self.set_titlebar(header)

    def _build_system_status_info(self):
        """Build system status information panel"""
        status_frame = Gtk.Frame()
        status_frame.set_label(_("System Status"))
        status_frame.set_margin_bottom(12)
        
        status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        status_box.set_margin_top(6)
        status_box.set_margin_bottom(6)
        status_box.set_margin_start(12)
        status_box.set_margin_end(12)
        
        # System type
        system_label = Gtk.Label()
        system_label.set_markup(f'<b>{_("System Type:")}</b> {self.system_type}')
        system_label.set_halign(Gtk.Align.START)
        status_box.pack_start(system_label, False, False, 0)
        
        # MiniOS directory status
        minios_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        minios_title = Gtk.Label()
        minios_title.set_markup(f'<b>{_("MiniOS Directory:")}</b>')
        minios_title.set_halign(Gtk.Align.START)
        minios_hbox.pack_start(minios_title, False, False, 0)
        
        if self.minios_path:
            minios_status = _("Found at {}").format(self.minios_path)
            if self.minios_writable:
                minios_icon_name = "emblem-default"  # Green checkmark
                minios_color = "#2E7D32"  # Green
                access_text = _("(writable)")
            else:
                minios_icon_name = "dialog-warning"  # Warning icon
                minios_color = "#F57C00"  # Orange
                access_text = _("(read-only)")
        else:
            minios_status = _("Not found")
            minios_icon_name = "dialog-error"  # Error icon
            minios_color = "#D32F2F"  # Red
            access_text = ""
        
        # Status icon
        status_icon = Gtk.Image.new_from_icon_name(minios_icon_name, Gtk.IconSize.MENU)
        minios_hbox.pack_start(status_icon, False, False, 0)
        
        # Status text
        minios_status_label = Gtk.Label()
        minios_status_label.set_markup(
            f'<span color="{minios_color}">{minios_status} {access_text}</span>'
        )
        minios_status_label.set_halign(Gtk.Align.START)
        minios_hbox.pack_start(minios_status_label, False, False, 0)
        
        status_box.pack_start(minios_hbox, False, False, 0)
        
        status_frame.add(status_box)
        self.main_vbox.pack_start(status_frame, False, False, 0)

    def _build_main_ui(self):
        """Build main interface with tabs"""
        # Clear existing UI
        for child in self.main_vbox.get_children():
            self.main_vbox.remove(child)

        # System status info
        self._build_system_status_info()
        
        # Create notebook (tabs)
        self.notebook = Gtk.Notebook()
        self.notebook.set_tab_pos(Gtk.PositionType.TOP)
        self.main_vbox.pack_start(self.notebook, True, True, 0)
        
        # Install tab
        install_tab = self._build_install_tab()
        install_label = Gtk.Label(label=_("Package Kernel"))
        self.notebook.append_page(install_tab, install_label)
        
        # Activate tab
        activate_tab = self._build_activate_tab()
        activate_label = Gtk.Label(label=_("Manage Kernels"))
        self.notebook.append_page(activate_tab, activate_label)
        
        # Show everything first, then apply initial visibility logic
        self.show_all()
        
        # Apply initial UI state (this must be after show_all)
        self._update_kernel_selection_ui()

    def _build_selection_ui(self):
        """Return to main selection interface"""
        self._build_main_ui()

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
        
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_width(400)
        sw.set_min_content_height(200)
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.packaged_kernel_list)
        tab_box.pack_start(sw, True, True, 0)
        
        # Activate and Delete buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)
        button_box.set_margin_top(12)
        tab_box.pack_start(button_box, False, False, 0)
        
        self.delete_button = Gtk.Button.new_with_label(_("Delete Kernel"))
        self.delete_button.get_style_context().add_class('destructive-action')
        self.delete_button.connect("clicked", self._on_delete_clicked)
        self.delete_button.set_sensitive(False)
        button_box.pack_start(self.delete_button, False, False, 0)
        
        self.activate_button = Gtk.Button.new_with_label(_("Activate Kernel"))
        self.activate_button.get_style_context().add_class('suggested-action')
        self.activate_button.connect("clicked", self._on_activate_clicked)
        self.activate_button.set_sensitive(False)
        button_box.pack_start(self.activate_button, False, False, 0)
        
        # Populate packaged kernels
        self._populate_packaged_kernels()
        
        return tab_box

    def _build_selection_ui_content(self, container):
        """Build kernel selection interface content"""
        # Main instruction label
        lbl = Gtk.Label(label=_("Please select a kernel to package and configure compression settings:"), xalign=0)
        lbl.set_margin_bottom(8)
        container.pack_start(lbl, False, False, 0)

        # Horizontal box for main content
        hb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        container.pack_start(hb, True, True, 0)

        # Left side - Kernel selection
        vb_kernel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        
        # Kernel source selection
        source_label = Gtk.Label(label=_("Kernel Source:"), xalign=0)
        vb_kernel.pack_start(source_label, False, False, 0)
        
        source_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.local_radio = Gtk.RadioButton.new_with_label_from_widget(None, _("Manual Package"))
        self.local_radio.set_active(True)
        self.local_radio.connect("toggled", self._on_kernel_source_changed)
        source_box.pack_start(self.local_radio, False, False, 0)
        
        self.repo_radio = Gtk.RadioButton.new_with_label_from_widget(self.local_radio, _("Repository"))
        self.repo_radio.connect("toggled", self._on_kernel_source_changed)
        source_box.pack_start(self.repo_radio, False, False, 0)
        
        vb_kernel.pack_start(source_box, False, False, 0)
        
        # Kernel selection area
        kernel_selection_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        
        # Manual package selection (only shown when Manual Package is selected)
        self.manual_selection_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        
        manual_label = Gtk.Label(label=_("Select Kernel Package:"), xalign=0)
        self.manual_selection_box.pack_start(manual_label, False, False, 0)
        
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
        sw.set_min_content_width(350)
        sw.set_min_content_height(200)
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(self.kernel_list)
        self.repo_selection_box.pack_start(sw, True, True, 0)
        
        kernel_selection_box.pack_start(self.repo_selection_box, True, True, 0)
        self.repo_selection_box.hide()  # Hidden by default (Manual Package is selected)
        
        vb_kernel.pack_start(kernel_selection_box, True, True, 0)
        
        hb.pack_start(vb_kernel, True, True, 0)

        # Right side - Compression settings
        vb_compress = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        
        compress_label = Gtk.Label(label=_("Compression Settings:"), xalign=0)
        vb_compress.pack_start(compress_label, False, False, 0)
        
        # SquashFS compression
        sqfs_frame = Gtk.Frame()
        sqfs_frame.set_label(_("SquashFS Compression"))
        sqfs_frame.set_margin_bottom(12)
        
        sqfs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sqfs_box.set_margin_top(6)
        sqfs_box.set_margin_bottom(6)
        sqfs_box.set_margin_start(12)
        sqfs_box.set_margin_end(12)
        
        self.sqfs_combo = Gtk.ComboBoxText()
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
        
        sqfs_box.pack_start(self.sqfs_combo, False, False, 0)
        sqfs_frame.add(sqfs_box)
        vb_compress.pack_start(sqfs_frame, False, False, 0)
        
        # Initramfs compression
        initrd_frame = Gtk.Frame()
        initrd_frame.set_label(_("Initramfs Compression"))
        
        initrd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        initrd_box.set_margin_top(6)
        initrd_box.set_margin_bottom(6)
        initrd_box.set_margin_start(12)
        initrd_box.set_margin_end(12)
        
        self.initrd_combo = Gtk.ComboBoxText()
        for comp in compressions:
            self.initrd_combo.append_text(comp)
        self.initrd_combo.set_active(default_index)
        self.initrd_combo.connect("changed", self._on_initrd_compression_changed)
        
        initrd_box.pack_start(self.initrd_combo, False, False, 0)
        initrd_frame.add(initrd_box)
        vb_compress.pack_start(initrd_frame, False, False, 0)
        
        hb.pack_start(vb_compress, False, False, 0)

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

    def _populate_packaged_kernels(self):
        """Populate list of packaged kernels"""
        # Clear existing kernels
        for child in self.packaged_kernel_list.get_children():
            self.packaged_kernel_list.remove(child)
        
        if not self.minios_path:
            return
        
        all_kernels = list_all_kernels(self.minios_path)
        
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
                kernel_info = get_kernel_info(self.minios_path, kernel)
                if not kernel_info:
                    continue

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
                
                # Status badge on the right
                status_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                status_box.set_valign(Gtk.Align.CENTER)
                
                status_label = Gtk.Label()
                status_color = kernel_info.get('status_color', '#666666')
                status_label.set_markup(f'<span size="small" weight="bold" color="{status_color}">{GLib.markup_escape_text(kernel_info["status"])}</span>')
                status_label.set_halign(Gtk.Align.END)
                status_box.pack_start(status_label, False, False, 0)
                
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

            is_active = kernel_info['status'] == 'active'
            is_running = is_kernel_currently_running(self.selected_packaged_kernel)
            
            self.activate_button.set_sensitive(not is_active)
            self.delete_button.set_sensitive(not is_active and not is_running)

            if is_active:
                self.activate_button.set_tooltip_text(_("This kernel is already active"))
                self.delete_button.set_tooltip_text(_("Cannot delete an active kernel"))
            elif is_running:
                self.delete_button.set_tooltip_text(_("Cannot delete the currently running kernel"))
            else:
                self.activate_button.set_tooltip_text(_("Activate this kernel"))
                self.delete_button.set_tooltip_text(_("Delete this kernel and all associated files"))
        else:
            self.selected_packaged_kernel = None
            self.activate_button.set_sensitive(False)
            self.delete_button.set_sensitive(False)
            self.activate_button.set_tooltip_text("")
            self.delete_button.set_tooltip_text("")

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
        """Activate the selected kernel"""
        try:
            success = activate_kernel(self.minios_path, self.selected_packaged_kernel)
            
            if success:
                dialog = Gtk.MessageDialog(
                    transient_for=self,
                    flags=0,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text=_("Kernel Activated")
                )
                dialog.format_secondary_text(
                    _("Kernel {} has been activated successfully!").format(self.selected_packaged_kernel)
                )
                dialog.run()
                dialog.destroy()
                
                self._populate_packaged_kernels()
            else:
                self._show_error(f"Failed to activate kernel")
                
        except Exception as e:
            self._show_error(f"Error activating kernel: {str(e)}")

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
            success = delete_packaged_kernel(self.minios_path, self.selected_packaged_kernel)
            
            if success:
                dialog = Gtk.MessageDialog(
                    transient_for=self,
                    flags=0,
                    message_type=Gtk.MessageType.INFO,
                    buttons=Gtk.ButtonsType.OK,
                    text=_("Kernel Deleted")
                )
                dialog.format_secondary_text(
                    _("Kernel '{}' has been deleted successfully!").format(self.selected_packaged_kernel)
                )
                dialog.run()
                dialog.destroy()
                
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
                self.build_button.set_sensitive(True)
                
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
            
            icon_name = "package-x-generic" if source_type == "manual" else "folder-download"
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
            
            
            row.add(main_box)
            row.kernel_version = kernel_name
            row.kernel_info = kernel_info
            self.kernel_list.add(row)
            
        self.kernel_list.show_all()

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
            self.build_button.set_sensitive(False)

    def _on_kernel_selected(self, listbox, row):
        """Handle kernel selection change"""
        _ = listbox  # Mark parameter as used
        if row:
            self.selected_kernel = row.kernel_version
            self.build_button.set_sensitive(True)
        else:
            self.selected_kernel = None
            self.build_button.set_sensitive(False)

    def _on_sqfs_compression_changed(self, combo):
        """Handle SquashFS compression change"""
        self.sqfs_compression = combo.get_active_text()

    def _on_initrd_compression_changed(self, combo):
        """Handle initramfs compression change"""
        self.initrd_compression = combo.get_active_text()

    def _on_build_clicked(self, button):
        """Start build process"""
        _ = button  # Mark parameter as used
        if not self.selected_kernel:
            self._show_error(_("Please select a kernel first"))
            return

        if self.is_building:
            return

        # Switch to progress UI
        self._build_progress_ui()
        
        self.is_building = True
        self.cancel_requested = False

        # Run the packaging CLI tool asynchronously
        self._run_package_cli_async()

    def _run_package_cli_async(self):
        """Run the minios-kernel-package CLI tool asynchronously."""
        try:
            self.temp_output_dir = tempfile.mkdtemp()
            
            cmd = ['minios-kernel-package']
            if self.kernel_source == 'repository':
                cmd.extend(['--source-repo', self.selected_kernel])
            else:
                cmd.extend(['--source-deb', self.selected_kernel])
            
            # The kernel version is not known before packaging, so we pass a placeholder
            # The CLI tool will determine the actual version
            cmd.extend([
                '--output-dir', self.temp_output_dir,
                '--squashfs-comp', self.sqfs_compression,
                '--initrd-comp', self.initrd_compression
            ])

            # Spawn the process asynchronously
            pid, stdin, stdout, stderr = GLib.spawn_async(
                cmd,
                flags=GLib.SpawnFlags.DO_NOT_REAP_CHILD,
                standard_output=True,
                standard_error=True
            )

            self.active_pid = pid

            # Watch stdout for real-time logging
            GLib.io_add_watch(GLib.IOChannel(stdout), GLib.IO_IN, self._on_cli_output)
            GLib.io_add_watch(GLib.IOChannel(stderr), GLib.IO_IN, self._on_cli_output)

            # Watch for process exit
            GLib.child_watch_add(pid, self._on_cli_exit)

        except Exception as e:
            self._show_error(f"Failed to start packaging process: {str(e)}")
            self._build_finished()

    def _on_cli_output(self, source, condition):
        """Callback to handle CLI output in real-time."""
        if condition == GLib.IO_IN:
            line = source.readline()
            if line:
                self._log_message(line.strip())
                return True
        return False

    def _on_cli_exit(self, pid, status):
        """Callback for when the CLI process finishes."""
        GLib.spawn_close_pid(pid)
        self.active_pid = None

        if os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0:
            self._log_message(_("CLI tool finished successfully."))
            # Now, move the packaged files to the repository
            try:
                packaged_files = os.listdir(self.temp_output_dir)
                vmlinuz_file = next((f for f in packaged_files if f.startswith('vmlinuz-')), None)
                if not vmlinuz_file:
                    raise Exception("Could not find vmlinuz file in package output")
                
                kernel_version = vmlinuz_file.replace('vmlinuz-','')

                package_kernel_to_repository(
                    self.minios_path, kernel_version,
                    os.path.join(self.temp_output_dir, f"01-kernel-{kernel_version}.sb"),
                    os.path.join(self.temp_output_dir, vmlinuz_file),
                    os.path.join(self.temp_output_dir, f"initrfs-{kernel_version}.img")
                )
                GLib.idle_add(self._populate_packaged_kernels)
                GLib.idle_add(self._show_completion_message)
            except Exception as e:
                GLib.idle_add(self._show_error, f"Failed to process package output: {str(e)}")
        else:
            self._show_error(_("Kernel packaging failed. Check log for details."))

        if os.path.exists(self.temp_output_dir):
            shutil.rmtree(self.temp_output_dir)
        
        self._build_finished()

    def _build_progress_ui(self):
        """Build progress interface"""
        # Clear existing UI
        for child in self.main_vbox.get_children():
            self.main_vbox.remove(child)

        # Progress label
        lbl = Gtk.Label(label=_("Packaging kernel..."), xalign=0)
        self.main_vbox.pack_start(lbl, False, False, 0)

        # Progress bar
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.main_vbox.pack_start(self.progress_bar, False, False, 0)

        # Status label
        self.status_label = Gtk.Label(label=_("Starting packaging process..."))
        self.status_label.set_halign(Gtk.Align.START)
        self.main_vbox.pack_start(self.status_label, False, False, 0)

        # Log output
        log_frame = Gtk.Frame()
        log_frame.set_label(_("Packaging Log"))
        
        scrolled_log = Gtk.ScrolledWindow()
        scrolled_log.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        
        self.log_textview = Gtk.TextView()
        self.log_textview.set_editable(False)
        self.log_buffer = self.log_textview.get_buffer()
        scrolled_log.add(self.log_textview)
        
        log_frame.add(scrolled_log)
        self.main_vbox.pack_start(log_frame, True, True, 0)

        # Bottom buttons
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)
        self.main_vbox.pack_start(button_box, False, False, 0)

        self.cancel_button = Gtk.Button.new_with_label(_("Cancel"))
        self.cancel_button.connect("clicked", self._on_cancel_clicked)
        button_box.pack_start(self.cancel_button, False, False, 0)

        self.show_all()

    def _on_cancel_clicked(self, button):
        """Cancel build process and return to selection"""
        _ = button  # Mark parameter as used
        if self.active_pid:
            os.kill(self.active_pid, 9) # Kill the process
        self.cancel_requested = True
        self.is_building = False
        self._log_message(_("Cancelling packaging..."))
        self._build_finished()

    def _build_finished(self):
        """Called when build is finished"""
        self.is_building = False
        
        if self.cancel_requested:
            self._build_selection_ui()
            return
            
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.set_halign(Gtk.Align.END)
        
        back_button = Gtk.Button.new_with_label(_("Back to Selection"))
        back_button.connect("clicked", lambda _: self._build_selection_ui())
        button_box.pack_start(back_button, False, False, 0)
        
        for child in self.main_vbox.get_children():
            if isinstance(child, Gtk.Box):
                for btn_child in child.get_children():
                    if isinstance(btn_child, Gtk.Button) and btn_child.get_label() == _("Cancel"):
                        self.main_vbox.remove(child)
                        self.main_vbox.pack_start(button_box, False, False, 0)
                        self.show_all()
                        return

    def _update_progress(self, fraction, text):
        """Update progress bar and status"""
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f"{int(fraction * 100)}%")
        self.status_label.set_text(text)
        self._log_message(text)

    def _log_message(self, message):
        """Add message to log"""
        timestamp = time.strftime("%H:%M:%S")
        full_message = f"[{timestamp}] {message}\n"
        
        end_iter = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end_iter, full_message)
        
        mark = self.log_buffer.get_insert()
        self.log_textview.scroll_mark_onscreen(mark)

    def _show_error(self, message):
        """Show error dialog"""
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