#!/usr/bin/python
# Copyright 2014 Canonical Ltd. All rights reserved.

import subprocess
from textwrap import dedent

from charmhelpers import fetch
from charmhelpers.core import hookenv, host
from charmhelpers.core.hookenv import (
    log, CRITICAL, ERROR, WARNING, INFO, DEBUG)


try:
    from jinja2 import Template
except ImportError:
    fetch.apt_install(['python-jinja2'], fatal=True)
    from jinja2 import Template


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


@hooks.hook(
    'config_changed',
    'db-proxy-relation-joined',
    'db-proxy-relation-changed',
    'db-proxy-relation-broken',
    'backend-db-admin-relation-joined',
    'backend-db-admin-relation-changed',
    'backend-db-admin-relation-broken')
def reset_the_world():
    config = hookenv.config()
    install_packages()

    # Retrieve requirements from client units.
    for relid, relunit in reltype_units('db-proxy'):
        relinfo = hookenv.relation_get(unit=relunit, rid=relid)
        # If the client has not provided a database name, we generate
        # the same one that the PostgreSQL charm would choose if the
        # client service was directly connected. This way, the proxy can
        # easily be added and removed between a client and the database.
        database = client_relinfo.get('database', service_name(relunit))
        roles = set(client_relinfo.get('roles', '').split(','))
        break  # All units should agree, eventually. No need to continue.

    # Inform backend relations of client requirements.
    for relid in relation_ids('backend-db-admin'):
        hookenv.relation_set(relid, database=database, roles=roles)

    # Discover server units, ready or not.
    servers = set()
    for relid, relunit in reltype_units('backend-db-admin'):
        servers.add(relunit)

    # Discover server units that are ready, and their roles.
    master = None
    master_state = None
    master_relinfo = None
    standby_relinfos = set()
    for relid, relunit in reltype_units('backend-db-admin'):
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
            standby_relinfos.add(relinfo))
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

    regenerate_pgbouncer_config(config, master_relinfo, standby_relinfos)

    open_ports(config)

    if host.service_running(SERVICE_NAME):
        host.service_reload(SERVICE_NAME)

    config.save()


def regenerate_pgbouncer_config(config, master_relinfo, standbys):
    params = dict(config)
    params['databases'] = set()

    database = master_relinfo['database']

    quote = lambda x: x.replace('"', '""')

    # Database section for the master or standalone database.
    if master_relinfo:
        databases.add("{} = {}".format(
            quote(database),
            connstr(master_relinfo['host'], master_relinfo['port'], database)))

    # TODO: Add a section for the standby databases, via local haproxy.

    # Regenerate /etc/pgbouncer/pgbouncer.ini
    template_file = "{}/templates/pgbouncer.ini".format(hookenv.charm_dir())
    contents = Template(open(template_file).read()).render(config)
    host.write_file('/etc/pgbouncer/pgbouncer.ini', contents)

    # Regenerate /etc/default/pgbouncer.
    contents = dedent("""\
                      START=1
                      ulimit -n 65536
                      """).format(**config)
    host.write_file('/etc/default/pgbouncer', contents)


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


def reltype_units(reltype):
    for relid in hookenv.relation_ids(reltype):
        for relunit in hookenv.related_units(relid):
            yield relid, relunit


def connstr(host, port, dbname):
    param = lambda x: "'{}'".format(
        str(x).replace('\\','\\\\').replace("'","\\'"))
    return "host={} port={} dbname={}".format(
        param(host), param(port), param(dbame))


if __name__ == '__main__':
    log(INFO, "Running {} hook, relid {}, remote unit {}".format(
        os.path.basename(sys.argv[0]),
        hookenv.relation_id(), hookenv.remote_unit())
    hooks.execute(sys.argv)
