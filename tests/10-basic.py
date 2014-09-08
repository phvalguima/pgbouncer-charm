#!/usr/bin/python3

import json
import os
import signal
import socket
import subprocess
import time

import amulet
import psycopg2


def run(unit, cmd):
    # Due to Bug #133143, we can't use sentries. But no need for
    # sentries any more now that we have 'juju run'.
    output = subprocess.check_output(['juju', 'run', '--unit', unit, cmd])
    return output.decode('UTF-8').strip()


class Tunnel:
    def __init__(self, unit, relinfo):
        self.unit = unit
        self.relinfo = relinfo
        self.local_port = None
        self._proc = None

    def __enter__(self):
        # Choose a local port for our tunnel.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("", 0))
        self.local_port = s.getsockname()[1]
        s.close()

        # Open the tunnel and wait for it to come up.
        # The new process group is to ensure we can reap all the ssh
        # tunnels, as simply killing the 'juju ssh' process doesn't seem
        # to be enough.
        tunnel_cmd = [
            'juju', 'ssh', self.unit, '-N', '-L',
            '{}:{}:{}'.format(
                self.local_port, self.relinfo['host'], self.relinfo['port'])]
        self._proc = subprocess.Popen(
            tunnel_cmd, stdin=subprocess.PIPE, preexec_fn=os.setpgrp)
            # Don't disable stdout, so we can see when there are SSH
            # failures like bad host keys.
            #stdout=open('/dev/null', 'ab'), stderr=subprocess.STDOUT)
        self._proc.stdin.close()

        timeout = time.time() + 60
        while True:
            time.sleep(1)
            assert self._proc.poll() is None, 'Tunnel died {!r}'.format(
                self._proc.stdout)
            try:
                socket.create_connection(
                    ('localhost', self.local_port)).close()
                break
            except socket.error:
                if time.time() > timeout:
                    # Its not going to work. Per Bug #802117, this
                    # is likely an invalid host key forcing
                    # tunnelling to be disabled.
                    raise
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._proc:
            os.killpg(self._proc.pid, signal.SIGTERM)
            self._proc.wait()
        return False

    def connect(self):
        con = psycopg2.connect(
            database=self.relinfo['database'],
            port=self.local_port,
            host='localhost',
            user=self.relinfo['user'],
            password=self.relinfo['password'])
        con.autocommit = True
        return con


d = amulet.Deployment(sentries=False)  # No sentries due to Bug #133143.
d.add('postgresql')
d.add('pgbouncer')
d.add('psql', 'postgresql-psql')
d.relate('postgresql:db-admin', 'pgbouncer:backend-db-admin')
d.relate('psql:db', 'pgbouncer:db-proxy')

try:
    d.setup(timeout=900)
except amulet.helpers.TimeoutError:
    amulet.raise_status(amulet.SKIP, msg="Environment was not setup in time")
except:
    raise

relid = run('psql/0', 'relation-ids db')
relinfo = json.loads(run(
    'psql/0',
    'relation-get --format=json -r {} - pgbouncer/0'.format(relid)))

if 'psql/0' not in relinfo['allowed-units']:
    print("client not found in allowed-units")
    amulet.raise_status(amulet.FAIL)

if relinfo['database'] != 'psql':
    print("Incorrect database advertised: {}".format(relinfo['database']))
    amulet.raise_status(amulet.FAIL)

with Tunnel('psql/0', relinfo) as tunnel:
    con = tunnel.connect()
    cur = con.cursor()
    cur.execute('SELECT current_database()')
    database = cur.fetchone()[0]

    if database != 'psql':
        print("Connected to incorrect database: {}".format(database))
        amulet.raise_status(amulet.FAIL)

# Reconfigure the psql service to request an explicit database by name
# and roles assigned to the generated user.
subprocess.check_call([
    'juju', 'set', 'psql', 'database=explicitdb', 'roles=a_role'])

# Per Bug #1200267 we have no way of telling when the relation has been
# setup. Instead, we loop for a while until we get the result we expect,
# and fail if we timeout and give up.
start = time.time()
while True:
    time.sleep(1)
    now = time.time()
    try:
        relid = run('psql/0', 'relation-ids db')
        relinfo = json.loads(run(
            'psql/0',
            'relation-get --format=json -r {} - pgbouncer/0'.format(relid)))
        if now > start + 120:
            break
        if ('psql/0' in relinfo['allowed-units']
                and relinfo['database'] == 'explicitdb'):
            break
    except Exception:
        if now > start + 120:
            raise

if 'psql/0' not in relinfo['allowed-units']:
    print("client not found in allowed-units")
    amulet.raise_status(amulet.FAIL)

if relinfo['database'] != 'explicitdb':
    print("Incorrect database advertised: {}".format(relinfo['database']))
    amulet.raise_status(amulet.FAIL)

with Tunnel('psql/0', relinfo) as tunnel:
    con = tunnel.connect()
    cur = con.cursor()
    cur.execute('SELECT current_database()')
    database = cur.fetchone()[0]

    if database != 'explicitdb':
        print("Connected to incorrect database: {}".format(database))
        amulet.raise_status(amulet.FAIL)

    cur.execute("SELECT pg_has_role('a_role', 'MEMBER')")
    has_role = cur.fetchone()[0]

    if not has_role:
        print("Not granted role")
        amulet.raise_status(amulet.FAIL)

amulet.raise_status(amulet.PASS)
