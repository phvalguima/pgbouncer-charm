#!/usr/bin/python

# Copyright 2012 Canonical Ltd. All rights reserved.
# Author: Haw Loeung <haw.loeung@canonical.com>

# Script used to generate file using python-cheetah template.

from optparse import OptionParser
from Cheetah.Template import Template
import json
import sys


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
    searchlist = json.loads(open(options.json).read())

    for template in args:
        template_generate(template, searchlist)


if __name__ == '__main__':
        sys.exit(main())
