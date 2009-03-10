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

"""Tests for the core Hooks logic."""

from bzrlib import errors
from bzrlib.hooks import (
    Hook,
    Hooks,
    )
from bzrlib.errors import (
    UnknownHook,
    )

from bzrlib.symbol_versioning import one_five
from bzrlib.tests import TestCase


class TestHooks(TestCase):

    def test_create_hook_first(self):
        hooks = Hooks()
        doc = ("Invoked after changing the tip of a branch object. Called with"
            "a bzrlib.branch.PostChangeBranchTipParams object")
        hook = Hook("post_tip_change", doc, (0, 15), None)
        hooks.create_hook(hook)
        self.assertEqual(hook, hooks['post_tip_change'])

    def test_create_hook_name_collision_errors(self):
        hooks = Hooks()
        doc = ("Invoked after changing the tip of a branch object. Called with"
            "a bzrlib.branch.PostChangeBranchTipParams object")
        hook = Hook("post_tip_change", doc, (0, 15), None)
        hook2 = Hook("post_tip_change", None, None, None)
        hooks.create_hook(hook)
        self.assertRaises(errors.DuplicateKey, hooks.create_hook, hook2)
        self.assertEqual(hook, hooks['post_tip_change'])

    def test_docs(self):
        """docs() should return something reasonable about the Hooks."""
        hooks = Hooks()
        hooks['legacy'] = []
        hook1 = Hook('post_tip_change',
            "Invoked after the tip of a branch changes. Called with "
            "a ChangeBranchTipParams object.", (1, 4), None)
        hook2 = Hook('pre_tip_change',
            "Invoked before the tip of a branch changes. Called with "
            "a ChangeBranchTipParams object. Hooks should raise "
            "TipChangeRejected to signal that a tip change is not permitted.",
            (1, 6), None)
        hooks.create_hook(hook1)
        hooks.create_hook(hook2)
        self.assertEqual(
            "legacy\n"
            "------\n"
            "\n"
            "An old-style hook. For documentation see the __init__ method of 'Hooks'\n"
            "\n"
            "post_tip_change\n"
            "---------------\n"
            "\n"
            "Introduced in: 1.4\n"
            "Deprecated in: Not deprecated\n"
            "\n"
            "Invoked after the tip of a branch changes. Called with a\n"
            "ChangeBranchTipParams object.\n"
            "\n"
            "pre_tip_change\n"
            "--------------\n"
            "\n"
            "Introduced in: 1.6\n"
            "Deprecated in: Not deprecated\n"
            "\n"
            "Invoked before the tip of a branch changes. Called with a\n"
            "ChangeBranchTipParams object. Hooks should raise TipChangeRejected to\n"
            "signal that a tip change is not permitted.\n", hooks.docs())

    def test_install_hook_raises_unknown_hook(self):
        """install_hook should raise UnknownHook if a hook is unknown."""
        hooks = Hooks()
        self.assertRaises(UnknownHook, self.applyDeprecated, one_five,
                          hooks.install_hook, 'silly', None)

    def test_install_hook_appends_known_hook(self):
        """install_hook should append the callable for known hooks."""
        hooks = Hooks()
        hooks['set_rh'] = []
        self.applyDeprecated(one_five, hooks.install_hook, 'set_rh', None)
        self.assertEqual(hooks['set_rh'], [None])

    def test_install_named_hook_raises_unknown_hook(self):
        hooks = Hooks()
        self.assertRaises(UnknownHook, hooks.install_named_hook, 'silly',
                          None, "")

    def test_install_named_hook_appends_known_hook(self):
        hooks = Hooks()
        hooks['set_rh'] = []
        hooks.install_named_hook('set_rh', None, "demo")
        self.assertEqual(hooks['set_rh'], [None])

    def test_install_named_hook_and_retrieve_name(self):
        hooks = Hooks()
        hooks['set_rh'] = []
        hooks.install_named_hook('set_rh', None, "demo")
        self.assertEqual("demo", hooks.get_hook_name(None))

    def test_name_hook_and_retrieve_name(self):
        """name_hook puts the name in the names mapping."""
        hooks = Hooks()
        hooks['set_rh'] = []
        self.applyDeprecated(one_five, hooks.install_hook, 'set_rh', None)
        hooks.name_hook(None, 'demo')
        self.assertEqual("demo", hooks.get_hook_name(None))

    def test_get_unnamed_hook_name_is_unnamed(self):
        hooks = Hooks()
        hooks['set_rh'] = []
        self.applyDeprecated(one_five, hooks.install_hook, 'set_rh', None)
        self.assertEqual("No hook name", hooks.get_hook_name(None))


class TestHook(TestCase):

    def test___init__(self):
        doc = ("Invoked after changing the tip of a branch object. Called with"
            "a bzrlib.branch.PostChangeBranchTipParams object")
        hook = Hook("post_tip_change", doc, (0, 15), None)
        self.assertEqual(doc, hook.__doc__)
        self.assertEqual("post_tip_change", hook.name)
        self.assertEqual((0, 15), hook.introduced)
        self.assertEqual(None, hook.deprecated)
        self.assertEqual([], list(hook))

    def test_docs(self):
        doc = ("Invoked after changing the tip of a branch object. Called with"
            " a bzrlib.branch.PostChangeBranchTipParams object")
        hook = Hook("post_tip_change", doc, (0, 15), None)
        self.assertEqual("post_tip_change\n"
            "---------------\n"
            "\n"
            "Introduced in: 0.15\n"
            "Deprecated in: Not deprecated\n"
            "\n"
            "Invoked after changing the tip of a branch object. Called with a\n"
            "bzrlib.branch.PostChangeBranchTipParams object\n", hook.docs())

    def test_hook(self):
        hook = Hook("foo", "no docs", None, None)
        def callback():
            pass
        hook.hook(callback, "my callback")
        self.assertEqual([callback], list(hook))

    def test___repr(self):
        # The repr should list all the callbacks, with names.
        hook = Hook("foo", "no docs", None, None)
        def callback():
            pass
        hook.hook(callback, "my callback")
        callback_repr = repr(callback)
        self.assertEqual(
            '<bzrlib.hooks.Hook(foo), callbacks=[%s(my callback)]>' %
            callback_repr, repr(hook))
