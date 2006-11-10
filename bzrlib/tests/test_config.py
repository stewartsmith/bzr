# Copyright (C) 2005, 2006 Canonical Ltd
#   Authors: Robert Collins <robert.collins@canonical.com>
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

"""Tests for finding and reading the bzr config file[s]."""
# import system imports here
from bzrlib.util.configobj.configobj import ConfigObj, ConfigObjError
from cStringIO import StringIO
import os
import sys

#import bzrlib specific imports here
from bzrlib import (
    config,
    errors,
    osutils,
    urlutils,
    )
from bzrlib.branch import Branch
from bzrlib.bzrdir import BzrDir
from bzrlib.tests import TestCase, TestCaseInTempDir, TestCaseWithTransport


sample_long_alias="log -r-15..-1 --line"
sample_config_text = u"""
[DEFAULT]
email=Erik B\u00e5gfors <erik@bagfors.nu>
editor=vim
gpg_signing_command=gnome-gpg
log_format=short
user_global_option=something
[ALIASES]
h=help
ll=""" + sample_long_alias + "\n"


sample_always_signatures = """
[DEFAULT]
check_signatures=ignore
create_signatures=always
"""

sample_ignore_signatures = """
[DEFAULT]
check_signatures=require
create_signatures=never
"""

sample_maybe_signatures = """
[DEFAULT]
check_signatures=ignore
create_signatures=when-required
"""

sample_branches_text = """
[http://www.example.com]
# Top level policy
email=Robert Collins <robertc@example.org>
normal_option = normal
appendpath_option = append
appendpath_option:policy = appendpath
norecurse_option = norecurse
norecurse_option:policy = norecurse
[http://www.example.com/ignoreparent]
# different project: ignore parent dir config
ignore_parents=true
[http://www.example.com/norecurse]
# configuration items that only apply to this dir
recurse=false
normal_option = norecurse
[http://www.example.com/dir]
appendpath_option = normal
[/b/]
check_signatures=require
# test trailing / matching with no children
[/a/]
check_signatures=check-available
gpg_signing_command=false
user_local_option=local
# test trailing / matching
[/a/*]
#subdirs will match but not the parent
[/a/c]
check_signatures=ignore
post_commit=bzrlib.tests.test_config.post_commit
#testing explicit beats globs
"""


class InstrumentedConfigObj(object):
    """A config obj look-enough-alike to record calls made to it."""

    def __contains__(self, thing):
        self._calls.append(('__contains__', thing))
        return False

    def __getitem__(self, key):
        self._calls.append(('__getitem__', key))
        return self

    def __init__(self, input, encoding=None):
        self._calls = [('__init__', input, encoding)]

    def __setitem__(self, key, value):
        self._calls.append(('__setitem__', key, value))

    def __delitem__(self, key):
        self._calls.append(('__delitem__', key))

    def keys(self):
        self._calls.append(('keys',))
        return []

    def write(self, arg):
        self._calls.append(('write',))

    def as_bool(self, value):
        self._calls.append(('as_bool', value))
        return False

    def get_value(self, section, name):
        self._calls.append(('get_value', section, name))
        return None


class FakeBranch(object):

    def __init__(self, base=None, user_id=None):
        if base is None:
            self.base = "http://example.com/branches/demo"
        else:
            self.base = base
        self.control_files = FakeControlFiles(user_id=user_id)

    def lock_write(self):
        pass

    def unlock(self):
        pass


class FakeControlFiles(object):

    def __init__(self, user_id=None):
        self.email = user_id
        self.files = {}

    def get_utf8(self, filename):
        if filename != 'email':
            raise NotImplementedError
        if self.email is not None:
            return StringIO(self.email)
        raise errors.NoSuchFile(filename)

    def get(self, filename):
        try:
            return StringIO(self.files[filename])
        except KeyError:
            raise errors.NoSuchFile(filename)

    def put(self, filename, fileobj):
        self.files[filename] = fileobj.read()


class InstrumentedConfig(config.Config):
    """An instrumented config that supplies stubs for template methods."""
    
    def __init__(self):
        super(InstrumentedConfig, self).__init__()
        self._calls = []
        self._signatures = config.CHECK_NEVER

    def _get_user_id(self):
        self._calls.append('_get_user_id')
        return "Robert Collins <robert.collins@example.org>"

    def _get_signature_checking(self):
        self._calls.append('_get_signature_checking')
        return self._signatures


