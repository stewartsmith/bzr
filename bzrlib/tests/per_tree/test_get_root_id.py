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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA

"""Tests for Tree.get_root_id()"""

from bzrlib.tests.per_tree import TestCaseWithTree


class TestGetRootID(TestCaseWithTree):

    def make_tree_with_default_root_id(self):
        tree = self.make_branch_and_tree('tree')
        return self._convert_tree(tree)

    def make_tree_with_fixed_root_id(self):
        tree = self.make_branch_and_tree('tree')
        tree.set_root_id('custom-tree-root-id')
        return self._convert_tree(tree)

    def test_get_root_id_default(self):
        tree = self.make_tree_with_default_root_id()
        tree.lock_read()
        self.addCleanup(tree.unlock)
        self.assertIsNot(None, tree.get_root_id())

    def test_get_root_id_fixed(self):
        tree = self.make_tree_with_fixed_root_id()
        tree.lock_read()
        self.addCleanup(tree.unlock)
        self.assertEqual('custom-tree-root-id', tree.get_root_id())

