# Copyright (C) 2005, 2007 Canonical Ltd
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

"""Test Escaped Stores."""

from cStringIO import StringIO
import os
import gzip

from bzrlib import osutils
from bzrlib.errors import BzrError, UnlistableStore, NoSuchFile
from bzrlib.store.text import TextStore
from bzrlib.tests import TestCaseWithTransport
import bzrlib.transport


class TestEscaped(TestCaseWithTransport):
    """Mixin template class that provides some common tests for stores"""

    def get_store(self, prefixed=False, escaped=True):
        t = bzrlib.transport.get_transport(self.get_url())
        return TextStore(t, prefixed=prefixed, escaped=escaped)

    def test_prefixed(self):
        # Prefix should be determined by unescaped string
        text_store = self.get_store(prefixed=True)

        # hash_prefix() is not defined for unicode characters
        # it is only defined for byte streams.
        # so hash_prefix() needs to operate on *at most* utf-8
        # encoded. However urlutils.escape() does both encoding to utf-8
        # and urllib quoting, so we will use the escaped form
        # as the path passed to hash_prefix

        self.assertEqual('62/a', text_store._relpath('a'))
        self.assertEqual('88/%2520', text_store._relpath(' '))
        self.assertEqual('72/%40%253a%253c%253e',
                text_store._relpath('@:<>'))

    def test_files(self):
        text_store = self.get_store(prefixed=True)

        text_store.add(StringIO('a'), 'a')
        self.failUnlessExists('62/a')

        text_store.add(StringIO('space'), ' ')
        self.failUnlessExists('88/%20')
        self.assertEquals('space', text_store.get(' ').read())

        text_store.add(StringIO('surprise'), '@:<>')
        self.failUnlessExists('72/@%3a%3c%3e')
        self.assertEquals('surprise', text_store.get('@:<>').read())

        text_store.add(StringIO('utf8'), '\xc2\xb5')
        self.failUnlessExists('77/%c2%b5')
        self.assertEquals('utf8', text_store.get('\xc2\xb5').read())

    def test_weave(self):
        from bzrlib.store.versioned import WeaveStore
        from bzrlib.transactions import PassThroughTransaction

        trans = PassThroughTransaction()

        t = bzrlib.transport.get_transport(self.get_url())
        weave_store = WeaveStore(t, prefixed=True, escaped=True)
        def add_text(file_id, rev_id, contents, parents, transaction):
            vfile = weave_store.get_weave_or_empty(file_id, transaction)
            vfile.add_lines(rev_id, parents, contents)

        def check_text(file_id, revision_id, contents):
            vfile = weave_store.get_weave(file_id, trans)
            self.assertEqual(contents, vfile.get_lines(revision_id))

        add_text('a', 'r', ['a'], [], trans)
        self.failUnlessExists('62/a.weave')
        check_text('a', 'r', ['a'])

        add_text(' ', 'r', ['space'], [], trans)
        self.failIfExists('21/ .weave')
        self.failUnlessExists('88/%20.weave')
        check_text(' ', 'r', ['space'])

        add_text('@:<>', 'r', ['surprise'], [], trans)
        self.failUnlessExists('72/@%3a%3c%3e.weave')
        check_text('@:<>', 'r', ['surprise'])

        add_text('\xc2\xb5', 'r', ['utf8'], [], trans)
        self.failUnlessExists('77/%c2%b5.weave')
        check_text('\xc2\xb5', 'r', ['utf8'])