bool_config = """[DEFAULT]
active = true
inactive = false
[UPPERCASE]
active = True
nonactive = False
"""
class TestConfigObj(TestCase):
    def test_get_bool(self):
        from bzrlib.config import ConfigObj
        co = ConfigObj(StringIO(bool_config))
        self.assertIs(co.get_bool('DEFAULT', 'active'), True)
        self.assertIs(co.get_bool('DEFAULT', 'inactive'), False)
        self.assertIs(co.get_bool('UPPERCASE', 'active'), True)
        self.assertIs(co.get_bool('UPPERCASE', 'nonactive'), False)


class TestConfig(TestCase):

    def test_constructs(self):
        config.Config()
 
    def test_no_default_editor(self):
        self.assertRaises(NotImplementedError, config.Config().get_editor)

    def test_user_email(self):
        my_config = InstrumentedConfig()
        self.assertEqual('robert.collins@example.org', my_config.user_email())
        self.assertEqual(['_get_user_id'], my_config._calls)

    def test_username(self):
        my_config = InstrumentedConfig()
        self.assertEqual('Robert Collins <robert.collins@example.org>',
                         my_config.username())
        self.assertEqual(['_get_user_id'], my_config._calls)

    def test_signatures_default(self):
        my_config = config.Config()
        self.assertFalse(my_config.signature_needed())
        self.assertEqual(config.CHECK_IF_POSSIBLE,
                         my_config.signature_checking())
        self.assertEqual(config.SIGN_WHEN_REQUIRED,
                         my_config.signing_policy())

    def test_signatures_template_method(self):
        my_config = InstrumentedConfig()
        self.assertEqual(config.CHECK_NEVER, my_config.signature_checking())
        self.assertEqual(['_get_signature_checking'], my_config._calls)

    def test_signatures_template_method_none(self):
        my_config = InstrumentedConfig()
        my_config._signatures = None
        self.assertEqual(config.CHECK_IF_POSSIBLE,
                         my_config.signature_checking())
        self.assertEqual(['_get_signature_checking'], my_config._calls)

    def test_gpg_signing_command_default(self):
        my_config = config.Config()
        self.assertEqual('gpg', my_config.gpg_signing_command())

    def test_get_user_option_default(self):
        my_config = config.Config()
        self.assertEqual(None, my_config.get_user_option('no_option'))

    def test_post_commit_default(self):
        my_config = config.Config()
        self.assertEqual(None, my_config.post_commit())

    def test_log_format_default(self):
        my_config = config.Config()
        self.assertEqual('long', my_config.log_format())


class TestConfigPath(TestCase):

    def setUp(self):
        super(TestConfigPath, self).setUp()
        self.old_home = os.environ.get('HOME', None)
        self.old_appdata = os.environ.get('APPDATA', None)
        os.environ['HOME'] = '/home/bogus'
        os.environ['APPDATA'] = \
            r'C:\Documents and Settings\bogus\Application Data'

    def tearDown(self):
        if self.old_home is None:
            del os.environ['HOME']
        else:
            os.environ['HOME'] = self.old_home
        if self.old_appdata is None:
            del os.environ['APPDATA']
        else:
            os.environ['APPDATA'] = self.old_appdata
        super(TestConfigPath, self).tearDown()
    
    def test_config_dir(self):
        if sys.platform == 'win32':
            self.assertEqual(config.config_dir(), 
                'C:/Documents and Settings/bogus/Application Data/bazaar/2.0')
        else:
            self.assertEqual(config.config_dir(), '/home/bogus/.bazaar')

    def test_config_filename(self):
        if sys.platform == 'win32':
            self.assertEqual(config.config_filename(), 
                'C:/Documents and Settings/bogus/Application Data/bazaar/2.0/bazaar.conf')
        else:
            self.assertEqual(config.config_filename(),
                             '/home/bogus/.bazaar/bazaar.conf')

    def test_branches_config_filename(self):
        if sys.platform == 'win32':
            self.assertEqual(config.branches_config_filename(), 
                'C:/Documents and Settings/bogus/Application Data/bazaar/2.0/branches.conf')
        else:
            self.assertEqual(config.branches_config_filename(),
                             '/home/bogus/.bazaar/branches.conf')

    def test_locations_config_filename(self):
        if sys.platform == 'win32':
            self.assertEqual(config.locations_config_filename(), 
                'C:/Documents and Settings/bogus/Application Data/bazaar/2.0/locations.conf')
        else:
            self.assertEqual(config.locations_config_filename(),
                             '/home/bogus/.bazaar/locations.conf')

