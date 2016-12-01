# Copyright 2012-2016 Canonical Ltd. All rights reserved.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import csv
from io import StringIO
import os.path
from textwrap import dedent

from charmhelpers import context
from charmhelpers.core import hookenv, host
from charmhelpers.core.hookenv import log, INFO
from charms import reactive, leadership
from charms.reactive import hook, when, when_not, not_unless

import jinja2
import psycopg2
from psycopg2.extensions import AsIs

from relations.pgsql.requires import ConnectionString, ConnectionStrings


SERVICE_NAME = 'pgbouncer'

CLIENT_RELNAME = 'db-proxy'


@when('apt.installed.pgbouncer')
def bootstrap():
    reactive.set_state('pgbouncer.enabled')


@hook('stop')
def stop():
    reactive.remove_state('pgbouncer.enabled')


@when('pgbouncer.enabled')
@when_not('backend-db-admin.connected')
def blocked():
    hookenv.status_set('blocked', 'Backend relation required')


@when('pgbouncer.enabled')
@when('backend-db-admin.connected')
@when_not('backend-db-admin.master.available')
def waiting(backend):
    hookenv.status_set('waiting', 'Waiting for backend relation')


@when('pgbouncer.enabled')
@when('backend-db-admin.master.available')
@when_not('pgbouncer.service_resumed')
def enable(backend):
    if host.service_resume(SERVICE_NAME):
        reactive.set_state('pgbouncer.service_resumed')
    else:
        hookenv.status_set('blocked', 'Failed to start')


@when_not('pgbouncer.enabled')
@when('apt.installed.pgbouncer')
@when('pgbouncer.service_resumed')
def disable():
    hookenv.status_set('maintenance', 'Disabling')
    host.service_pause(SERVICE_NAME)
    hookenv.status_set('maintenance', 'Disabled')
    reactive.remove_state('pgbouncer.service_resumed')


@when('pgbouncer.needs_restart')
def restart():
    hookenv.status_set('maintenance', 'Restarting')
    hookenv.log('Resarting pgbouncer')
    if host.service_restart(SERVICE_NAME):
        hookenv.status_set('active', 'Active')
        reactive.remove_state('pgbouncer.needs_reload')
        reactive.remove_state('pgbouncer.needs_restart')
    else:
        hookenv.status_set('blocked', 'Failed to restart daemon')


@when('pgbouncer.needs_reload')
@when_not('pgbouncer.needs_restart')
def reload():
    config = hookenv.config()
    if reactive.helpers.data_changed('pgbouncer.restart',
                                     [config['listen_port'],
                                      hookenv.unit_private_ip()]):
        hookenv.log('pgbouncer restart required')
        reactive.set_state('pgbouncer.needs_restart')
    elif host.service_reload(SERVICE_NAME):
        reactive.remove_state('pgbouncer.needs_reload')
        hookenv.status_set('active', 'Active')
    else:
        hookenv.status_set('blocked', 'Failed to reload daemon')


@when('pgbouncer.enabled')
@when('backend-db-admin.master.available')
def configure(backend):
    config = hookenv.config()

    relations = context.Relations()

    backend = get_backend()

    con = connect()
    if con is None:
        return

    dbnames = set()
    for relname in ['db', 'db-admin']:
        for relid, relation in relations[relname].items():
            for client_unit, client_relinfo in relation.items():
                uname = get_username(relid, client_unit)
                pw = get_password(uname)
                if pw is None:
                    break  # Leader hasn't published the password yet
                roles = set(role.strip()
                            for role in client_relinfo.get('roles',
                                                           '').split(',')
                            if role.strip())
                dbname = (client_relinfo.get('database', '').strip() or
                          get_dbname(client_unit))
                extensions = set(ext.strip()
                                 for ext in client_relinfo.get('extensions',
                                                               '').split(',')
                                 if ext.strip())

                if hookenv.is_leader():
                    ensure_user(con, uname, roles, relname == 'db-admin')
                    ensure_database(con, uname, dbname)
                    ensure_extensions(dbname, extensions)

                dbnames.add(dbname)

                relation.local['version'] = backend.version

                # Send the clients their connection details, starting
                # with the 'old' v1 protocol. The lead pgbouncer unit will
                # advertise as the master, and any remaining pgbouncer
                # units will advertise as a standby.
                relation.local['host'] = hookenv.unit_private_ip()
                relation.local['database'] = dbname
                relation.local['port'] = str(config['listen_port'])
                relation.local['user'] = uname
                relation.local['roles'] = client_relinfo.get('roles')
                relation.local['password'] = pw
                relation.local['state'] = ('master' if hookenv.is_leader()
                                           else 'standby')
                relation.local['allowed-units'] = ' '.join(
                    sorted(relation.keys()))

                # Next, 'new' v2 protocol, where each unit advertises
                # the master and all standby connection strings. Clients
                # should use these connection strings rather than construct
                # their own from host, database, port etc. If they don't,
                # they will only be able to connect to a single backend unit
                # through this pgbouncer unit.
                c = dict(host=relation.local['host'],
                         dbname=relation.local['database'],
                         port=relation.local['port'],
                         user=relation.local['user'],
                         password=relation.local['password'])
                relation.local['master'] = ConnectionString(**c)
                # pgbouncer presents a single standby. In the future,
                # we should load balance this single standby over all
                # available backends using HAProxy or similar.
                c['dbname'] = '{}_standby'.format(c['dbname'])
                relation.local['standbys'] = '\n'.join([ConnectionString(**c)])

                break  # One client only. They will agree eventually.

    # We have everything we need. Generate a valid pgbouncer
    # configuration.
    generate_pgbouncer_config(dbnames)


