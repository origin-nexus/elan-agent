ifeq ($(PACKAGE-NAME),)
$(error "Please define PACKAGE-NAME (and PACKAGE-DESC)")
endif

# Make sure that the MCN key exists in gpg configuration
.PHONY: gpgkey
gpgkey:
	@gpg --list-secret-keys "Origin Nexus" || gpg --import < ../core/packaging/gpg.key

$(HOME)/.dupload.conf: ../core/packaging/dupload.conf
	cp $< $@

gen-from-tmpl = @perl -pe 's:%{PACKAGE-NAME}:${PACKAGE-NAME}:g; s\#%{PACKAGE-DESC}\#${PACKAGE-DESC}\#g' $(1) > $(2)

.PHONY: deb-stable
deb-stable: ORIGIN_TARGET = stable
deb-stable: deb

.PHONY: deb
deb: gpgkey debian/changelog debian/control
	rm -f ../$(PACKAGE-NAME)_*
	debuild -b -mdebian@origin-nexus.com

ifneq ($(wildcard debian/preinst.in),)
deb: debian/preinst
endif

ifneq ($(wildcard debian/postinst.in),)
deb: debian/postinst
endif

ifneq ($(wildcard debian/prerm.in),)
deb: debian/prerm
endif

ifneq ($(wildcard debian/postrm.in),)
deb: debian/postrm
endif

# Make sure to regenerate changelog each time to add EPOCH time of build
.PHONY: debian/changelog
debian/changelog: debian/changelog.in
	$(call gen-from-tmpl,$<,$@)
	@if [ "$(ORIGIN_TARGET)" = "stable" ]; \
	then \
		perl -p -i -e 's:unstable:stable:g' $@;\
	else \
		# replace first line of change log with version followed by ~EPOCH (~ in deb-version ordering comes before anything, even nothing: version 1.0~whatevr is less that 1.0) \
		perl -p -i -e '$$now=time(); s:\((.*)\):($${1}~$$now): if 1 .. 1' $@ ;\
	fi

debian/control: debian/control.in
	$(call gen-from-tmpl,$<,$@)

debian/preinst: debian/preinst.in
	$(call gen-from-tmpl,$<,$@)

debian/postinst: debian/postinst.in
	$(call gen-from-tmpl,$<,$@)

debian/prerm: debian/prerm.in
	$(call gen-from-tmpl,$<,$@)

debian/postrm: debian/postrm.in
	$(call gen-from-tmpl,$<,$@)
