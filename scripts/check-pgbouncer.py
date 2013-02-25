#!/usr/bin/python

# Copyright 2012 Canonical Ltd. All rights reserved.
# Author: Liam Young <liam.young@canonical.com>


from collections import defaultdict
from optparse import OptionParser
import psycopg2
import psycopg2.extras


def connect(database, user, password, host, port):
    """Connect to the database, returning the DB-API connection."""
    if not database:
        database = "pgbouncer"
    if not user:
        user = "nagios"
    if not host:
        host = "127.0.0.1"
    if not port:
        port = "5434"
    if not password:
        return psycopg2.connect("dbname=%s user=%s host=%s port=% \
                                 sslmode=require"
                                % (database, user, host, port))
    else:
        return psycopg2.connect("dbname=%s user=%s password=%s host=%s \
                                 port=% sslmode=require"
                                % (database, user, password, host, port))


def get_list_values(db_connection):
    db_connection.set_isolation_level(0)
    cur = db_connection.cursor()
    cur.execute("""SHOW LISTS""")
    rows = cur.fetchall()
    cur.close()
    return dict((str(key).rstrip("\x00"), value) for key, value in rows)


def get_config_values(db_connection):
    db_connection.set_isolation_level(0)
    cur = db_connection.cursor()
    cur.execute("""SHOW CONFIG""")
    rows = cur.fetchall()
    cur.close()
    return dict((str(key).rstrip("\x00"), str(value).rstrip("\x00")) for key, value, changeable in rows)


def get_pool_stats(db_connection):
    db_connection.set_isolation_level(0)
    # need this special cursor to get columnname info
    cur = db_connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SHOW POOLS""")
    rows = cur.fetchall()
    cur.close()
    sum_keys = frozenset([
        'cl_active', 'cl_waiting', 'sv_active', 'sv_idle', 'sv_used',
        'sv_tested', 'sv_login'])
    summary = defaultdict(int)
    for row in rows:
        for key in sum_keys:
            summary[key] += int(row[key])
        summary['maxwait'] = max(summary['maxwait'], row['maxwait'])
    return summary


def check_pool_wait(db_connection, warnlevel, critlevel):
    stats_dict = get_pool_stats(db_connection)
    cl_waiting = stats_dict['cl_waiting']
    maxwait = stats_dict['maxwait']
    status_message = ("Max seconds waiting for an available backend (maxwait): %d, "
                      "FYI number of clients waiting for a backend (cl_waiting): %d"
                      ) % (maxwait, cl_waiting)

    # check if we have a client conns waiting for more than maxwait secs for backend:
    if maxwait < warnlevel:
        print "OK: " + status_message
        return 0
    elif maxwait < critlevel:
        print "WARNING: " + status_message
        return 1
    else:
        print "CRITICAL: " + status_message
        return 2


def check_max_conns(db_connection, warn_pct, critical_pct):
    db_connection.set_isolation_level(0)
    show_config = get_config_values(db_connection)
    show_lists = get_list_values(db_connection)
    max_connections = int(show_config["max_client_conn"])
    current_count = int(show_lists["used_clients"])
    warn_limit = ((warn_pct * max_connections) / 100)
    critical_limit = ((critical_pct * max_connections) / 100)
    status_message = "Current connections: %s Maximum connections: %s" % (current_count, max_connections)
    if current_count < warn_limit:
        print "OK: " + status_message
        return 0
    elif current_count < critical_limit:
        print "WARNING: " + status_message
        return 1
    else:
        print "CRITICAL: " + status_message
        return 2


class NagiosOptionParser(OptionParser):
    def error(self, msg):
        print 'ERROR: %s' % msg
        raise SystemExit(3)  # Code for Unknown


if __name__ == '__main__':
    parser = NagiosOptionParser()
    parser.add_option("-d", "--database", dest="database")
    parser.add_option("-u", "--user", dest="user")
    parser.add_option("-p", "--password", dest="password")
    parser.add_option("-H", "--host", dest="host")
    parser.add_option("-P", "--port", dest="port")
    parser.add_option("-C", "--checkname", dest="checkname",
                      help=("'check_max_conns': current and max client connections; "
                            "'check_pool_wait': max time in secs waited for clients until pgbouncer "
                            "finds an available backend, also instant number of clients waiting is shown "
                            "in the status message"))
    parser.add_option("-w", "--warnlevel", dest="warnlevel", type=int)
    parser.add_option("-c", "--critlevel", dest="critlevel", type=int)

    (options, args) = parser.parse_args()

    if (options.warnlevel is None or options.critlevel is None
            or options.checkname is None):
        parser.error("--warnlevel, --critlevel and --checkname are required")

    conn = None
    exit_code = 3
    try:
        conn = connect(options.database, options.user, options.password,
                       options.host, options.port)
        if options.checkname == "check_max_conns":
            exit_code = check_max_conns(
                conn, int(options.warnlevel), int(options.critlevel))
        elif options.checkname == "check_pool_wait":
            exit_code = check_pool_wait(
                conn, int(options.warnlevel), int(options.critlevel))
        else:
            parser.error("Invalid --checkname %s" % repr(options.checkname))
    except psycopg2.Error, exception:
        error_msg = str(exception)
        print "ERROR: %s" % error_msg
        exit_code = 2

    if conn is not None:
        conn.close()
    raise SystemExit(exit_code)
