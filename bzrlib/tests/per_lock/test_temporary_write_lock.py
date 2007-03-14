# Copyright (C) 2007 Canonical Ltd
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Tests for temporarily upgrading to a WriteLock."""

from bzrlib import (
    errors,
    )

from bzrlib.tests.per_lock import TestCaseWithLock


class TestTemporaryWriteLock(TestCaseWithLock):

    def setUp(self):
        super(TestTemporaryWriteLock, self).setUp()
        self.build_tree(['a-file'])

    def test_can_upgrade_and_write(self):
        """With only one lock, we should be able to write lock and switch back."""
        a_lock = self.read_lock('a-file')
        try:
            t_write_lock = a_lock.temporary_write_lock()
            try:
                self.assertEqual('contents of a-file\n',
                                 t_write_lock.f.read())
                # We should be able to write to the file.
                t_write_lock.f.seek(0)
                t_write_lock.f.write('new contents for a-file\n')
                t_write_lock.f.seek(0)
                self.assertEqual('new contents for a-file\n',
                                 t_write_lock.f.read())
            finally:
                a_lock = t_write_lock.restore_read_lock()
        finally:
            a_lock.unlock()

    def test_is_write_locked(self):
        """With a temporary write lock, we cannot grab another lock."""
        a_lock = self.read_lock('a-file')
        try:
            t_write_lock = a_lock.temporary_write_lock()
            try:
                self.assertRaises(errors.LockContention,
                                  self.write_lock, 'a-file')
                self.assertRaises(errors.LockContention,
                                  self.read_lock, 'a-file')
            finally:
                a_lock = t_write_lock.restore_read_lock()
            # Now we only have a read lock, so we should be able to grab
            # another read lock, but not a write lock
            self.assertRaises(errors.LockContention,
                              self.write_lock, 'a-file')
            b_lock = self.read_lock('a-file')
            b_lock.unlock()
        finally:
            a_lock.unlock()

    # TODO: jam 20070314 to truly test these, we should be spawning an external
    #       process, and having it lock/unlock/try lock on request.

    # TODO: jam 20070314 Test that the write lock can fail if another process
    #       holds a read lock. And that we recover properly.
