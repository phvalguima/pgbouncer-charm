#!/usr/bin/python
# Copyright 2014 Canonical Ltd. All rights reserved.

import csv
from cStringIO import StringIO
import glob
import os.path
import subprocess
import sys
from textwrap import dedent

from charmhelpers import fetch
from charmhelpers.core import hookenv, host
from charmhelpers.core.hookenv import log, INFO

try:
    import jinja2
    import psycopg2
except ImportError:
    fetch.apt_install(['python-jinja2', 'python-psycopg2'], fatal=True)
    import jinja2
    import psycopg2

from psycopg2.extensions import AsIs


hooks = hookenv.Hooks()

SERVICE_NAME = 'pgbouncer'

CLIENT_RELNAME = 'db-proxy'
BACKEND_RELNAME = 'backend-db-admin'


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
    'config-changed',
    'db-proxy-relation-changed',
    'db-proxy-relation-departed',
    'backend-db-admin-relation-changed',
    'backend-db-admin-relation-departed')
def reset_the_world():
    config = hookenv.config()
    install_packages()

    ensure_admin_passwords()

    master, standbys = discover_backends()

    # Gather requirements from the client relations. Not all units in a
    # relation may agree, but they should eventually so there is no need
    # to merge inconsistencies.
    client_users = {}
    client_roles = {}
    client_databases = {}
    for relid, relunit, relinfo in relname_units(CLIENT_RELNAME):
        client_users[relid] = username(relid, relunit)
        client_roles[relid] = (
            client_roles.get(relid, None)
            or set(role.strip()
                   for role in relinfo.get('roles', '').split(',')
                   if role.strip()))
        client_databases[relid] = (client_databases.get(relid, None)
                                   or relinfo.get('database', '').strip()
                                   or dbname(relunit))

    # We have everything we need. Generate a valid pgbouncer
    # configuration.
    regenerate_pgbouncer_config(config, set(client_databases.values()),
                                master, standbys)
    open_ports(config)
    if host.service_running(SERVICE_NAME):
        host.service_reload(SERVICE_NAME)

    # We have done as much as we can without having a master backend
    # available for creating users, databases etc. If there is no master
    # backend available yet, exit the hook.
    if not master:
        log('Waiting for a master database to be ready', INFO)
        config.save()
        sys.exit(0)

    con = connect()
    for relid in hookenv.relation_ids(CLIENT_RELNAME):
        if len(list(hookenv.related_units(relid))) == 0:
            continue  # No more clients in this relation.
        # Create required database resources, reimplementing a chunk of the
        # PostgreSQL charm.
        ensure_user(con, client_users[relid], client_roles[relid])
        ensure_database(con, client_users[relid], client_databases[relid])
        # Notify the clients.
        allowed_units = ' '.join(hookenv.related_units(relid))
        hookenv.relation_set(relid, {
            'host': hookenv.unit_private_ip(),
            'database': client_databases[relid],
            'port': config['listen_port'],
            'user': client_users[relid],
            'password': password(client_users[relid]),
            'state': 'standalone',
            'allowed-units': allowed_units})
    config.save()


def discover_backends():
    """Return (master, standbys).

    `master` is the relinfo dict from the master unit.
    `standbys` is a set of relinfo dicts from the standby units.

    Backends not yet ready to perform as a master or standby are ignored.
    """
    # Discover server units, ready or not.
    num_backends = len(list(relname_units(BACKEND_RELNAME)))

    # Discover server units that are ready, and their roles.
    master = None
    standbys = set()
    for relid, relunit, relinfo in relname_units(BACKEND_RELNAME):
        # If the backend is not yet ready to talk to us, skip it.
        allowed_units = set(relinfo.get('allowed-units', '').split())
        if hookenv.local_unit() not in allowed_units:
            continue

        state = relinfo.get('state', None)
        # Due to race contitions, it is important to ignore standalone
        # units if there is more than one unit, and master and standby
        # units if there is only one unit.
        if num_backends == 1:
            if state == 'standalone':
                master = relinfo
        else:
            if state == 'master':
                master = relinfo
            elif state == 'hot standby':
                standbys.add(relinfo)

    return master, standbys