class TestIniConfig(TestCase):

    def test_contructs(self):
        my_config = config.IniBasedConfig("nothing")

    def test_from_fp(self):
        config_file = StringIO(sample_config_text.encode('utf-8'))
        my_config = config.IniBasedConfig(None)
        self.failUnless(
            isinstance(my_config._get_parser(file=config_file),
                        ConfigObj))

    def test_cached(self):
        config_file = StringIO(sample_config_text.encode('utf-8'))
        my_config = config.IniBasedConfig(None)
        parser = my_config._get_parser(file=config_file)
        self.failUnless(my_config._get_parser() is parser)


class TestGetConfig(TestCase):

    def test_constructs(self):
        my_config = config.GlobalConfig()

    def test_calls_read_filenames(self):
        # replace the class that is constructured, to check its parameters
        oldparserclass = config.ConfigObj
        config.ConfigObj = InstrumentedConfigObj
        my_config = config.GlobalConfig()
        try:
            parser = my_config._get_parser()
        finally:
            config.ConfigObj = oldparserclass
        self.failUnless(isinstance(parser, InstrumentedConfigObj))
        self.assertEqual(parser._calls, [('__init__', config.config_filename(),
                                          'utf-8')])


class TestBranchConfig(TestCaseWithTransport):

    def test_constructs(self):
        branch = FakeBranch()
        my_config = config.BranchConfig(branch)
        self.assertRaises(TypeError, config.BranchConfig)

    def test_get_location_config(self):
        branch = FakeBranch()
        my_config = config.BranchConfig(branch)
        location_config = my_config._get_location_config()
        self.assertEqual(branch.base, location_config.location)
        self.failUnless(location_config is my_config._get_location_config())

    def test_get_config(self):
        """The Branch.get_config method works properly"""
        b = BzrDir.create_standalone_workingtree('.').branch
        my_config = b.get_config()
        self.assertIs(my_config.get_user_option('wacky'), None)
        my_config.set_user_option('wacky', 'unlikely')
        self.assertEqual(my_config.get_user_option('wacky'), 'unlikely')

        # Ensure we get the same thing if we start again
        b2 = Branch.open('.')
        my_config2 = b2.get_config()
        self.assertEqual(my_config2.get_user_option('wacky'), 'unlikely')

    def test_has_explicit_nickname(self):
        b = self.make_branch('.')
        self.assertFalse(b.get_config().has_explicit_nickname())
        b.nick = 'foo'
        self.assertTrue(b.get_config().has_explicit_nickname())

    def test_config_url(self):
        """The Branch.get_config will use section that uses a local url"""
        branch = self.make_branch('branch')
        self.assertEqual('branch', branch.nick)

        locations = config.locations_config_filename()
        config.ensure_config_dir_exists()
        local_url = urlutils.local_path_to_url('branch')
        open(locations, 'wb').write('[%s]\nnickname = foobar' 
                                    % (local_url,))
        self.assertEqual('foobar', branch.nick)

    def test_config_local_path(self):
        """The Branch.get_config will use a local system path"""
        branch = self.make_branch('branch')
        self.assertEqual('branch', branch.nick)

        locations = config.locations_config_filename()
        config.ensure_config_dir_exists()
        open(locations, 'wb').write('[%s/branch]\nnickname = barry' 
                                    % (osutils.getcwd().encode('utf8'),))
        self.assertEqual('barry', branch.nick)

    def test_config_creates_local(self):
        """Creating a new entry in config uses a local path."""
        branch = self.make_branch('branch')
        branch.set_push_location('http://foobar')
        locations = config.locations_config_filename()
        local_path = osutils.getcwd().encode('utf8')
        # Surprisingly ConfigObj doesn't create a trailing newline
        self.check_file_contents(locations,
            '[%s/branch]\npush_location = http://foobar\npush_location:policy = norecurse' % (local_path,))


