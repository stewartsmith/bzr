# Copyright (C) 2007 Canonical Ltd
# Authors:  Robert Collins <robert.collins@canonical.com>
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


from bzrlib import (
    inventory,
    )
from bzrlib.tests import TestNotApplicable
from bzrlib.transform import TreeTransform
from bzrlib.tests.per_workingtree import TestCaseWithWorkingTree


class TestNestedSupport(TestCaseWithWorkingTree):

    def make_branch_and_tree(self, path):
        tree = TestCaseWithWorkingTree.make_branch_and_tree(self, path)
        if not tree.supports_tree_reference():
            raise TestNotApplicable('Tree references not supported')
        return tree

    def test_set_get_tree_reference(self):
        """This tests that setting a tree reference is persistent."""
        tree = self.make_branch_and_tree('.')
        transform = TreeTransform(tree)
        trans_id = transform.new_directory('reference', transform.root,
            'subtree-id')
        transform.set_tree_reference('subtree-revision', trans_id)
        transform.apply()
        tree = tree.bzrdir.open_workingtree()
        tree.lock_read()
        self.addCleanup(tree.unlock)
        self.assertEqual('subtree-revision',
            tree.root_inventory['subtree-id'].reference_revision)

    def test_extract_while_locked(self):
        tree = self.make_branch_and_tree('.')
        tree.lock_write()
        self.addCleanup(tree.unlock)
        self.build_tree(['subtree/'])
        tree.add(['subtree'], ['subtree-id'])
        subtree = tree.extract('subtree-id')

    def prepare_with_subtree(self):
        tree = self.make_branch_and_tree('.')
        tree.lock_write()
        self.addCleanup(tree.unlock)
        subtree = self.make_branch_and_tree('subtree')
        tree.add(['subtree'], ['subtree-id'])
        return tree

    def test_kind_does_not_autodetect_subtree(self):
        tree = self.prepare_with_subtree()
        self.assertEqual('directory', tree.kind('subtree-id'))

    def test_comparison_data_does_not_autodetect_subtree(self):
        tree = self.prepare_with_subtree()
        ie = inventory.InventoryDirectory('subtree-id', 'subtree',
                                          tree.path2id(''))
        self.assertEqual('directory',
                         tree._comparison_data(ie, 'subtree')[0])

    def test_inventory_does_not_autodetect_subtree(self):
        tree = self.prepare_with_subtree()
        self.assertEqual('directory', tree.kind('subtree-id'))

    def test_iter_entries_by_dir_autodetects_subtree(self):
        tree = self.prepare_with_subtree()
        path, ie = tree.iter_entries_by_dir(['subtree-id']).next()
        self.assertEqual('tree-reference', ie.kind)
