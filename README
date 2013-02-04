# Overview

PgBouncer is a lightweight connection pooler for PostgreSQL.


# Installation

To deploy you'll need at a minimum: a cloud environment, a working Juju
installation, and a successful bootstrap. Please refer to the
[Juju Getting Started](https://juju.ubuntu.com/docs/getting-started.html)
documentation before continuing.

Once bootstrapped, deploy the PgBouncer charm from the Juju charm store:

    juju deploy pgbouncer

Now deploy a few instances of PostgreSQL:

    juju deploy postgresql postgresql-1
    juju deploy postgresql postgresql-2
    juju deploy postgresql postgresql-3

Create the relations:

    juju add-relation pgbouncer:backend-db-admin postgresql:db-admin


# Configuration

    juju set pgbouncer max_client_conn=50

# db-proxy relationship

The charm joining the db-proxy relationship can specify a database that
will be created in addition to the default one based on the service name
and can specify a comma seperated list of roles that will be granted to
the user (these roles will be created if they do not already exist)
