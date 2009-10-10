# Copyright (C) 2009 Canonical Ltd
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


"""Tests specific to Branch implementations that use foreign VCS'es."""


from bzrlib.errors import (
    UnstackableBranchFormat,
    )
from bzrlib.revision import (
    NULL_REVISION,
    )
from bzrlib.tests import (
    TestCase,
    )


class ForeignBranchFactory(object):
    """Factory of branches for ForeignBranchTests."""

    def make_empty_branch(self):
        """Create an empty branch with no commits in it."""
        raise NotImplementedError(self.make_empty_branch)


class ForeignBranchTests(TestCase):
    """Basic tests for foreign branch implementations.
    
    These tests mainly make sure that the implementation covers the required 
    bits of the API and returns reasonable values. 
    """
    branch_factory = None # Set to an instance of ForeignBranchFactory by scenario

    def test_set_parent(self):
        """Test that setting the parent works."""
        branch = self.branch_factory.make_empty_branch()
        branch.set_parent("foobar")

    def test_break_lock(self):
        """Test that break_lock() works, even if it is a no-op."""
        branch = self.branch_factory.make_empty_branch()
        branch.break_lock()

    def test_set_push_location(self):
        """Test that setting the push location works."""
        branch = self.branch_factory.make_empty_branch()
        branch.set_push_location("http://bar/bloe")

    def test_repr_type(self):
        branch = self.branch_factory.make_empty_branch()
        self.assertIsInstance(repr(branch), str)

    def test_get_parent(self):
        """Test that getting the parent location works, and returns None."""
        # TODO: Allow this to be non-None when foreign branches add support 
        #       for storing this URL.
        branch = self.branch_factory.make_empty_branch()
        self.assertIs(None, branch.get_parent())

    def test_get_push_location(self):
        """Test that getting the push location works, and returns None."""
        # TODO: Allow this to be non-None when foreign branches add support 
        #       for storing this URL.
        branch = self.branch_factory.make_empty_branch()
        self.assertIs(None, branch.get_push_location())

    def test_check(self):
        """See if a basic check works."""
        branch = self.branch_factory.make_empty_branch()
        result = branch.check()
        self.assertEqual(branch, result.branch) 

    def test_attributes(self):
        """Check that various required attributes are present."""
        branch = self.branch_factory.make_empty_branch()
        self.assertIsNot(None, getattr(branch, "repository", None))
        self.assertIsNot(None, getattr(branch, "mapping", None))
        self.assertIsNot(None, getattr(branch, "_format", None))
        self.assertIsNot(None, getattr(branch, "base", None))

    def test__get_nick(self):
        """Make sure _get_nick is implemented and returns a string."""
        branch = self.branch_factory.make_empty_branch()
        self.assertIsInstance(branch._get_nick(local=False), str)
        self.assertIsInstance(branch._get_nick(local=True), str)

    def test_null_revid_revno(self):
        """null: should return revno 0."""
        branch = self.branch_factory.make_empty_branch()
        self.assertEquals(0, branch.revision_id_to_revno(NULL_REVISION))

    def test_get_stacked_on_url(self):
        """Test that get_stacked_on_url() behaves as expected.

        Inter-Format stacking doesn't work yet, so all foreign implementations
        should raise UnstackableBranchFormat at the moment.
        """
        branch = self.branch_factory.make_empty_branch()
        self.assertRaises(UnstackableBranchFormat, 
                          branch.get_stacked_on_url)

    def test_get_physical_lock_status(self):
        branch = self.branch_factory.make_empty_branch()
        self.assertFalse(branch.get_physical_lock_status())

    def test_last_revision_empty_branch(self):
        branch = self.branch_factory.make_empty_branch()
        self.assertEquals(NULL_REVISION, branch.last_revision())
        self.assertEquals(0, branch.revno())
        self.assertEquals((0, NULL_REVISION), branch.last_revision_info())


class ForeignBranchFormatTests(TestCase):
    """Basic tests for foreign branch format objects."""

    branch_format = None # Set to a BranchFormat instance by adapter

    def test_initialize(self):
        """Test this format is not initializable.
        
        Remote branches may be initializable on their own, but none currently
        support living in .bzr/branch.
        """
        self.assertRaises(NotImplementedError, self.branch_format.initialize, None)

    def test_get_format_description_type(self):
        self.assertIsInstance(self.branch_format.get_format_description(), str)

    def test_network_name(self):
        self.assertIsInstance(self.branch_format.network_name(), str)


