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

"""Tests for indices."""

from bzrlib import errors
from bzrlib.index import GraphIndexBuilder, GraphIndex
from bzrlib.tests import TestCaseWithMemoryTransport


class TestGraphIndexBuilder(TestCaseWithMemoryTransport):

    def test_build_index_empty(self):
        builder = GraphIndexBuilder()
        stream = builder.finish()
        contents = stream.read()
        self.assertEqual("Bazaar Graph Index 1\n\n", contents)


class TestGraphIndex(TestCaseWithMemoryTransport):

    def make_index(self):
        builder = GraphIndexBuilder()
        stream = builder.finish()
        trans = self.get_transport()
        trans.put('index', stream.read())
        return GraphIndex(trans, 'index')

    def test_iter_all_entries_empty(self):
        index = self.make_index()
        self.assertEqual([], list(index.iter_all_entries()))

    def test_iter_missing_entry_empty(self):
        index = self.make_index()
        self.assertRaises(errors.MissingKey, list, index.iter_entries(['a']))
