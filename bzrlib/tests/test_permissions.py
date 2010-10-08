# Copyright (C) 2005-2010 Canonical Ltd
# -*- coding: utf-8 -*-
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA


"""Tests for bzr setting permissions.

Files which are created underneath .bzr/ should inherit its permissions.
So if the directory is group writable, the files and subdirs should be as well.

In the future, when we have Repository/Branch/Checkout information, the
permissions should be inherited individually, rather than all be the same.
"""

# TODO: jam 20051215 There are no tests for ftp yet, because we have no ftp server
# TODO: jam 20051215 Currently the default behavior for 'bzr branch' is just
#                    defined by the local umask. This isn't terrible, is it
#                    the truly desired behavior?

import os
import sys
import stat
from cStringIO import StringIO
import urllib

from bzrlib import transport
from bzrlib.branch import Branch
from bzrlib.bzrdir import BzrDir
from bzrlib.tests import TestCaseWithTransport, TestSkipped
from bzrlib.tests.test_sftp_transport import TestCaseWithSFTPServer
from bzrlib.workingtree import WorkingTree


def chmod_r(base, file_mode, dir_mode):
    """Recursively chmod from a base directory"""
    os.chmod(base, dir_mode)
    for root, dirs, files in os.walk(base):
        for d in dirs:
            p = os.path.join(root, d)
            os.chmod(p, dir_mode)
        for f in files:
            p = os.path.join(root, f)
            os.chmod(p, file_mode)


def check_mode_r(test, base, file_mode, dir_mode, include_base=True):
    """Check that all permissions match

    :param test: The TestCase being run
    :param base: The path to the root directory to check
    :param file_mode: The mode for all files
    :param dir_mode: The mode for all directories
    :param include_base: If false, only check the subdirectories
    """
    t = transport.get_transport(".")
    if include_base:
        test.assertTransportMode(t, base, dir_mode)
    for root, dirs, files in os.walk(base):
        for d in dirs:
            p = '/'.join([urllib.quote(x) for x in root.split('/\\') + [d]])
            test.assertTransportMode(t, p, dir_mode)
        for f in files:
            p = os.path.join(root, f)
            p = '/'.join([urllib.quote(x) for x in root.split('/\\') + [f]])
            test.assertTransportMode(t, p, file_mode)


class TestPermissions(TestCaseWithTransport):

    def test_new_files(self):
        if sys.platform == 'win32':
            raise TestSkipped('chmod has no effect on win32')

        t = self.make_branch_and_tree('.')
        b = t.branch
        open('a', 'wb').write('foo\n')
        # ensure check_mode_r works with capital-letter file-ids like TREE_ROOT
        t.add('a', 'CAPS-ID')
        t.commit('foo')

        chmod_r('.bzr', 0644, 0755)
        check_mode_r(self, '.bzr', 0644, 0755)

        # although we are modifying the filesystem
        # underneath the objects, they are not locked, and thus it must
        # be safe for most operations. But here we want to observe a
        # mode change in the control bits, which current do not refresh
        # when a new lock is taken out.
        t = WorkingTree.open('.')
        b = t.branch
        self.assertEqualMode(0755, b.control_files._dir_mode)
        self.assertEqualMode(0644, b.control_files._file_mode)
        self.assertEqualMode(0755, b.bzrdir._get_dir_mode())
        self.assertEqualMode(0644, b.bzrdir._get_file_mode())

        # Modifying a file shouldn't break the permissions
        open('a', 'wb').write('foo2\n')
        t.commit('foo2')
        # The mode should be maintained after commit
        check_mode_r(self, '.bzr', 0644, 0755)

        # Adding a new file should maintain the permissions
        open('b', 'wb').write('new b\n')
        t.add('b')
        t.commit('new b')
        check_mode_r(self, '.bzr', 0644, 0755)

        # Recursively update the modes of all files
        chmod_r('.bzr', 0664, 0775)
        check_mode_r(self, '.bzr', 0664, 0775)
        t = WorkingTree.open('.')
        b = t.branch
        self.assertEqualMode(0775, b.control_files._dir_mode)
        self.assertEqualMode(0664, b.control_files._file_mode)
        self.assertEqualMode(0775, b.bzrdir._get_dir_mode())
        self.assertEqualMode(0664, b.bzrdir._get_file_mode())

        open('a', 'wb').write('foo3\n')
        t.commit('foo3')
        check_mode_r(self, '.bzr', 0664, 0775)

        open('c', 'wb').write('new c\n')
        t.add('c')
        t.commit('new c')
        check_mode_r(self, '.bzr', 0664, 0775)

    def test_new_files_group_sticky_bit(self):
        if sys.platform == 'win32':
            raise TestSkipped('chmod has no effect on win32')
        elif sys.platform == 'darwin' or sys.platform.startswith('freebsd'):
            # OS X (and FreeBSD) create temp dirs with the 'wheel' group, which
            # users are not likely to be in, and this prevents us from setting
            # the sgid bit
            os.chown(self.test_dir, os.getuid(), os.getgid())

        t = self.make_branch_and_tree('.')
        b = t.branch

        # Test the group sticky bit
        # Recursively update the modes of all files
        chmod_r('.bzr', 0664, 02775)
        check_mode_r(self, '.bzr', 0664, 02775)
        t = WorkingTree.open('.')
        b = t.branch
        self.assertEqualMode(02775, b.control_files._dir_mode)
        self.assertEqualMode(0664, b.control_files._file_mode)
        self.assertEqualMode(02775, b.bzrdir._get_dir_mode())
        self.assertEqualMode(0664, b.bzrdir._get_file_mode())

        open('a', 'wb').write('foo4\n')
        t.commit('foo4')
        check_mode_r(self, '.bzr', 0664, 02775)

        open('d', 'wb').write('new d\n')
        t.add('d')
        t.commit('new d')
        check_mode_r(self, '.bzr', 0664, 02775)