def regenerate_pgbouncer_config(config, databases, master, standbys):
    loader = jinja2.FileSystemLoader(
        os.path.join(hookenv.charm_dir(), 'templates'))
    env = jinja2.Environment(loader=loader)
    env.globals['config'] = config

    pgbouncer_quote = lambda x: x.replace('"', '""')

    # Database section for the master or standalone database.
    database_stanzas = set()
    if master:
        for database in databases:
            database_stanzas.add("{} = {}".format(
                pgbouncer_quote(database),
                connstr(host=master['host'],
                        port=master['port'], dbname=database)))

    # TODO: Add a section for the standby databases, via local haproxy.

    # Regenerate /etc/pgbouncer/pgbouncer.ini
    template = env.get_template('pgbouncer.ini.tmpl')
    contents = template.render(database_stanzas=database_stanzas)
    host.write_file('/etc/pgbouncer/pgbouncer.ini', contents)

    # Regenerate /etc/default/pgbouncer.
    contents = dedent("""\
                      START=1
                      ulimit -n 65536
                      """)
    host.write_file('/etc/default/pgbouncer', contents)


def install_packages():
    packages = ['pgbouncer', 'python-cheetah', 'postgresql-client']
    packages = fetch.filter_installed_packages(packages)
    if packages:
        fetch.apt_install(packages, fatal=True)
    ensure_package_status('pgbouncer', hookenv.config('package_status'))


def ensure_package_status(package, status):
    selections = ''.join(['{} {}\n'.format(package, status)])
    dpkg = subprocess.Popen(
        ['dpkg', '--set-selections'], stdin=subprocess.PIPE)
    dpkg.communicate(input=selections)
    if dpkg.returncode != 0:
        log('dpkg --set-selections failed', CRITICAL)
        sys.exit(1)


def open_ports(config):
    key = 'listen_port'
    if config.changed(key):
        if config.previous(key) is not None:
            hookenv.close_port(config.previous(key))
        hookenv.open_port(config[key])


def relname_units(relname):
    for relid in hookenv.relation_ids(relname):
        for relunit in hookenv.related_units(relid):
            yield relid, relunit, hookenv.relation_get(unit=relunit, rid=relid)


def connect():
    master_relinfo = None
    for relid, relunit, relinfo in relname_units(BACKEND_RELNAME):
        state = relinfo.get('state', None)
        if state == 'master':
            master_relinfo = relinfo
            break
        elif state == 'standalone':
            master_relinfo = master_relinfo or relinfo
    con = psycopg2.connect(connstr(
        host=master_relinfo['host'],
        port=master_relinfo['port'],
        user=master_relinfo['user'],
        password=master_relinfo['password'],
        dbname='postgres'))
    con.autocommit = True
    return con


def username(relid, unit, schema=False):
    """Generate the same username as the PostgreSQL charm would."""
    # Note that clients only have access via the generated username, and
    # that the generated username can never match the ones with
    # pgbouncer administrative access ('postgres', 'pgbouncer', 'ubuntu',
    # 'root', 'nagios').
    components = [sanitize(relid), sanitize(unit.split('/', 1)[0])]
    if schema:
        components.append('schema')
    return '_'.join(components)


def dbname(unit):
    """Generate the same database name as the PostgreSQL charm would."""
    return sanitize(unit.split('/')[0])


def password(username):
    """Return the password for a user from the userlist.txt.

    The password will be generated if it does not already exist.
    """
    # userlist.txt is trivially parsed and regenerated using Python's
    # csv module.
    userlist = '/etc/pgbouncer/userlist.txt'
    csv_dialect = dict(delimiter=' ', doublequote=True, quoting=csv.QUOTE_ALL)

    passwords = dict(csv.reader(open(userlist, 'r'), **csv_dialect))

    if username not in passwords:
        passwords[username] = host.pwgen()
        s = StringIO()
        csv.writer(s, **csv_dialect).writerows(passwords.items())
        host.write_file(userlist, s.getvalue(), 'postgres', 'postgres', 0400)

    return passwords[username]


def ensure_database(con, user, database):
    cur = con.cursor()
    cur.execute(
        "SELECT datname FROM pg_database WHERE datname = %s", (database,))
    if not cur.fetchone():
        log("Creating database {}".format(database), INFO)
        cur.execute("CREATE DATABASE %s", (AsIs(quote_identifier(database)),))
    cur.execute(
        "GRANT CONNECT ON DATABASE %s TO %s",
        (AsIs(quote_identifier(database)), AsIs(quote_identifier(user))))


