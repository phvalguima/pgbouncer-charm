# Overview

PgBouncer is a lightweight connection pooler for PostgreSQL.

http://wiki.postgresql.org/wiki/PgBouncer

# Usage

## Installation

To deploy you'll need at a minimum: a cloud environment, a working Juju
installation, and a successful bootstrap. Please refer to the
[Juju Getting Started](https://juju.ubuntu.com/docs/getting-started.html)
documentation before continuing.

It is also recommended that you read the documentation for the
postgresql charm so you understand how to set up postgresql in
a master-slave relationship.

    juju deploy cs:postgresql
    juju deploy cs:~postgresql-charmers/pgbouncer
    juju deploy cs:~postgresql-charmers/postgresql-client psql
    
    juju add-relation postgresql:db-admin pgbouncer:backend-db-admin
    juju add-relation psql:db pgbouncer:db  # Or db-admin


## Charming

The pgbouncer charm implements the same interface as the PostgreSQL charm.
See the [PostgreSQL Client Interface](http://interface-pgsql.readthedocs.io)
for details. Both charms provide the `db` (standard privileges)
and `db-admin` (administrative privileges) relations, and may be used
interchangably.


## Configuration

See `config.yaml` for configuration options. Further details may be
found in the [pgbouncer documentation](https://pgbouncer.github.io/config.html)


## Monitoring

This charm provides relations that support monitoring via Nagios using 
`cs:nrpe_external_master` as a subordinate charm.


# Support

This charm is maintained by [Stuart Bishop](mailto:stuart.bishop@canonical.com)
on [Launchpad](https://launchpad.net/pgbouncer-charm). Please use the main
Juju mailing list for general discussions.

Source is available in git at https://git.launchpad.net/pgbouncer-charm.

Bugs should be reported in the [Launchpad Bugtracker](https://bugs.launchpad.net/pgbouncer-charm).

