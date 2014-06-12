PACKAGE-NAME := ea-authentication
PACKAGE-DESC := Authentication component of Edge Agent
PACKAGE-DEPENDS := freeradius, freeradius-ldap, ea-core, python-mako

include ../core/packaging.mk

.PHONY: test
test:
	

.PHONY: install
install: freeradius python

.PHONY: python
python: origin/authentication.py
	install -d ${DESTDIR}${ORIGIN_PREFIX}/lib/python/origin
	install -t ${DESTDIR}${ORIGIN_PREFIX}/lib/python/origin origin/authentication.py
  
   
.PHONY: freeradius
freeradius:
	install -d ${DESTDIR}${ORIGIN_PREFIX}/authentication/freeradius
	install -m 644 freeradius.dictionary ${DESTDIR}${ORIGIN_PREFIX}/authentication/freeradius/dictionary
	install -m 644 freeradius.policy ${DESTDIR}${ORIGIN_PREFIX}/authentication/freeradius/policy
	install -m 644 freeradius.rest-module ${DESTDIR}${ORIGIN_PREFIX}/authentication/freeradius/rest-module
	install -m 644 freeradius.ldap-module ${DESTDIR}${ORIGIN_PREFIX}/authentication/freeradius/ldap-module
	install -d ${DESTDIR}${ORIGIN_PREFIX}/bin
	install -m 755 bin/authentication_provider ${DESTDIR}${ORIGIN_PREFIX}/bin/
	
