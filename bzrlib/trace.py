#! /usr/bin/env python
# -*- coding: UTF-8 -*-

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA


__copyright__ = "Copyright (C) 2005 Canonical Ltd."
__author__ = "Martin Pool <mbp@canonical.com>"


import sys

######################################################################
# messages and logging

# Messages are always written to here, so that we have some
# information if something goes wrong.  In a future version this
# file will be removed on successful completion.
_tracefile = file('.bzr.log', 'at')

## TODO: If --verbose is given then write to both stderr and
## _tracefile; perhaps replace _tracefile with a tee thing.

# used to have % (os.environ['USER'], time.time(), os.getpid()), 'w')


# If false, notes also go to stdout; should replace this with --silent
# at some point.
silent = False

verbose = False


def mutter(msg):
    _tracefile.write(msg)
    _tracefile.write('\n')
    _tracefile.flush()
    if verbose:
        sys.stderr.write('- ' + msg + '\n')


def note(msg):
    b = '* ' + str(msg) + '\n'
    if not silent:
        sys.stderr.write(b)
    _tracefile.write(b)
    _tracefile.flush()


def log_error(msg):
    sys.stderr.write(msg)
    _tracefile.write(msg)
    _tracefile.flush()
