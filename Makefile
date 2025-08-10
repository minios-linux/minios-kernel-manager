EXECUTABLES = bin/minios-kernel-manager bin/minios-kernel
LIBRARIES = lib/*.py
APPLICATIONS = share/applications/minios-kernel-manager.desktop
POLICIES = share/polkit/dev.minios.kernel-manager.policy
STYLES = share/styles/style.css

BINDIR = usr/bin
LIBDIR = usr/lib/minios-kernel-manager
APPLICATIONSDIR = usr/share/applications
POLKITACTIONSDIR = usr/share/polkit-1/actions
LOCALEDIR = usr/share/locale
SHAREDIR = usr/share/minios-kernel-manager

PO_FILES = $(shell find po -maxdepth 1 -name "*.po")
MO_FILES = $(patsubst %.po,%.mo,$(PO_FILES))

build: mo

mo: $(MO_FILES)

update-po:
	@echo "Updating translation files..."
	./update-po.sh

%.mo: %.po
	@echo "Generating mo file for $<"
	msgfmt -o $@ $< 
	chmod 644 $@

clean:
	rm -rf $(MO_FILES)

install: build
	install -d $(DESTDIR)/$(BINDIR) \
				$(DESTDIR)/$(LIBDIR) \
				$(DESTDIR)/$(APPLICATIONSDIR) \
				$(DESTDIR)/$(POLKITACTIONSDIR) \
				$(DESTDIR)/$(LOCALEDIR) \
				$(DESTDIR)/$(SHAREDIR)

	cp $(EXECUTABLES) $(DESTDIR)/$(BINDIR)/
	cp $(LIBRARIES) $(DESTDIR)/$(LIBDIR)/
	chmod +x $(DESTDIR)/$(LIBDIR)/minios_kernel_manager.py
	chmod +x $(DESTDIR)/$(LIBDIR)/minios_kernel.py
	cp $(APPLICATIONS) $(DESTDIR)/$(APPLICATIONSDIR)
	cp $(POLICIES) $(DESTDIR)/$(POLKITACTIONSDIR)
	cp $(STYLES) $(DESTDIR)/$(SHAREDIR)

	@for MO_FILE in $(MO_FILES); do \
		LOCALE=$$(basename $$MO_FILE .mo); \
		echo "Copying mo file $$MO_FILE to $(DESTDIR)/usr/share/locale/$$LOCALE/LC_MESSAGES/minios-kernel-manager.mo"; \
		install -Dm644 "$$MO_FILE" "$(DESTDIR)/usr/share/locale/$$LOCALE/LC_MESSAGES/minios-kernel-manager.mo"; \
	done

uninstall:
	@echo "Uninstalling MiniOS Kernel Manager..."
	
	# Remove executables
	rm -f $(DESTDIR)/$(BINDIR)/minios-kernel-manager
	rm -f $(DESTDIR)/$(BINDIR)/minios-kernel
	
	# Remove library directory
	rm -rf $(DESTDIR)/$(LIBDIR)
	
	# Remove desktop file
	rm -f $(DESTDIR)/$(APPLICATIONSDIR)/minios-kernel-manager.desktop
	
	# Remove PolicyKit policy
	rm -f $(DESTDIR)/$(POLKITACTIONSDIR)/dev.minios.kernel-manager.policy
	
	# Remove shared directory
	rm -rf $(DESTDIR)/$(SHAREDIR)
	
	# Remove translations
	@for MO_FILE in $(MO_FILES); do \
		LOCALE=$$(basename $$MO_FILE .mo); \
		echo "Removing translation file for locale $$LOCALE"; \
		rm -f "$(DESTDIR)/usr/share/locale/$$LOCALE/LC_MESSAGES/minios-kernel-manager.mo"; \
		rmdir "$(DESTDIR)/usr/share/locale/$$LOCALE/LC_MESSAGES" 2>/dev/null || true; \
		rmdir "$(DESTDIR)/usr/share/locale/$$LOCALE" 2>/dev/null || true; \
	done
	
	# Remove man page (if installed by debhelper)
	rm -f $(DESTDIR)/usr/share/man/man1/minios-kernel-manager.1*
	
	# Remove lintian overrides (if installed by debhelper)  
	rm -f $(DESTDIR)/usr/share/lintian/overrides/minios-kernel-manager
	
	@echo "MiniOS Kernel Manager uninstalled successfully"

reinstall: uninstall install
	@echo "MiniOS Kernel Manager reinstalled successfully"

help:
	@echo "MiniOS Kernel Manager - Available targets:"
	@echo ""
	@echo "  build       - Build translation files (.mo)"
	@echo "  clean       - Remove built files (.mo)"
	@echo "  install     - Install to DESTDIR (default: /)"
	@echo "  uninstall   - Remove installed files from DESTDIR"
	@echo "  reinstall   - Uninstall and install again"
	@echo "  update-po   - Update translation template and files"
	@echo "  help        - Show this help message"
	@echo ""
	@echo "Variables:"
	@echo "  DESTDIR     - Installation prefix (default: /)"
	@echo ""
	@echo "Examples:"
	@echo "  make install DESTDIR=/tmp/test    # Install to test directory"
	@echo "  make uninstall                    # Remove from system"
	@echo "  sudo make reinstall               # Reinstall as root"

.PHONY: build mo update-po clean install uninstall reinstall help
