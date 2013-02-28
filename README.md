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

## Example

First, if you haven't already done so, bootstrap your environment:

    juju bootstrap

Now deploy a standalone PostgreSQL instance:

    juju deploy postgresql

Let's add another unit:

    juju add-unit postgresql

As per the documentation in the postgresql charm, you now have a master
and a hot standby set up with replication.

Now that you have a functional PostgreSQL setup, deploy the 
PgBouncer charm from the Juju charm store:

    juju deploy pgbouncer

Create the relations between pgbouncer and postgresql:
    juju add-relation pgbouncer:backend-db-admin postgresql:db-admin

Now you have set up pgbouncer in front of your PostgreSQL units.

In a real world scenario, you might have a (web) application that
sends write queries to a master (directly) and read-only queries
to a cluster of slaves, with load balancing using pgbouncer.


## Configuration

    juju set pgbouncer max_client_conn=50

## db-proxy relationship

The charm joining the db-proxy relationship can specify a database that
will be created in addition to the default one based on the service name
and can specify a comma seperated list of roles that will be granted to
the user (these roles will be created if they do not already exist)

## Monitoring

This charm provides relations that support monitoring via Nagios using 
nrpe_external_master as a subordinate charm.

