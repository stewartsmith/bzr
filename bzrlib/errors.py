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


######################################################################
# exceptions 
class BzrError(StandardError):
    pass

class BzrCheckError(BzrError):
    pass


class BzrCommandError(BzrError):
    # Error from malformed user command
    pass


class NotBranchError(BzrError):
    """Specified path is not in a branch"""
    pass


class BadFileKindError(BzrError):
    """Specified file is of a kind that cannot be added.

    (For example a symlink or device file.)"""
    pass


class ForbiddenFileError(BzrError):
    """Cannot operate on a file because it is a control file."""
    pass


class LockError(BzrError):
    pass


def bailout(msg, explanation=[]):
    ex = BzrError(msg, explanation)
    import trace
    trace._tracefile.write('* raising %s\n' % ex)
    raise ex