@when('apt.installed.pgbouncer')
def ensure_admin_passwords():
    users = ['root', 'postgres', 'ubuntu', 'pgbouncer', 'nagios']
    for user in users:
        pw = get_password(user)  # Ensure password exists in userlist.txt
        home = os.path.expanduser('~{}'.format(user))
        if os.path.isdir(home):
            contents = "# This file maintained by Juju\n*:*:*:{}:{}".format(
                user, pw)
            host.write_file(
                os.path.join(home, '.pgpass'), contents.encode(),
                user, user, 0o600)


@when('config.changed.listen_port')
def ensure_console_shortcut():
    """Generate a small script to connect to the pgbouncer console."""
    config = hookenv.config()
    contents = dedent("""\
                      #!/bin/sh
                      export LC_ALL=en_US.UTF-8
                      exec psql -h localhost -p {} pgbouncer
                      """).format(config['listen_port'])
    host.write_file('/usr/local/bin/pgbouncer-cli',
                    contents.encode(), perms=0o555)
    reactive.set_state('pgbouncer.cli.created')


@when('config.changed.listen_port')
def open_ports():
    key = 'listen_port'
    config = hookenv.config()
    if config.previous(key) is not None:
        hookenv.close_port(config.previous(key))
    hookenv.open_port(config[key])


def generate_pgbouncer_config(databases):
    loader = jinja2.FileSystemLoader(
        os.path.join(hookenv.charm_dir(), 'templates'))
    env = jinja2.Environment(loader=loader)
    env.globals['config'] = hookenv.config()
    env.globals['listen_addr'] = hookenv.unit_private_ip()

    def pgbouncer_quote(x):
        return x.replace('"', '""')

    backend = get_backend()

    database_stanzas = set()

    def _bouncer_cs(cs, dbname):
        # Convert backend relation ConnectionString to pgbouncer
        # backend connection details. username & password stripped,
        # since the client supplies these, and dbname is forced.
        return ConnectionString(cs, user=None, password=None, dbname=dbname)

    # Database section for the master or standalone database.
    for dbname in databases:
        if backend.master:
            database_stanzas.add("{} = {}".format(
                pgbouncer_quote(dbname),
                _bouncer_cs(backend.master, dbname)))
        for standby in backend.standbys:
            database_stanzas.add("{}_standby = {}".format(
                pgbouncer_quote(dbname),
                _bouncer_cs(standby, dbname)))
            break

    # Regenerate /etc/pgbouncer/pgbouncer.ini
    template = env.get_template('pgbouncer.ini.tmpl')
    contents = template.render(database_stanzas=database_stanzas)
    config_path = '/etc/pgbouncer/pgbouncer.ini'

    if contents.encode() != open(config_path, 'rb').read():
        hookenv.log('Updating pgbouncer.ini')
        host.write_file('/etc/pgbouncer/pgbouncer.ini', contents.encode())
        reactive.set_state('pgbouncer.needs_reload')

    # Regenerate /etc/default/pgbouncer.
    # contents = dedent("""\
    #                   START=1
    #                   ulimit -n 65536
    #                   """)
    # host.write_file('/etc/default/pgbouncer', contents)


@not_unless('backend-db-admin.master.available')
def connect(dbname='postgres'):
    c = dict(get_backend().master)
    c['dbname'] = dbname
    try:
        con = psycopg2.connect(ConnectionString(**c))
    except psycopg2.OperationalError:
        # Even though our reactive states are set, they may lag behind
        # reality. The PostgreSQL backend may have already run its
        # -departed hook and revoked access, before this units
        # backend-db-admin-relation-departed hook has had a chance to
        # run.
        reactive.remove_state('backend-db-admin.master.available')
        return None
    con.autocommit = True
    return con


@not_unless('backend-db-admin.master.available')
def get_backend():
    '''Return the :class:`ConnectionStrings` to the backend databases'''
    for relid in context.Relations()['backend-db-admin'].keys():
        return ConnectionStrings(relid)


