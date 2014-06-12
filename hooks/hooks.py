#!/usr/bin/python
# Copyright 2014 Canonical Ltd. All rights reserved.

import subprocess

from charmhelpers import fetch
from charmhelpers.core import hookenv, host
from charmhelpers.core.hookenv import (
    log, CRITICAL, ERROR, WARNING, INFO, DEBUG)


hooks = hookenv.Hooks()

SERVICE_NAME = 'pgbouncer'


@hooks.hook()
def start():
    host.service_start(SERVICE_NAME)


@hooks.hook()
def stop():
    host.service_stop(SERVICE_NAME)


@hooks.hook()
def install():
    for f in glob.glob('exec.d/*/charm-pre-install'):
        if os.path.isfile(f) and os.access(f, os.X_OK):
            subprocess.check_call(['sh', '-c', f])


@hooks.hook('config_changed')
def reset_the_world():
    config = hookenv.config()
    install_packages()

    # Retrieve requirements from client units.
    for relid, relunit in reltype_units('db-proxy'):
        relinfo = hookenv.relation_get(unit=relunit, rid=relid)
        database = client_relinfo.get('database', service_name(relunit))
        roles = set(client_relinfo.get('roles', '').split(','))
        break  # All units should agree, eventually.

    # Discover server units, informing them of client requirements.
    servers = set()
    for relid, relunit in reltype_units('db-admin-backend'):
        # Inform the server unit of the client requirements.
        hookenv.relation_set(relid, database=database, roles=roles)
        servers.add(relunit)

    # Discover ready server units and their roles.
    master = None
    master_state = None
    master_relinfo = None
    for relid, relunit in reltype_units('db-admin-backend'):
        relinfo = hookenv.relation_get(unit=relunit, rid=relid)

        # If the backend is not yet ready to talk to us, skip it.
        allowed_units = set(relinfo.get('allowed-units', '').split())
        if hookenv.local_unit() not in allowed_units:
            continue
        if relinfo.get('database', None) != database:
            continue

        state = relinfo.get('state', None)
        if ((len(servers) == 1 and state == 'standalone')
            or (len(servers) > 1 and state == 'master')):
            master = relunit
            master_state = state
            master_relinfo = relinfo
        elif len(servers) > 1 and state == 'hot standby':
            standbys.add(relunit)
        else:
            continue

    # Inform the clients of connection details to reach the master.
    for relid in hookenv.relation_ids('db-proxy'):
        allowed_units = ' '.join(hookenv.related_units(relid))
        if master is not None:
            hookenv.relation_set(relid,
                                host=hookenv.unit_private_ip(),
                                database=database,
                                port=config['port'],
                                user=master_relinfo['user'],
                                password=master_relinfo['password'],
                                state=master_state
                                allowed_units=allowed_units)

    open_ports(config)
    if host.service_running(SERVICE_NAME):
        host.service_reload(SERVICE_NAME)

    config.save()


def reltype_units(reltype):
    for relid in hookenv.relation_ids(reltype):
        for relunit in hookenv.related_units(relid):
            yield relid, relunit


def install_packages():
    packages = ['pgbouncer']
    packages = fetch.filter_installed_packages(packages)
    if packages:
        fetch.apt_install(packages, fatal=True)


def open_ports(config):
    if config.changed(port):
        if config.previous(port) is not None:
            hookenv.close_port(config.previous(port))
        hookenv.open_port(config['port'])


if __name__ == '__main__':
    log(INFO, "Running {} hook, relid {}, remote unit {}".format(
        os.path.basename(sys.argv[0]),
        hookenv.relation_id(), hookenv.remote_unit())
    hooks.execute(sys.argv)