class TestGlobalConfigItems(TestCase):

    def test_user_id(self):
        config_file = StringIO(sample_config_text.encode('utf-8'))
        my_config = config.GlobalConfig()
        my_config._parser = my_config._get_parser(file=config_file)
        self.assertEqual(u"Erik B\u00e5gfors <erik@bagfors.nu>",
                         my_config._get_user_id())

    def test_absent_user_id(self):
        config_file = StringIO("")
        my_config = config.GlobalConfig()
        my_config._parser = my_config._get_parser(file=config_file)
        self.assertEqual(None, my_config._get_user_id())

    def test_configured_editor(self):
        config_file = StringIO(sample_config_text.encode('utf-8'))
        my_config = config.GlobalConfig()
        my_config._parser = my_config._get_parser(file=config_file)
        self.assertEqual("vim", my_config.get_editor())

    def test_signatures_always(self):
        config_file = StringIO(sample_always_signatures)
        my_config = config.GlobalConfig()
        my_config._parser = my_config._get_parser(file=config_file)
        self.assertEqual(config.CHECK_NEVER,
                         my_config.signature_checking())
        self.assertEqual(config.SIGN_ALWAYS,
                         my_config.signing_policy())
        self.assertEqual(True, my_config.signature_needed())

    def test_signatures_if_possible(self):
        config_file = StringIO(sample_maybe_signatures)
        my_config = config.GlobalConfig()
        my_config._parser = my_config._get_parser(file=config_file)
        self.assertEqual(config.CHECK_NEVER,
                         my_config.signature_checking())
        self.assertEqual(config.SIGN_WHEN_REQUIRED,
                         my_config.signing_policy())
        self.assertEqual(False, my_config.signature_needed())

    def test_signatures_ignore(self):
        config_file = StringIO(sample_ignore_signatures)
        my_config = config.GlobalConfig()
        my_config._parser = my_config._get_parser(file=config_file)
        self.assertEqual(config.CHECK_ALWAYS,
                         my_config.signature_checking())
        self.assertEqual(config.SIGN_NEVER,
                         my_config.signing_policy())
        self.assertEqual(False, my_config.signature_needed())

    def _get_sample_config(self):
        config_file = StringIO(sample_config_text.encode('utf-8'))
        my_config = config.GlobalConfig()
        my_config._parser = my_config._get_parser(file=config_file)
        return my_config

    def test_gpg_signing_command(self):
        my_config = self._get_sample_config()
        self.assertEqual("gnome-gpg", my_config.gpg_signing_command())
        self.assertEqual(False, my_config.signature_needed())

    def _get_empty_config(self):
        config_file = StringIO("")
        my_config = config.GlobalConfig()
        my_config._parser = my_config._get_parser(file=config_file)
        return my_config

    def test_gpg_signing_command_unset(self):
        my_config = self._get_empty_config()
        self.assertEqual("gpg", my_config.gpg_signing_command())

    def test_get_user_option_default(self):
        my_config = self._get_empty_config()
        self.assertEqual(None, my_config.get_user_option('no_option'))

    def test_get_user_option_global(self):
        my_config = self._get_sample_config()
        self.assertEqual("something",
                         my_config.get_user_option('user_global_option'))
        
    def test_post_commit_default(self):
        my_config = self._get_sample_config()
        self.assertEqual(None, my_config.post_commit())

    def test_configured_logformat(self):
        my_config = self._get_sample_config()
        self.assertEqual("short", my_config.log_format())

    def test_get_alias(self):
        my_config = self._get_sample_config()
        self.assertEqual('help', my_config.get_alias('h'))

    def test_get_no_alias(self):
        my_config = self._get_sample_config()
        self.assertEqual(None, my_config.get_alias('foo'))

    def test_get_long_alias(self):
        my_config = self._get_sample_config()
        self.assertEqual(sample_long_alias, my_config.get_alias('ll'))


