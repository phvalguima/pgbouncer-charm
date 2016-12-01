#!/usr/bin/python3

from functools import partial, wraps
import json
import os
import unittest

import amulet
import psycopg2


SERIES = os.environ.get('SERIES', 'xenial')


def for_every_database(test_method,
                       relnames=('db', 'db-admin'),
                       roles=('master', 'standby')):
    @wraps(test_method)
    def _wrap(self):
        for unit in self.conn_str.keys():
            for relname in relnames:
                for role in roles:
                    with self.subTest(unit=unit,
                                      relname=relname, role=role):
                        test_method(self, self.connect(role, relname, unit))
    return _wrap


for_every_master = partial(for_every_database, roles=('master',))
for_every_standby = partial(for_every_database, roles=('standby',))
for_every_admin = partial(for_every_database, relnames=('db-admin',))
for_every_nonadmin = partial(for_every_database, relnames=('db',))


class TestDeployment(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        d = amulet.Deployment(series=SERIES)
        cls.d = d
        d.add('postgresql', units=2)
        d.add('pgbouncer', units=2)
        d.add('psql', 'cs:~postgresql-charmers/postgresql-client')

        d.relate('postgresql:db-admin', 'pgbouncer:backend-db-admin')
        d.relate('psql:db', 'pgbouncer:db')
        d.relate('psql:db', 'pgbouncer:db-admin')

        d.expose('pgbouncer')

        d.setup(timeout=900)
        cls.reset_config()

    @classmethod
    def reset_config(cls):
        cls.d.configure('psql', dict(roles='', database='cli', extensions=''))
        cls.d.configure('pgbouncer', dict(listen_port=6432))
        cls.d.sentry.wait(timeout=300)
        TestDeployment.collect_conn_str()

    @classmethod
    def collect_conn_str(cls):
        # Extract connection strings from the relation
        # conn_str['pgbouncer/0']['db-admin']['standby'] == libpq conn str
        conn_str = {}
        for pgbouncer in cls.d.sentry['pgbouncer']:
            unit = pgbouncer.info['unit_name']
            conn_str[unit] = {}
            for relname in ['db', 'db-admin']:
                # Alas, the relation() helper fails with multiple
                # relations between applications. Its fixable, but too
                # tricky to tackle now.
                # rel = pgbouncer.relation(relname, 'psql:db')
                rid, rc = pgbouncer._run('relation-ids {}'
                                         ''.format(relname))
                assert rc == 0, 'relation-ids failed'
                rid = rid.strip()
                raw, rc = pgbouncer._run('relation-get --format=json '
                                         '-r {} - {}'.format(rid, unit))
                assert rc == 0, 'relation-get failed'
                rel = json.loads(raw)

                master = rel.get('master')
                # Technically, standbys is a newline separated list of
                # connection strings but pgbouncer only ever advertises
                # a single endpoint (multiple standbys will be load balanced
                # using haproxy).
                standby = rel.get('standbys')
                conn_str[unit][relname] = dict(master=master, standby=standby)
        cls.conn_str = conn_str

    _needs_cleanup = False

    def tearDown(self):
        if self._needs_cleanup:
            TestDeployment.reset_config()
            self._needs_cleanup = False

    def configure(self, appname, **kw):
        self.d.configure(appname, kw)
        self.d.sentry.wait(timeout=300)
        TestDeployment.collect_conn_str()
        self._needs_cleanup = True

    def connect(self, role, relname='db', pgbouncer=None):
        if pgbouncer is None:
            pgbouncer = list(self.conn_str.keys())[0]
        return psycopg2.connect(self.conn_str[pgbouncer][relname][role])

    @for_every_database
    def test_dbname(self, con):
        cur = con.cursor()
        cur.execute('SELECT current_database()')
        dbname = cur.fetchone()[0]
        self.assertEqual(dbname, 'cli')

    @for_every_master
    def test_is_master(self, con):
        cur = con.cursor()
        cur.execute('SELECT pg_is_in_recovery()')
        recovery = cur.fetchone()[0]
        self.assertFalse(recovery)

    @for_every_standby
    def test_is_standby(self, con):
        cur = con.cursor()
        cur.execute('SELECT pg_is_in_recovery()')
        recovery = cur.fetchone()[0]
        self.assertTrue(recovery)

    @for_every_admin
    def test_is_admin(self, con):
        cur = con.cursor()
        cur.execute('SELECT rolsuper from pg_roles where rolname=session_user')
        self.assertTrue(cur.fetchone()[0])

    @for_every_nonadmin
    def test_is_not_admin(self, con):
        cur = con.cursor()
        cur.execute('SELECT rolsuper from pg_roles where rolname=session_user')
        self.assertFalse(cur.fetchone()[0])

    def test_client_config(self):
        self.configure('psql',
                       roles='fred', database='newdb', extensions='unaccent')
        con = self.connect('master')
        cur = con.cursor()
        cur.execute("SELECT unaccent('foo')")
        cur.execute("SELECT pg_has_role('fred', 'MEMBER')")
        self.assertTrue(cur.fetchone()[0])
        cur.execute("SELECT current_database()")
        self.assertEqual(cur.fetchone()[0], 'newdb')

    def test_pgbouncer_config(self):
        unit = list(self.conn_str.keys())[0]
        self.assertNotIn('7777', self.conn_str[unit]['db']['master'])

        self.configure('pgbouncer', listen_port=7777)

        # The connection string changed
        unit = list(self.conn_str.keys())[0]
        self.assertIn('7777', self.conn_str[unit]['db']['master'])

        # The db is contactable, meaning pgbouncer was restarted
        self.connect('master')


if __name__ == '__main__':
    unittest.main()
