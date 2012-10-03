#!/usr/bin/python

# Copyright 2012 Canonical Ltd. All rights reserved.
# Author: Haw Loeung <haw.loeung@canonical.com>

# Script used to generate file using python-cheetah template.

from optparse import OptionParser
from Cheetah.Template import Template
import json
import sys


###############################################################################
# Supporting functions
###############################################################################

#------------------------------------------------------------------------------
# config_get:  Returns a dictionary containing all of the config information
#              Optional parameter: scope
#              scope: limits the scope of the returned configuration to the
#                     desired config item.
#------------------------------------------------------------------------------
def config_get(scope=None):
    try:
        config_cmd_line = ['config-get']
        if scope is not None:
            config_cmd_line.append(scope)
        config_cmd_line.append('--format=json')
        config_data = json.loads(subprocess.check_output(config_cmd_line))
    except Exception, e:
        subprocess.call(['juju-log', str(e)])
        config_data = None
    finally:
        return(config_data)

#------------------------------------------------------------------------------
# relation_json:  Returns json-formatted relation data
#                Optional parameters: scope, relation_id
#                scope:        limits the scope of the returned data to the
#                              desired item.
#                unit_name:    limits the data ( and optionally the scope )
#                              to the specified unit
#                relation_id:  specify relation id for out of context usage.
#------------------------------------------------------------------------------
def relation_json(scope=None, unit_name=None, relation_id=None):
    try:
        relation_cmd_line = ['relation-get', '--format=json']
        if relation_id is not None:
            relation_cmd_line.extend(('-r', relation_id))
        if scope is not None:
            relation_cmd_line.append(scope)
        else:
            relation_cmd_line.append('-')
        relation_cmd_line.append(unit_name)
        relation_data = subprocess.check_output(relation_cmd_line)
    except Exception, e:
        subprocess.call(['juju-log', str(e)])
        relation_data = None
    finally:
        return(relation_data)

#------------------------------------------------------------------------------
# relation_get:  Returns a dictionary containing the relation information
#                Optional parameters: scope, relation_id
#                scope:        limits the scope of the returned data to the
#                              desired item.
#                unit_name:    limits the data ( and optionally the scope )
#                              to the specified unit
#                relation_id:  specify relation id for out of context usage.
#------------------------------------------------------------------------------
def relation_get(scope=None, unit_name=None, relation_id=None):
    try:
        relation_data = json.loads(relation_json())
    except Exception, e:
        subprocess.call(['juju-log', str(e)])
        relation_data = None
    finally:
        return(relation_data)

#------------------------------------------------------------------------------
# relation_ids:  Returns a list of relation ids
#                optional parameters: relation_type
#                relation_type: return relations only of this type
#------------------------------------------------------------------------------
def relation_ids(relation_types=['db-proxy','backend-db-admin']):
    # accept strings or iterators
    if isinstance(relation_types, basestring):
        reltypes = [relation_types,]
    else:
        reltypes = relation_types
    relids = []
    for reltype in reltypes:
        relid_cmd_line = ['relation-ids', '--format=json',reltype]
        relids.extend(json.loads(subprocess.check_output(relid_cmd_line)))
    return relids

#------------------------------------------------------------------------------
# relation_get_all:  Returns a dictionary containing the relation information
#                optional parameters: relation_type
#                relation_type: limits the scope of the returned data to the
#                               desired item.
#------------------------------------------------------------------------------
def relation_get_all():
    reldata = {}
    try:
        relids = relation_ids()
        for relid in relids:
            units_cmd_line = ['relation-list','--format=json','-r',relid]
            units = json.loads(subprocess.check_output(units_cmd_line))
            for unit in units:
                reldata[unit] = json.loads(relation_json(relation_id=relid,unit_name=unit))
                reldata[unit]['relation-id'] = relid
                reldata[unit]['name'] = unit.replace("/","_")
    except Exception, e:
        subprocess.call(['juju-log', str(e)])
        reldata = []
    finally:
        return(reldata)

#------------------------------------------------------------------------------
# template_generate: Generate the cheetah template
#                    template:     Template file
#                    searchlist:   searchlist dictionary of configuration
#------------------------------------------------------------------------------
def template_generate(template, searchlist):
    """ Generate file using specified python-cheetah template file """

    if not template:
        print "ERROR: No template provided."
        return -1

    tmpl = Template(file=template, searchList=searchlist)
    print tmpl


def main():
    parser = OptionParser()
    parser.add_option("-j", "--json", dest="json",
                      help="JSON file to read in for template searchlist")

    (options, args) = parser.parse_args()
    searchlist = dict(json.loads(open(options.json).read()).items() +  relation_get_all().items())

    for template in args:
        template_generate(template, searchlist)


if __name__ == '__main__':
        sys.exit(main())