def get_username(relid, unit, schema=False):
    """Generate the same username as the PostgreSQL charm would.

    We want the same username so that if the proxy is removed and the
    client connected directly to PostgreSQL, then database permissions
    remain valid.
    """
    # Note that clients only have access via the generated username, and
    # that the generated username can never match the ones with
    # pgbouncer administrative access ('postgres', 'pgbouncer', 'ubuntu',
    # 'root', 'nagios').
    components = [sanitize(relid), sanitize(unit.split('/', 1)[0])]
    if schema:
        components.append('schema')
    return '_'.join(components)


def get_dbname(unit):
    """Generate the same database name as the PostgreSQL charm would."""
    return sanitize(unit.split('/')[0])


def get_password(username):
    """Return the password for a user from the userlist.txt.

    The password will be generated and stored in userlist.txt if it
    does not already exist.
    """
    # userlist.txt is trivially parsed and regenerated using Python's
    # csv module.
    csv_dialect = dict(delimiter=' ', doublequote=True, quoting=csv.QUOTE_ALL)

    # Master copy is stored in leadership settings.
    userlist = leadership.leader_get('userlist')
    if userlist:
        passwords = dict(csv.reader(userlist.splitlines(), **csv_dialect))
    else:
        passwords = {}

    if username not in passwords and hookenv.is_leader():
        passwords[username] = host.pwgen()
        s = StringIO()
        csv.writer(s, **csv_dialect).writerows(passwords.items())
        leadership.leader_set(userlist=s.getvalue())

    return passwords.get(username)


@when('apt.installed.pgbouncer')
@when_not('leadership.set.userlist')
def initialize_userlist():
    '''Ensure userlist.txt exists to keep the pgbouncer daemon happy.'''
    userlist = '/etc/pgbouncer/userlist.txt'
    host.write_file(userlist, ''.encode(), 'postgres', 'postgres', 0o400)


@when('apt.installed.pgbouncer')
@when('leadership.changed.userlist')
def sync_userlist():
    userlist = '/etc/pgbouncer/userlist.txt'
    host.write_file(userlist, leadership.leader_get('userlist').encode(),
                    'postgres', 'postgres', 0o400)


def ensure_database(con, user, database):
    cur = con.cursor()
    try:
        cur.execute(
            "SELECT datname FROM pg_database WHERE datname = %s", (database,))
        if not cur.fetchone():
            log("Creating database {}".format(database), INFO)
            cur.execute("CREATE DATABASE %s", (pgidentifier(database),))
        cur.execute(
            "GRANT CONNECT ON DATABASE %s TO %s",
            (pgidentifier(database), pgidentifier(user)))
    except psycopg2.IntegrityError:
        # Race with another unit. DB already created.
        pass


def ensure_user(con, user, roles, admin):
    cur = con.cursor()
    if not role_exists(con, user):
        if admin:
            log("Creating superuser {}".format(user), INFO)
            cur.execute("CREATE ROLE %s WITH SUPERUSER LOGIN PASSWORD %s",
                        (pgidentifier(user), get_password(user)))
        else:
            log("Creating user {}".format(user), INFO)
            cur.execute("CREATE ROLE %s WITH LOGIN PASSWORD %s",
                        (pgidentifier(user), get_password(user)))

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
                        (pgidentifier(role),))
        log("Granting {} to {}".format(role, user), INFO)
        cur.execute(
            "GRANT %s TO %s",
            (pgidentifier(role), pgidentifier(user)))

    for role in roles_to_revoke:
        log("Revoking {} from {}".format(role, user), INFO)
        cur.execute(
            "REVOKE %s FROM %s",
            (pgidentifier(role), pgidentifier(user)))


def role_exists(con, role):
    cur = con.cursor()
    cur.execute("SELECT rolname FROM pg_roles WHERE rolname = %s", (role,))
    return cur.fetchone() is not None


def ensure_extensions(dbname, extensions):
    if extensions:
        con = connect(dbname)
        if con is None:
            return
        cur = con.cursor()
        for ext in extensions:
            cur.execute('CREATE EXTENSION IF NOT EXISTS %s',
                        (pgidentifier(ext),))


def sanitize(s):
    s = s.replace(':', '_')
    s = s.replace('-', '_')
    s = s.replace('/', '_')
    s = s.replace('"', '_')
    s = s.replace("'", '_')
    return s


def pgidentifier(token):
    '''Wrap a string for interpolation by psycopg2 as an SQL identifier'''
    return AsIs(quote_identifier(token))


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
        identifier.encode('ascii')
        return '"{}"'.format(identifier.replace('"', '""'))
    except UnicodeEncodeError:
        escaped = []
        for c in identifier:
            if c == '\\':
                escaped.append(b'\\\\')
            elif c == '"':
                escaped.append(b'""')
            else:
                c = c.encode('ascii', 'backslashreplace')
                # Note Python only supports 32 bit unicode, so we use
                # the 4 hexdigit PostgreSQL syntax (\1234) rather than
                # the 6 hexdigit format (\+123456).
                if c.startswith(b'\\u'):
                    c = b'\\' + c[2:]
                escaped.append(c)
        return 'U&"{}"'.format(''.join(s.decode('ascii') for s in escaped))