class TestLocationConfig(TestCaseInTempDir):

    def test_constructs(self):
        my_config = config.LocationConfig('http://example.com')
        self.assertRaises(TypeError, config.LocationConfig)

    def test_branch_calls_read_filenames(self):
        # This is testing the correct file names are provided.
        # TODO: consolidate with the test for GlobalConfigs filename checks.
        #
        # replace the class that is constructured, to check its parameters
        oldparserclass = config.ConfigObj
        config.ConfigObj = InstrumentedConfigObj
        try:
            my_config = config.LocationConfig('http://www.example.com')
            parser = my_config._get_parser()
        finally:
            config.ConfigObj = oldparserclass
        self.failUnless(isinstance(parser, InstrumentedConfigObj))
        self.assertEqual(parser._calls,
                         [('__init__', config.locations_config_filename(),
                           'utf-8')])
        config.ensure_config_dir_exists()
        #os.mkdir(config.config_dir())
        f = file(config.branches_config_filename(), 'wb')
        f.write('')
        f.close()
        oldparserclass = config.ConfigObj
        config.ConfigObj = InstrumentedConfigObj
        try:
            my_config = config.LocationConfig('http://www.example.com')
            parser = my_config._get_parser()
        finally:
            config.ConfigObj = oldparserclass

    def test_get_global_config(self):
        my_config = config.BranchConfig(FakeBranch('http://example.com'))
        global_config = my_config._get_global_config()
        self.failUnless(isinstance(global_config, config.GlobalConfig))
        self.failUnless(global_config is my_config._get_global_config())

    def test__get_matching_sections_no_match(self):
        self.get_branch_config('/')
        self.assertEqual([], self.my_location_config._get_matching_sections())
        
    def test__get_matching_sections_exact(self):
        self.get_branch_config('http://www.example.com')
        self.assertEqual([('http://www.example.com', '')],
                         self.my_location_config._get_matching_sections())
   
    def test__get_matching_sections_suffix_does_not(self):
        self.get_branch_config('http://www.example.com-com')
        self.assertEqual([], self.my_location_config._get_matching_sections())

    def test__get_matching_sections_subdir_recursive(self):
        self.get_branch_config('http://www.example.com/com')
        self.assertEqual([('http://www.example.com', 'com')],
                         self.my_location_config._get_matching_sections())

    def test__get_matching_sections_ignoreparent(self):
        self.get_branch_config('http://www.example.com/ignoreparent')
        self.assertEqual([('http://www.example.com/ignoreparent', '')],
                         self.my_location_config._get_matching_sections())

    def test__get_matching_sections_ignoreparent_subdir(self):
        self.get_branch_config(
            'http://www.example.com/ignoreparent/childbranch')
        self.assertEqual([('http://www.example.com/ignoreparent', 'childbranch')],
                         self.my_location_config._get_matching_sections())

    def test__get_matching_sections_subdir_trailing_slash(self):
        self.get_branch_config('/b')
        self.assertEqual([('/b/', '')],
                         self.my_location_config._get_matching_sections())

    def test__get_matching_sections_subdir_child(self):
        self.get_branch_config('/a/foo')
        self.assertEqual([('/a/*', ''), ('/a/', 'foo')],
                         self.my_location_config._get_matching_sections())

    def test__get_matching_sections_subdir_child_child(self):
        self.get_branch_config('/a/foo/bar')
        self.assertEqual([('/a/*', 'bar'), ('/a/', 'foo/bar')],
                         self.my_location_config._get_matching_sections())

    def test__get_matching_sections_trailing_slash_with_children(self):
        self.get_branch_config('/a/')
        self.assertEqual([('/a/', '')],
                         self.my_location_config._get_matching_sections())

    def test__get_matching_sections_explicit_over_glob(self):
        # XXX: 2006-09-08 jamesh
        # This test only passes because ord('c') > ord('*').  If there
        # was a config section for '/a/?', it would get precedence
        # over '/a/c'.
        self.get_branch_config('/a/c')
        self.assertEqual([('/a/c', ''), ('/a/*', ''), ('/a/', 'c')],
                         self.my_location_config._get_matching_sections())

    def test__get_option_policy_normal(self):
        self.get_branch_config('http://www.example.com')
        self.assertEqual(
            self.my_location_config._get_config_policy(
            'http://www.example.com', 'normal_option'),
            config.POLICY_NONE)

    def test__get_option_policy_norecurse(self):
        self.get_branch_config('http://www.example.com')
        self.assertEqual(
            self.my_location_config._get_option_policy(
            'http://www.example.com', 'norecurse_option'),
            config.POLICY_NORECURSE)
        # Test old recurse=False setting:
        self.assertEqual(
            self.my_location_config._get_option_policy(
            'http://www.example.com/norecurse', 'normal_option'),
            config.POLICY_NORECURSE)

    def test__get_option_policy_normal(self):
        self.get_branch_config('http://www.example.com')
        self.assertEqual(
            self.my_location_config._get_option_policy(
            'http://www.example.com', 'appendpath_option'),
            config.POLICY_APPENDPATH)

    def test_location_without_username(self):
        self.get_branch_config('http://www.example.com/ignoreparent')
        self.assertEqual(u'Erik B\u00e5gfors <erik@bagfors.nu>',
                         self.my_config.username())

    def test_location_not_listed(self):
        """Test that the global username is used when no location matches"""
        self.get_branch_config('/home/robertc/sources')
        self.assertEqual(u'Erik B\u00e5gfors <erik@bagfors.nu>',
                         self.my_config.username())

    def test_overriding_location(self):
        self.get_branch_config('http://www.example.com/foo')
        self.assertEqual('Robert Collins <robertc@example.org>',
                         self.my_config.username())

    def test_signatures_not_set(self):
        self.get_branch_config('http://www.example.com',
                                 global_config=sample_ignore_signatures)
        self.assertEqual(config.CHECK_ALWAYS,
                         self.my_config.signature_checking())
        self.assertEqual(config.SIGN_NEVER,
                         self.my_config.signing_policy())

    def test_signatures_never(self):
        self.get_branch_config('/a/c')
        self.assertEqual(config.CHECK_NEVER,
                         self.my_config.signature_checking())
        
    def test_signatures_when_available(self):
        self.get_branch_config('/a/', global_config=sample_ignore_signatures)
        self.assertEqual(config.CHECK_IF_POSSIBLE,
                         self.my_config.signature_checking())
        
    def test_signatures_always(self):
        self.get_branch_config('/b')
        self.assertEqual(config.CHECK_ALWAYS,
                         self.my_config.signature_checking())
        
    def test_gpg_signing_command(self):
        self.get_branch_config('/b')
        self.assertEqual("gnome-gpg", self.my_config.gpg_signing_command())

    def test_gpg_signing_command_missing(self):
        self.get_branch_config('/a')
        self.assertEqual("false", self.my_config.gpg_signing_command())

    def test_get_user_option_global(self):
        self.get_branch_config('/a')
        self.assertEqual('something',
                         self.my_config.get_user_option('user_global_option'))

    def test_get_user_option_local(self):
        self.get_branch_config('/a')
        self.assertEqual('local',
                         self.my_config.get_user_option('user_local_option'))

    def test_get_user_option_appendpath(self):
        # returned as is for the base path:
        self.get_branch_config('http://www.example.com')
        self.assertEqual('append',
                         self.my_config.get_user_option('appendpath_option'))
        # Extra path components get appended:
        self.get_branch_config('http://www.example.com/a/b/c')
        self.assertEqual('append/a/b/c',
                         self.my_config.get_user_option('appendpath_option'))
        # Overriden for http://www.example.com/dir, where it is a
        # normal option:
        self.get_branch_config('http://www.example.com/dir/a/b/c')
        self.assertEqual('normal',
                         self.my_config.get_user_option('appendpath_option'))

    def test_get_user_option_norecurse(self):
        self.get_branch_config('http://www.example.com')
        self.assertEqual('norecurse',
                         self.my_config.get_user_option('norecurse_option'))
        self.get_branch_config('http://www.example.com/dir')
        self.assertEqual(None,
                         self.my_config.get_user_option('norecurse_option'))
        # http://www.example.com/norecurse is a recurse=False section
        # that redefines normal_option.  Subdirectories do not pick up
        # this redefinition.
        self.get_branch_config('http://www.example.com/norecurse')
        self.assertEqual('norecurse',
                         self.my_config.get_user_option('normal_option'))
        self.get_branch_config('http://www.example.com/norecurse/subdir')
        self.assertEqual('normal',
                         self.my_config.get_user_option('normal_option'))

    def test_set_user_option_norecurse(self):
        self.get_branch_config('http://www.example.com')
        self.my_config.set_user_option('foo', 'bar',
                                       store=config.STORE_LOCATION_NORECURSE)
        self.assertEqual(
            self.my_location_config._get_option_policy(
            'http://www.example.com', 'foo'),
            config.POLICY_NORECURSE)

    def test_set_user_option_appendpath(self):
        self.get_branch_config('http://www.example.com')
        self.my_config.set_user_option('foo', 'bar',
                                       store=config.STORE_LOCATION_APPENDPATH)
        self.assertEqual(
            self.my_location_config._get_option_policy(
            'http://www.example.com', 'foo'),
            config.POLICY_APPENDPATH)

    def test_set_user_option_change_policy(self):
        self.get_branch_config('http://www.example.com')
        self.my_config.set_user_option('norecurse_option', 'normal',
                                       store=config.STORE_LOCATION)
        self.assertEqual(
            self.my_location_config._get_option_policy(
            'http://www.example.com', 'norecurse_option'),
            config.POLICY_NONE)

    def test_set_user_option_recurse_false_section(self):
        # The following section has recurse=False set
        self.get_branch_config('http://www.example.com/norecurse')
        self.my_config.set_user_option('foo', 'bar',
                                       store=config.STORE_LOCATION)
        self.assertEqual(
            self.my_location_config._get_option_policy(
            'http://www.example.com/norecurse', 'foo'),
            config.POLICY_NONE)
        # The previously existing option is still norecurse:
        self.assertEqual(
            self.my_location_config._get_option_policy(
            'http://www.example.com/norecurse', 'normal_option'),
            config.POLICY_NORECURSE)
        

    def test_post_commit_default(self):
        self.get_branch_config('/a/c')
        self.assertEqual('bzrlib.tests.test_config.post_commit',
                         self.my_config.post_commit())

    def get_branch_config(self, location, global_config=None):
        if global_config is None:
            global_file = StringIO(sample_config_text.encode('utf-8'))
        else:
            global_file = StringIO(global_config.encode('utf-8'))
        branches_file = StringIO(sample_branches_text.encode('utf-8'))
        self.my_config = config.BranchConfig(FakeBranch(location))
        # Force location config to use specified file
        self.my_location_config = self.my_config._get_location_config()
        self.my_location_config._get_parser(branches_file)
        # Force global config to use specified file
        self.my_config._get_global_config()._get_parser(global_file)

    def test_set_user_setting_sets_and_saves(self):
        self.get_branch_config('/a/c')
        record = InstrumentedConfigObj("foo")
        self.my_location_config._parser = record

        real_mkdir = os.mkdir
        self.created = False
        def checked_mkdir(path, mode=0777):
            self.log('making directory: %s', path)
            real_mkdir(path, mode)
            self.created = True

        os.mkdir = checked_mkdir
        try:
            self.my_config.set_user_option('foo', 'bar',
                                           store=config.STORE_LOCATION)
        finally:
            os.mkdir = real_mkdir

        self.failUnless(self.created, 'Failed to create ~/.bazaar')
        self.assertEqual([('__contains__', '/a/c'),
                          ('__contains__', '/a/c/'),
                          ('__setitem__', '/a/c', {}),
                          ('__getitem__', '/a/c'),
                          ('__setitem__', 'foo', 'bar'),
                          ('__getitem__', '/a/c'),
                          ('as_bool', 'recurse'),
                          ('__getitem__', '/a/c'),
                          ('__delitem__', 'recurse'),
                          ('__getitem__', '/a/c'),
                          ('keys',),
                          ('__getitem__', '/a/c'),
                          ('__contains__', 'foo:policy'),
                          ('write',)],
                         record._calls[1:])

    def test_set_user_setting_sets_and_saves2(self):
        self.get_branch_config('/a/c')
        self.assertIs(self.my_config.get_user_option('foo'), None)
        self.my_config.set_user_option('foo', 'bar')
        self.assertEqual(
            self.my_config.branch.control_files.files['branch.conf'], 
            'foo = bar')
        self.assertEqual(self.my_config.get_user_option('foo'), 'bar')
        self.my_config.set_user_option('foo', 'baz',
                                       store=config.STORE_LOCATION)
        self.assertEqual(self.my_config.get_user_option('foo'), 'baz')
        self.my_config.set_user_option('foo', 'qux')
        self.assertEqual(self.my_config.get_user_option('foo'), 'baz')
        