class TestSftpPermissions(TestCaseWithSFTPServer):

    def test_new_files(self):
        if sys.platform == 'win32':
            raise TestSkipped('chmod has no effect on win32')
        # Though it would be nice to test that SFTP to a server
        # which does support chmod has the right effect

        # bodge around for stubsftpserver not letting use connect
        # more than once
        _t = transport.get_transport(self.get_url())

        os.mkdir('local')
        t_local = self.make_branch_and_tree('local')
        b_local = t_local.branch
        open('local/a', 'wb').write('foo\n')
        t_local.add('a')
        t_local.commit('foo')

        # Delete them because we are modifying the filesystem underneath them
        chmod_r('local/.bzr', 0644, 0755)
        check_mode_r(self, 'local/.bzr', 0644, 0755)

        t = WorkingTree.open('local')
        b_local = t.branch
        self.assertEqualMode(0755, b_local.control_files._dir_mode)
        self.assertEqualMode(0644, b_local.control_files._file_mode)
        self.assertEqualMode(0755, b_local.bzrdir._get_dir_mode())
        self.assertEqualMode(0644, b_local.bzrdir._get_file_mode())

        os.mkdir('sftp')
        sftp_url = self.get_url('sftp')
        b_sftp = BzrDir.create_branch_and_repo(sftp_url)

        b_sftp.pull(b_local)
        del b_sftp
        chmod_r('sftp/.bzr', 0644, 0755)
        check_mode_r(self, 'sftp/.bzr', 0644, 0755)

        b_sftp = Branch.open(sftp_url)
        self.assertEqualMode(0755, b_sftp.control_files._dir_mode)
        self.assertEqualMode(0644, b_sftp.control_files._file_mode)
        self.assertEqualMode(0755, b_sftp.bzrdir._get_dir_mode())
        self.assertEqualMode(0644, b_sftp.bzrdir._get_file_mode())

        open('local/a', 'wb').write('foo2\n')
        t_local.commit('foo2')
        b_sftp.pull(b_local)
        # The mode should be maintained after commit
        check_mode_r(self, 'sftp/.bzr', 0644, 0755)

        open('local/b', 'wb').write('new b\n')
        t_local.add('b')
        t_local.commit('new b')
        b_sftp.pull(b_local)
        check_mode_r(self, 'sftp/.bzr', 0644, 0755)

        del b_sftp
        # Recursively update the modes of all files
        chmod_r('sftp/.bzr', 0664, 0775)
        check_mode_r(self, 'sftp/.bzr', 0664, 0775)

        b_sftp = Branch.open(sftp_url)
        self.assertEqualMode(0775, b_sftp.control_files._dir_mode)
        self.assertEqualMode(0664, b_sftp.control_files._file_mode)
        self.assertEqualMode(0775, b_sftp.bzrdir._get_dir_mode())
        self.assertEqualMode(0664, b_sftp.bzrdir._get_file_mode())

        open('local/a', 'wb').write('foo3\n')
        t_local.commit('foo3')
        b_sftp.pull(b_local)
        check_mode_r(self, 'sftp/.bzr', 0664, 0775)

        open('local/c', 'wb').write('new c\n')
        t_local.add('c')
        t_local.commit('new c')
        b_sftp.pull(b_local)
        check_mode_r(self, 'sftp/.bzr', 0664, 0775)

    def test_sftp_server_modes(self):
        if sys.platform == 'win32':
            raise TestSkipped('chmod has no effect on win32')

        umask = 0022
        original_umask = os.umask(umask)

        try:
            t = transport.get_transport(self.get_url())
            # Direct access should be masked by umask
            t._sftp_open_exclusive('a', mode=0666).write('foo\n')
            self.assertTransportMode(t, 'a', 0666 &~umask)

            # but Transport overrides umask
            t.put_bytes('b', 'txt', mode=0666)
            self.assertTransportMode(t, 'b', 0666)

            t._get_sftp().mkdir('c', mode=0777)
            self.assertTransportMode(t, 'c', 0777 &~umask)

            t.mkdir('d', mode=0777)
            self.assertTransportMode(t, 'd', 0777)
        finally:
            os.umask(original_umask)