def ensure_user(con, user, roles):
    cur = con.cursor()
    if not role_exists(con, user):
        log("Creating user {}".format(user), INFO)
        cur.execute("CREATE ROLE %s WITH LOGIN PASSWORD %s",
                    (AsIs(quote_identifier(user)), password(user)))

    # Reset the user's roles.
    wanted_roles = set(roles)
    cur.execute("""
        SELECT role.rolname
        FROM pg_roles AS role, pg_roles AS member, pg_auth_members
        WHERE
            member.oid = pg_auth_members.member
            AND role.oid = pg_auth_members.roleid
            AND member.rolname = %s
        """, (user,))
    existing_roles = set(r[0] for r in cur.fetchall())
    roles_to_grant = wanted_roles.difference(existing_roles)
    roles_to_revoke = existing_roles.difference(wanted_roles)

    for role in roles_to_grant:
        if not role_exists(con, role):
            log("Creating role {}".format(role), INFO)
            cur.execute("CREATE ROLE %s INHERIT NOLOGIN",
                        (AsIs(quote_identifier(role)),))
        log("Granting {} to {}".format(role, user), INFO)
        cur.execute(
            "GRANT %s TO %s",
            (AsIs(quote_identifier(role)), AsIs(quote_identifier(user))))

    for role in roles_to_revoke:
        log("Revoking {} from {}".format(role, user), INFO)
        cur.execute(
            "REVOKE %s FROM %s",
            (AsIs(quote_identifier(role)), AsIs(quote_identifier(user))))


def role_exists(con, role):
    cur = con.cursor()
    cur.execute("SELECT rolname FROM pg_roles WHERE rolname = %s", (role,))
    return cur.fetchone() is not None


def connstr(**kw):
    """Return a correctly quoted libpq style connection string."""
    param = lambda k, v: "{}='{}'".format(
        k, str(v).replace('\\', '\\\\').replace("'", "\\'"))
    return ' '.join(param(k, v) for k, v in kw.items())


def sanitize(s):
    s = s.replace(':', '_')
    s = s.replace('-', '_')
    s = s.replace('/', '_')
    s = s.replace('"', '_')
    s = s.replace("'", '_')
    return s


def quote_identifier(identifier):
    r'''Quote an identifier, such as a table or role name.

    In SQL, identifiers are quoted using " rather than ' (which is reserved
    for strings).

    >>> print(quote_identifier('hello'))
    "hello"

    Quotes and Unicode are handled if you make use of them in your
    identifiers.

    >>> print(quote_identifier("'"))
    "'"
    >>> print(quote_identifier('"'))
    """"
    >>> print(quote_identifier("\\"))
    "\"
    >>> print(quote_identifier('\\"'))
    "\"""
    >>> print(quote_identifier('\\ aargh \u0441\u043b\u043e\u043d'))
    U&"\\ aargh \0441\043b\043e\043d"
    '''
    try:
        return '"%s"' % identifier.encode('US-ASCII').replace('"', '""')
    except UnicodeEncodeError:
        escaped = []
        for c in identifier:
            if c == '\\':
                escaped.append('\\\\')
            elif c == '"':
                escaped.append('""')
            else:
                c = c.encode('US-ASCII', 'backslashreplace')
                # Note Python only supports 32 bit unicode, so we use
                # the 4 hexdigit PostgreSQL syntax (\1234) rather than
                # the 6 hexdigit format (\+123456).
                if c.startswith('\\u'):
                    c = '\\' + c[2:]
                escaped.append(c)
        return 'U&"%s"' % ''.join(escaped)


def ensure_admin_passwords():
    users = set('root', 'postgres', 'ubuntu', 'pgbouncer', 'nagios')
    for user in users:
        pw = password(user)  # Ensure password exists in userlist.txt
        home = os.path.expanduser('~{}'.format(user))
        if os.path.isdir(home):
            host.write_file(
                os.path.join(home, '.pgpass'),
                "*:*:*:{}:{}".format(user, pw),
                user, user, 0600)



if __name__ == '__main__':
    log("Running {} hook, relid {}, remote unit {}".format(
        os.path.basename(sys.argv[0]), hookenv.relation_id(),
        os.environ.get('JUJU_REMOTE_UNIT', None)), INFO)
    hooks.execute(sys.argv)