precedence_global = 'option = global'
precedence_branch = 'option = branch'
precedence_location = """
[http://]
recurse = true
option = recurse
[http://example.com/specific]
option = exact
"""


class TestBranchConfigItems(TestCaseInTempDir):

    def get_branch_config(self, global_config=None, location=None, 
                          location_config=None, branch_data_config=None):
        my_config = config.BranchConfig(FakeBranch(location))
        if global_config is not None:
            global_file = StringIO(global_config.encode('utf-8'))
            my_config._get_global_config()._get_parser(global_file)
        self.my_location_config = my_config._get_location_config()
        if location_config is not None:
            location_file = StringIO(location_config.encode('utf-8'))
            self.my_location_config._get_parser(location_file)
        if branch_data_config is not None:
            my_config.branch.control_files.files['branch.conf'] = \
                branch_data_config
        return my_config

    def test_user_id(self):
        branch = FakeBranch(user_id='Robert Collins <robertc@example.net>')
        my_config = config.BranchConfig(branch)
        self.assertEqual("Robert Collins <robertc@example.net>",
                         my_config.username())
        branch.control_files.email = "John"
        my_config.set_user_option('email', 
                                  "Robert Collins <robertc@example.org>")
        self.assertEqual("John", my_config.username())
        branch.control_files.email = None
        self.assertEqual("Robert Collins <robertc@example.org>",
                         my_config.username())

    def test_not_set_in_branch(self):
        my_config = self.get_branch_config(sample_config_text)
        my_config.branch.control_files.email = None
        self.assertEqual(u"Erik B\u00e5gfors <erik@bagfors.nu>",
                         my_config._get_user_id())
        my_config.branch.control_files.email = "John"
        self.assertEqual("John", my_config._get_user_id())

    def test_BZR_EMAIL_OVERRIDES(self):
        os.environ['BZR_EMAIL'] = "Robert Collins <robertc@example.org>"
        branch = FakeBranch()
        my_config = config.BranchConfig(branch)
        self.assertEqual("Robert Collins <robertc@example.org>",
                         my_config.username())
    
    def test_signatures_forced(self):
        my_config = self.get_branch_config(
            global_config=sample_always_signatures)
        self.assertEqual(config.CHECK_NEVER, my_config.signature_checking())
        self.assertEqual(config.SIGN_ALWAYS, my_config.signing_policy())
        self.assertTrue(my_config.signature_needed())

    def test_signatures_forced_branch(self):
        my_config = self.get_branch_config(
            global_config=sample_ignore_signatures,
            branch_data_config=sample_always_signatures)
        self.assertEqual(config.CHECK_NEVER, my_config.signature_checking())
        self.assertEqual(config.SIGN_ALWAYS, my_config.signing_policy())
        self.assertTrue(my_config.signature_needed())

    def test_gpg_signing_command(self):
        my_config = self.get_branch_config(
            # branch data cannot set gpg_signing_command
            branch_data_config="gpg_signing_command=pgp")
        config_file = StringIO(sample_config_text.encode('utf-8'))
        my_config._get_global_config()._get_parser(config_file)
        self.assertEqual('gnome-gpg', my_config.gpg_signing_command())

    def test_get_user_option_global(self):
        branch = FakeBranch()
        my_config = config.BranchConfig(branch)
        config_file = StringIO(sample_config_text.encode('utf-8'))
        (my_config._get_global_config()._get_parser(config_file))
        self.assertEqual('something',
                         my_config.get_user_option('user_global_option'))

    def test_post_commit_default(self):
        branch = FakeBranch()
        my_config = self.get_branch_config(sample_config_text, '/a/c',
                                           sample_branches_text)
        self.assertEqual(my_config.branch.base, '/a/c')
        self.assertEqual('bzrlib.tests.test_config.post_commit',
                         my_config.post_commit())
        my_config.set_user_option('post_commit', 'rmtree_root')
        # post-commit is ignored when bresent in branch data
        self.assertEqual('bzrlib.tests.test_config.post_commit',
                         my_config.post_commit())
        my_config.set_user_option('post_commit', 'rmtree_root',
                                  store=config.STORE_LOCATION)
        self.assertEqual('rmtree_root', my_config.post_commit())

    def test_config_precedence(self):
        my_config = self.get_branch_config(global_config=precedence_global)
        self.assertEqual(my_config.get_user_option('option'), 'global')
        my_config = self.get_branch_config(global_config=precedence_global, 
                                      branch_data_config=precedence_branch)
        self.assertEqual(my_config.get_user_option('option'), 'branch')
        my_config = self.get_branch_config(global_config=precedence_global, 
                                      branch_data_config=precedence_branch,
                                      location_config=precedence_location)
        self.assertEqual(my_config.get_user_option('option'), 'recurse')
        my_config = self.get_branch_config(global_config=precedence_global, 
                                      branch_data_config=precedence_branch,
                                      location_config=precedence_location,
                                      location='http://example.com/specific')
        self.assertEqual(my_config.get_user_option('option'), 'exact')


class TestMailAddressExtraction(TestCase):

    def test_extract_email_address(self):
        self.assertEqual('jane@test.com',
                         config.extract_email_address('Jane <jane@test.com>'))
        self.assertRaises(errors.NoEmailInUsername,
                          config.extract_email_address, 'Jane Tester')
