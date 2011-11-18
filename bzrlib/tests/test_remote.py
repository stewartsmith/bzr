# Copyright (C) 2006-2011 Canonical Ltd
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

"""Tests for remote bzrdir/branch/repo/etc

These are proxy objects which act on remote objects by sending messages
through a smart client.  The proxies are to be created when attempting to open
the object given a transport that supports smartserver rpc operations.

These tests correspond to tests.test_smart, which exercises the server side.
"""

import bz2
from cStringIO import StringIO

from bzrlib import (
    branch,
    bzrdir,
    config,
    controldir,
    errors,
    graph as _mod_graph,
    inventory,
    inventory_delta,
    remote,
    repository,
    tests,
    transport,
    treebuilder,
    versionedfile,
    )
from bzrlib.branch import Branch
from bzrlib.bzrdir import (
    BzrDir,
    BzrDirFormat,
    RemoteBzrProber,
    )
from bzrlib.remote import (
    RemoteBranch,
    RemoteBranchFormat,
    RemoteBzrDir,
    RemoteBzrDirFormat,
    RemoteRepository,
    RemoteRepositoryFormat,
    )
from bzrlib.repofmt import groupcompress_repo, knitpack_repo
from bzrlib.revision import NULL_REVISION
from bzrlib.smart import medium, request
from bzrlib.smart.client import _SmartClient
from bzrlib.smart.repository import (
    SmartServerRepositoryGetParentMap,
    SmartServerRepositoryGetStream_1_19,
    )
from bzrlib.symbol_versioning import deprecated_in
from bzrlib.tests import (
    test_server,
    )
from bzrlib.tests.scenarios import load_tests_apply_scenarios
from bzrlib.transport.memory import MemoryTransport
from bzrlib.transport.remote import (
    RemoteTransport,
    RemoteSSHTransport,
    RemoteTCPTransport,
    )


load_tests = load_tests_apply_scenarios


class BasicRemoteObjectTests(tests.TestCaseWithTransport):

    scenarios = [
        ('HPSS-v2',
            {'transport_server': test_server.SmartTCPServer_for_testing_v2_only}),
        ('HPSS-v3',
            {'transport_server': test_server.SmartTCPServer_for_testing})]


    def setUp(self):
        super(BasicRemoteObjectTests, self).setUp()
        self.transport = self.get_transport()
        # make a branch that can be opened over the smart transport
        self.local_wt = BzrDir.create_standalone_workingtree('.')
        self.addCleanup(self.transport.disconnect)

    def test_create_remote_bzrdir(self):
        b = remote.RemoteBzrDir(self.transport, RemoteBzrDirFormat())
        self.assertIsInstance(b, BzrDir)

    def test_open_remote_branch(self):
        # open a standalone branch in the working directory
        b = remote.RemoteBzrDir(self.transport, RemoteBzrDirFormat())
        branch = b.open_branch()
        self.assertIsInstance(branch, Branch)

    def test_remote_repository(self):
        b = BzrDir.open_from_transport(self.transport)
        repo = b.open_repository()
        revid = u'\xc823123123'.encode('utf8')
        self.assertFalse(repo.has_revision(revid))
        self.local_wt.commit(message='test commit', rev_id=revid)
        self.assertTrue(repo.has_revision(revid))

    def test_remote_branch_revision_history(self):
        b = BzrDir.open_from_transport(self.transport).open_branch()
        self.assertEqual([],
            self.applyDeprecated(deprecated_in((2, 5, 0)), b.revision_history))
        r1 = self.local_wt.commit('1st commit')
        r2 = self.local_wt.commit('1st commit', rev_id=u'\xc8'.encode('utf8'))
        self.assertEqual([r1, r2],
            self.applyDeprecated(deprecated_in((2, 5, 0)), b.revision_history))

    def test_find_correct_format(self):
        """Should open a RemoteBzrDir over a RemoteTransport"""
        fmt = BzrDirFormat.find_format(self.transport)
        self.assertTrue(bzrdir.RemoteBzrProber
                        in controldir.ControlDirFormat._server_probers)
        self.assertIsInstance(fmt, RemoteBzrDirFormat)

    def test_open_detected_smart_format(self):
        fmt = BzrDirFormat.find_format(self.transport)
        d = fmt.open(self.transport)
        self.assertIsInstance(d, BzrDir)

    def test_remote_branch_repr(self):
        b = BzrDir.open_from_transport(self.transport).open_branch()
        self.assertStartsWith(str(b), 'RemoteBranch(')

    def test_remote_bzrdir_repr(self):
        b = BzrDir.open_from_transport(self.transport)
        self.assertStartsWith(str(b), 'RemoteBzrDir(')

    def test_remote_branch_format_supports_stacking(self):
        t = self.transport
        self.make_branch('unstackable', format='pack-0.92')
        b = BzrDir.open_from_transport(t.clone('unstackable')).open_branch()
        self.assertFalse(b._format.supports_stacking())
        self.make_branch('stackable', format='1.9')
        b = BzrDir.open_from_transport(t.clone('stackable')).open_branch()
        self.assertTrue(b._format.supports_stacking())

    def test_remote_repo_format_supports_external_references(self):
        t = self.transport
        bd = self.make_bzrdir('unstackable', format='pack-0.92')
        r = bd.create_repository()
        self.assertFalse(r._format.supports_external_lookups)
        r = BzrDir.open_from_transport(t.clone('unstackable')).open_repository()
        self.assertFalse(r._format.supports_external_lookups)
        bd = self.make_bzrdir('stackable', format='1.9')
        r = bd.create_repository()
        self.assertTrue(r._format.supports_external_lookups)
        r = BzrDir.open_from_transport(t.clone('stackable')).open_repository()
        self.assertTrue(r._format.supports_external_lookups)

    def test_remote_branch_set_append_revisions_only(self):
        # Make a format 1.9 branch, which supports append_revisions_only
        branch = self.make_branch('branch', format='1.9')
        config = branch.get_config()
        branch.set_append_revisions_only(True)
        self.assertEqual(
            'True', config.get_user_option('append_revisions_only'))
        branch.set_append_revisions_only(False)
        self.assertEqual(
            'False', config.get_user_option('append_revisions_only'))

    def test_remote_branch_set_append_revisions_only_upgrade_reqd(self):
        branch = self.make_branch('branch', format='knit')
        config = branch.get_config()
        self.assertRaises(
            errors.UpgradeRequired, branch.set_append_revisions_only, True)


class FakeProtocol(object):
    """Lookalike SmartClientRequestProtocolOne allowing body reading tests."""

    def __init__(self, body, fake_client):
        self.body = body
        self._body_buffer = None
        self._fake_client = fake_client

    def read_body_bytes(self, count=-1):
        if self._body_buffer is None:
            self._body_buffer = StringIO(self.body)
        bytes = self._body_buffer.read(count)
        if self._body_buffer.tell() == len(self._body_buffer.getvalue()):
            self._fake_client.expecting_body = False
        return bytes

    def cancel_read_body(self):
        self._fake_client.expecting_body = False

    def read_streamed_body(self):
        return self.body


class FakeClient(_SmartClient):
    """Lookalike for _SmartClient allowing testing."""

    def __init__(self, fake_medium_base='fake base'):
        """Create a FakeClient."""
        self.responses = []
        self._calls = []
        self.expecting_body = False
        # if non-None, this is the list of expected calls, with only the
        # method name and arguments included.  the body might be hard to
        # compute so is not included. If a call is None, that call can
        # be anything.
        self._expected_calls = None
        _SmartClient.__init__(self, FakeMedium(self._calls, fake_medium_base))

    def add_expected_call(self, call_name, call_args, response_type,
        response_args, response_body=None):
        if self._expected_calls is None:
            self._expected_calls = []
        self._expected_calls.append((call_name, call_args))
        self.responses.append((response_type, response_args, response_body))

    def add_success_response(self, *args):
        self.responses.append(('success', args, None))

    def add_success_response_with_body(self, body, *args):
        self.responses.append(('success', args, body))
        if self._expected_calls is not None:
            self._expected_calls.append(None)

    def add_error_response(self, *args):
        self.responses.append(('error', args))

    def add_unknown_method_response(self, verb):
        self.responses.append(('unknown', verb))

    def finished_test(self):
        if self._expected_calls:
            raise AssertionError("%r finished but was still expecting %r"
                % (self, self._expected_calls[0]))

    def _get_next_response(self):
        try:
            response_tuple = self.responses.pop(0)
        except IndexError, e:
            raise AssertionError("%r didn't expect any more calls"
                % (self,))
        if response_tuple[0] == 'unknown':
            raise errors.UnknownSmartMethod(response_tuple[1])
        elif response_tuple[0] == 'error':
            raise errors.ErrorFromSmartServer(response_tuple[1])
        return response_tuple

    def _check_call(self, method, args):
        if self._expected_calls is None:
            # the test should be updated to say what it expects
            return
        try:
            next_call = self._expected_calls.pop(0)
        except IndexError:
            raise AssertionError("%r didn't expect any more calls "
                "but got %r%r"
                % (self, method, args,))
        if next_call is None:
            return
        if method != next_call[0] or args != next_call[1]:
            raise AssertionError("%r expected %r%r "
                "but got %r%r"
                % (self, next_call[0], next_call[1], method, args,))

    def call(self, method, *args):
        self._check_call(method, args)
        self._calls.append(('call', method, args))
        return self._get_next_response()[1]

    def call_expecting_body(self, method, *args):
        self._check_call(method, args)
        self._calls.append(('call_expecting_body', method, args))
        result = self._get_next_response()
        self.expecting_body = True
        return result[1], FakeProtocol(result[2], self)

    def call_with_body_bytes(self, method, args, body):
        self._check_call(method, args)
        self._calls.append(('call_with_body_bytes', method, args, body))
        result = self._get_next_response()
        return result[1], FakeProtocol(result[2], self)

    def call_with_body_bytes_expecting_body(self, method, args, body):
        self._check_call(method, args)
        self._calls.append(('call_with_body_bytes_expecting_body', method,
            args, body))
        result = self._get_next_response()
        self.expecting_body = True
        return result[1], FakeProtocol(result[2], self)

    def call_with_body_stream(self, args, stream):
        # Explicitly consume the stream before checking for an error, because
        # that's what happens a real medium.
        stream = list(stream)
        self._check_call(args[0], args[1:])
        self._calls.append(('call_with_body_stream', args[0], args[1:], stream))
        result = self._get_next_response()
        # The second value returned from call_with_body_stream is supposed to
        # be a response_handler object, but so far no tests depend on that.
        response_handler = None 
        return result[1], response_handler


class FakeMedium(medium.SmartClientMedium):

    def __init__(self, client_calls, base):
        medium.SmartClientMedium.__init__(self, base)
        self._client_calls = client_calls

    def disconnect(self):
        self._client_calls.append(('disconnect medium',))


class TestVfsHas(tests.TestCase):

    def test_unicode_path(self):
        client = FakeClient('/')
        client.add_success_response('yes',)
        transport = RemoteTransport('bzr://localhost/', _client=client)
        filename = u'/hell\u00d8'.encode('utf8')
        result = transport.has(filename)
        self.assertEqual(
            [('call', 'has', (filename,))],
            client._calls)
        self.assertTrue(result)


class TestRemote(tests.TestCaseWithMemoryTransport):

    def get_branch_format(self):
        reference_bzrdir_format = bzrdir.format_registry.get('default')()
        return reference_bzrdir_format.get_branch_format()

    def get_repo_format(self):
        reference_bzrdir_format = bzrdir.format_registry.get('default')()
        return reference_bzrdir_format.repository_format

    def assertFinished(self, fake_client):
        """Assert that all of a FakeClient's expected calls have occurred."""
        fake_client.finished_test()


class Test_ClientMedium_remote_path_from_transport(tests.TestCase):
    """Tests for the behaviour of client_medium.remote_path_from_transport."""

    def assertRemotePath(self, expected, client_base, transport_base):
        """Assert that the result of
        SmartClientMedium.remote_path_from_transport is the expected value for
        a given client_base and transport_base.
        """
        client_medium = medium.SmartClientMedium(client_base)
        t = transport.get_transport(transport_base)
        result = client_medium.remote_path_from_transport(t)
        self.assertEqual(expected, result)

    def test_remote_path_from_transport(self):
        """SmartClientMedium.remote_path_from_transport calculates a URL for
        the given transport relative to the root of the client base URL.
        """
        self.assertRemotePath('xyz/', 'bzr://host/path', 'bzr://host/xyz')
        self.assertRemotePath(
            'path/xyz/', 'bzr://host/path', 'bzr://host/path/xyz')

    def assertRemotePathHTTP(self, expected, transport_base, relpath):
        """Assert that the result of
        HttpTransportBase.remote_path_from_transport is the expected value for
        a given transport_base and relpath of that transport.  (Note that
        HttpTransportBase is a subclass of SmartClientMedium)
        """
        base_transport = transport.get_transport(transport_base)
        client_medium = base_transport.get_smart_medium()
        cloned_transport = base_transport.clone(relpath)
        result = client_medium.remote_path_from_transport(cloned_transport)
        self.assertEqual(expected, result)

    def test_remote_path_from_transport_http(self):
        """Remote paths for HTTP transports are calculated differently to other
        transports.  They are just relative to the client base, not the root
        directory of the host.
        """
        for scheme in ['http:', 'https:', 'bzr+http:', 'bzr+https:']:
            self.assertRemotePathHTTP(
                '../xyz/', scheme + '//host/path', '../xyz/')
            self.assertRemotePathHTTP(
                'xyz/', scheme + '//host/path', 'xyz/')


class Test_ClientMedium_remote_is_at_least(tests.TestCase):
    """Tests for the behaviour of client_medium.remote_is_at_least."""

    def test_initially_unlimited(self):
        """A fresh medium assumes that the remote side supports all
        versions.
        """
        client_medium = medium.SmartClientMedium('dummy base')
        self.assertFalse(client_medium._is_remote_before((99, 99)))

    def test__remember_remote_is_before(self):
        """Calling _remember_remote_is_before ratchets down the known remote
        version.
        """
        client_medium = medium.SmartClientMedium('dummy base')
        # Mark the remote side as being less than 1.6.  The remote side may
        # still be 1.5.
        client_medium._remember_remote_is_before((1, 6))
        self.assertTrue(client_medium._is_remote_before((1, 6)))
        self.assertFalse(client_medium._is_remote_before((1, 5)))
        # Calling _remember_remote_is_before again with a lower value works.
        client_medium._remember_remote_is_before((1, 5))
        self.assertTrue(client_medium._is_remote_before((1, 5)))
        # If you call _remember_remote_is_before with a higher value it logs a
        # warning, and continues to remember the lower value.
        self.assertNotContainsRe(self.get_log(), '_remember_remote_is_before')
        client_medium._remember_remote_is_before((1, 9))
        self.assertContainsRe(self.get_log(), '_remember_remote_is_before')
        self.assertTrue(client_medium._is_remote_before((1, 5)))


class TestBzrDirCloningMetaDir(TestRemote):

    def test_backwards_compat(self):
        self.setup_smart_server_with_call_log()
        a_dir = self.make_bzrdir('.')
        self.reset_smart_call_log()
        verb = 'BzrDir.cloning_metadir'
        self.disable_verb(verb)
        format = a_dir.cloning_metadir()
        call_count = len([call for call in self.hpss_calls if
            call.call.method == verb])
        self.assertEqual(1, call_count)

    def test_branch_reference(self):
        transport = self.get_transport('quack')
        referenced = self.make_branch('referenced')
        expected = referenced.bzrdir.cloning_metadir()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'BzrDir.cloning_metadir', ('quack/', 'False'),
            'error', ('BranchReference',)),
        client.add_expected_call(
            'BzrDir.open_branchV3', ('quack/',),
            'success', ('ref', self.get_url('referenced'))),
        a_bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        result = a_bzrdir.cloning_metadir()
        # We should have got a control dir matching the referenced branch.
        self.assertEqual(bzrdir.BzrDirMetaFormat1, type(result))
        self.assertEqual(expected._repository_format, result._repository_format)
        self.assertEqual(expected._branch_format, result._branch_format)
        self.assertFinished(client)

    def test_current_server(self):
        transport = self.get_transport('.')
        transport = transport.clone('quack')
        self.make_bzrdir('quack')
        client = FakeClient(transport.base)
        reference_bzrdir_format = bzrdir.format_registry.get('default')()
        control_name = reference_bzrdir_format.network_name()
        client.add_expected_call(
            'BzrDir.cloning_metadir', ('quack/', 'False'),
            'success', (control_name, '', ('branch', ''))),
        a_bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        result = a_bzrdir.cloning_metadir()
        # We should have got a reference control dir with default branch and
        # repository formats.
        # This pokes a little, just to be sure.
        self.assertEqual(bzrdir.BzrDirMetaFormat1, type(result))
        self.assertEqual(None, result._repository_format)
        self.assertEqual(None, result._branch_format)
        self.assertFinished(client)


class TestBzrDirHasWorkingTree(TestRemote):

    def test_has_workingtree(self):
        transport = self.get_transport('quack')
        client = FakeClient(transport.base)
        client.add_expected_call(
            'BzrDir.has_workingtree', ('quack/',),
            'success', ('yes',)),
        a_bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        self.assertTrue(a_bzrdir.has_workingtree())
        self.assertFinished(client)

    def test_no_workingtree(self):
        transport = self.get_transport('quack')
        client = FakeClient(transport.base)
        client.add_expected_call(
            'BzrDir.has_workingtree', ('quack/',),
            'success', ('no',)),
        a_bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        self.assertFalse(a_bzrdir.has_workingtree())
        self.assertFinished(client)


class TestBzrDirDestroyRepository(TestRemote):

    def test_destroy_repository(self):
        transport = self.get_transport('quack')
        client = FakeClient(transport.base)
        client.add_expected_call(
            'BzrDir.destroy_repository', ('quack/',),
            'success', ('ok',)),
        a_bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        a_bzrdir.destroy_repository()
        self.assertFinished(client)


class TestBzrDirOpen(TestRemote):

    def make_fake_client_and_transport(self, path='quack'):
        transport = MemoryTransport()
        transport.mkdir(path)
        transport = transport.clone(path)
        client = FakeClient(transport.base)
        return client, transport

    def test_absent(self):
        client, transport = self.make_fake_client_and_transport()
        client.add_expected_call(
            'BzrDir.open_2.1', ('quack/',), 'success', ('no',))
        self.assertRaises(errors.NotBranchError, RemoteBzrDir, transport,
                RemoteBzrDirFormat(), _client=client, _force_probe=True)
        self.assertFinished(client)

    def test_present_without_workingtree(self):
        client, transport = self.make_fake_client_and_transport()
        client.add_expected_call(
            'BzrDir.open_2.1', ('quack/',), 'success', ('yes', 'no'))
        bd = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client, _force_probe=True)
        self.assertIsInstance(bd, RemoteBzrDir)
        self.assertFalse(bd.has_workingtree())
        self.assertRaises(errors.NoWorkingTree, bd.open_workingtree)
        self.assertFinished(client)

    def test_present_with_workingtree(self):
        client, transport = self.make_fake_client_and_transport()
        client.add_expected_call(
            'BzrDir.open_2.1', ('quack/',), 'success', ('yes', 'yes'))
        bd = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client, _force_probe=True)
        self.assertIsInstance(bd, RemoteBzrDir)
        self.assertTrue(bd.has_workingtree())
        self.assertRaises(errors.NotLocalUrl, bd.open_workingtree)
        self.assertFinished(client)

    def test_backwards_compat(self):
        client, transport = self.make_fake_client_and_transport()
        client.add_expected_call(
            'BzrDir.open_2.1', ('quack/',), 'unknown', ('BzrDir.open_2.1',))
        client.add_expected_call(
            'BzrDir.open', ('quack/',), 'success', ('yes',))
        bd = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client, _force_probe=True)
        self.assertIsInstance(bd, RemoteBzrDir)
        self.assertFinished(client)

    def test_backwards_compat_hpss_v2(self):
        client, transport = self.make_fake_client_and_transport()
        # Monkey-patch fake client to simulate real-world behaviour with v2
        # server: upon first RPC call detect the protocol version, and because
        # the version is 2 also do _remember_remote_is_before((1, 6)) before
        # continuing with the RPC.
        orig_check_call = client._check_call
        def check_call(method, args):
            client._medium._protocol_version = 2
            client._medium._remember_remote_is_before((1, 6))
            client._check_call = orig_check_call
            client._check_call(method, args)
        client._check_call = check_call
        client.add_expected_call(
            'BzrDir.open_2.1', ('quack/',), 'unknown', ('BzrDir.open_2.1',))
        client.add_expected_call(
            'BzrDir.open', ('quack/',), 'success', ('yes',))
        bd = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client, _force_probe=True)
        self.assertIsInstance(bd, RemoteBzrDir)
        self.assertFinished(client)


class TestBzrDirOpenBranch(TestRemote):

    def test_backwards_compat(self):
        self.setup_smart_server_with_call_log()
        self.make_branch('.')
        a_dir = BzrDir.open(self.get_url('.'))
        self.reset_smart_call_log()
        verb = 'BzrDir.open_branchV3'
        self.disable_verb(verb)
        format = a_dir.open_branch()
        call_count = len([call for call in self.hpss_calls if
            call.call.method == verb])
        self.assertEqual(1, call_count)

    def test_branch_present(self):
        reference_format = self.get_repo_format()
        network_name = reference_format.network_name()
        branch_network_name = self.get_branch_format().network_name()
        transport = MemoryTransport()
        transport.mkdir('quack')
        transport = transport.clone('quack')
        client = FakeClient(transport.base)
        client.add_expected_call(
            'BzrDir.open_branchV3', ('quack/',),
            'success', ('branch', branch_network_name))
        client.add_expected_call(
            'BzrDir.find_repositoryV3', ('quack/',),
            'success', ('ok', '', 'no', 'no', 'no', network_name))
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('quack/',),
            'error', ('NotStacked',))
        bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        result = bzrdir.open_branch()
        self.assertIsInstance(result, RemoteBranch)
        self.assertEqual(bzrdir, result.bzrdir)
        self.assertFinished(client)

    def test_branch_missing(self):
        transport = MemoryTransport()
        transport.mkdir('quack')
        transport = transport.clone('quack')
        client = FakeClient(transport.base)
        client.add_error_response('nobranch')
        bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        self.assertRaises(errors.NotBranchError, bzrdir.open_branch)
        self.assertEqual(
            [('call', 'BzrDir.open_branchV3', ('quack/',))],
            client._calls)

    def test__get_tree_branch(self):
        # _get_tree_branch is a form of open_branch, but it should only ask for
        # branch opening, not any other network requests.
        calls = []
        def open_branch(name=None):
            calls.append("Called")
            return "a-branch"
        transport = MemoryTransport()
        # no requests on the network - catches other api calls being made.
        client = FakeClient(transport.base)
        bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        # patch the open_branch call to record that it was called.
        bzrdir.open_branch = open_branch
        self.assertEqual((None, "a-branch"), bzrdir._get_tree_branch())
        self.assertEqual(["Called"], calls)
        self.assertEqual([], client._calls)

    def test_url_quoting_of_path(self):
        # Relpaths on the wire should not be URL-escaped.  So "~" should be
        # transmitted as "~", not "%7E".
        transport = RemoteTCPTransport('bzr://localhost/~hello/')
        client = FakeClient(transport.base)
        reference_format = self.get_repo_format()
        network_name = reference_format.network_name()
        branch_network_name = self.get_branch_format().network_name()
        client.add_expected_call(
            'BzrDir.open_branchV3', ('~hello/',),
            'success', ('branch', branch_network_name))
        client.add_expected_call(
            'BzrDir.find_repositoryV3', ('~hello/',),
            'success', ('ok', '', 'no', 'no', 'no', network_name))
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('~hello/',),
            'error', ('NotStacked',))
        bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        result = bzrdir.open_branch()
        self.assertFinished(client)

    def check_open_repository(self, rich_root, subtrees, external_lookup='no'):
        reference_format = self.get_repo_format()
        network_name = reference_format.network_name()
        transport = MemoryTransport()
        transport.mkdir('quack')
        transport = transport.clone('quack')
        if rich_root:
            rich_response = 'yes'
        else:
            rich_response = 'no'
        if subtrees:
            subtree_response = 'yes'
        else:
            subtree_response = 'no'
        client = FakeClient(transport.base)
        client.add_success_response(
            'ok', '', rich_response, subtree_response, external_lookup,
            network_name)
        bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        result = bzrdir.open_repository()
        self.assertEqual(
            [('call', 'BzrDir.find_repositoryV3', ('quack/',))],
            client._calls)
        self.assertIsInstance(result, RemoteRepository)
        self.assertEqual(bzrdir, result.bzrdir)
        self.assertEqual(rich_root, result._format.rich_root_data)
        self.assertEqual(subtrees, result._format.supports_tree_reference)

    def test_open_repository_sets_format_attributes(self):
        self.check_open_repository(True, True)
        self.check_open_repository(False, True)
        self.check_open_repository(True, False)
        self.check_open_repository(False, False)
        self.check_open_repository(False, False, 'yes')

    def test_old_server(self):
        """RemoteBzrDirFormat should fail to probe if the server version is too
        old.
        """
        self.assertRaises(errors.NotBranchError,
            RemoteBzrProber.probe_transport, OldServerTransport())


class TestBzrDirCreateBranch(TestRemote):

    def test_backwards_compat(self):
        self.setup_smart_server_with_call_log()
        repo = self.make_repository('.')
        self.reset_smart_call_log()
        self.disable_verb('BzrDir.create_branch')
        branch = repo.bzrdir.create_branch()
        create_branch_call_count = len([call for call in self.hpss_calls if
            call.call.method == 'BzrDir.create_branch'])
        self.assertEqual(1, create_branch_call_count)

    def test_current_server(self):
        transport = self.get_transport('.')
        transport = transport.clone('quack')
        self.make_repository('quack')
        client = FakeClient(transport.base)
        reference_bzrdir_format = bzrdir.format_registry.get('default')()
        reference_format = reference_bzrdir_format.get_branch_format()
        network_name = reference_format.network_name()
        reference_repo_fmt = reference_bzrdir_format.repository_format
        reference_repo_name = reference_repo_fmt.network_name()
        client.add_expected_call(
            'BzrDir.create_branch', ('quack/', network_name),
            'success', ('ok', network_name, '', 'no', 'no', 'yes',
            reference_repo_name))
        a_bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        branch = a_bzrdir.create_branch()
        # We should have got a remote branch
        self.assertIsInstance(branch, remote.RemoteBranch)
        # its format should have the settings from the response
        format = branch._format
        self.assertEqual(network_name, format.network_name())

    def test_already_open_repo_and_reused_medium(self):
        """Bug 726584: create_branch(..., repository=repo) should work
        regardless of what the smart medium's base URL is.
        """
        self.transport_server = test_server.SmartTCPServer_for_testing
        transport = self.get_transport('.')
        repo = self.make_repository('quack')
        # Client's medium rooted a transport root (not at the bzrdir)
        client = FakeClient(transport.base)
        transport = transport.clone('quack')
        reference_bzrdir_format = bzrdir.format_registry.get('default')()
        reference_format = reference_bzrdir_format.get_branch_format()
        network_name = reference_format.network_name()
        reference_repo_fmt = reference_bzrdir_format.repository_format
        reference_repo_name = reference_repo_fmt.network_name()
        client.add_expected_call(
            'BzrDir.create_branch', ('extra/quack/', network_name),
            'success', ('ok', network_name, '', 'no', 'no', 'yes',
            reference_repo_name))
        a_bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        branch = a_bzrdir.create_branch(repository=repo)
        # We should have got a remote branch
        self.assertIsInstance(branch, remote.RemoteBranch)
        # its format should have the settings from the response
        format = branch._format
        self.assertEqual(network_name, format.network_name())


class TestBzrDirCreateRepository(TestRemote):

    def test_backwards_compat(self):
        self.setup_smart_server_with_call_log()
        bzrdir = self.make_bzrdir('.')
        self.reset_smart_call_log()
        self.disable_verb('BzrDir.create_repository')
        repo = bzrdir.create_repository()
        create_repo_call_count = len([call for call in self.hpss_calls if
            call.call.method == 'BzrDir.create_repository'])
        self.assertEqual(1, create_repo_call_count)

    def test_current_server(self):
        transport = self.get_transport('.')
        transport = transport.clone('quack')
        self.make_bzrdir('quack')
        client = FakeClient(transport.base)
        reference_bzrdir_format = bzrdir.format_registry.get('default')()
        reference_format = reference_bzrdir_format.repository_format
        network_name = reference_format.network_name()
        client.add_expected_call(
            'BzrDir.create_repository', ('quack/',
                'Bazaar repository format 2a (needs bzr 1.16 or later)\n',
                'False'),
            'success', ('ok', 'yes', 'yes', 'yes', network_name))
        a_bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        repo = a_bzrdir.create_repository()
        # We should have got a remote repository
        self.assertIsInstance(repo, remote.RemoteRepository)
        # its format should have the settings from the response
        format = repo._format
        self.assertTrue(format.rich_root_data)
        self.assertTrue(format.supports_tree_reference)
        self.assertTrue(format.supports_external_lookups)
        self.assertEqual(network_name, format.network_name())


class TestBzrDirOpenRepository(TestRemote):

    def test_backwards_compat_1_2_3(self):
        # fallback all the way to the first version.
        reference_format = self.get_repo_format()
        network_name = reference_format.network_name()
        server_url = 'bzr://example.com/'
        self.permit_url(server_url)
        client = FakeClient(server_url)
        client.add_unknown_method_response('BzrDir.find_repositoryV3')
        client.add_unknown_method_response('BzrDir.find_repositoryV2')
        client.add_success_response('ok', '', 'no', 'no')
        # A real repository instance will be created to determine the network
        # name.
        client.add_success_response_with_body(
            "Bazaar-NG meta directory, format 1\n", 'ok')
        client.add_success_response_with_body(
            reference_format.get_format_string(), 'ok')
        # PackRepository wants to do a stat
        client.add_success_response('stat', '0', '65535')
        remote_transport = RemoteTransport(server_url + 'quack/', medium=False,
            _client=client)
        bzrdir = RemoteBzrDir(remote_transport, RemoteBzrDirFormat(),
            _client=client)
        repo = bzrdir.open_repository()
        self.assertEqual(
            [('call', 'BzrDir.find_repositoryV3', ('quack/',)),
             ('call', 'BzrDir.find_repositoryV2', ('quack/',)),
             ('call', 'BzrDir.find_repository', ('quack/',)),
             ('call_expecting_body', 'get', ('/quack/.bzr/branch-format',)),
             ('call_expecting_body', 'get', ('/quack/.bzr/repository/format',)),
             ('call', 'stat', ('/quack/.bzr/repository',)),
             ],
            client._calls)
        self.assertEqual(network_name, repo._format.network_name())

    def test_backwards_compat_2(self):
        # fallback to find_repositoryV2
        reference_format = self.get_repo_format()
        network_name = reference_format.network_name()
        server_url = 'bzr://example.com/'
        self.permit_url(server_url)
        client = FakeClient(server_url)
        client.add_unknown_method_response('BzrDir.find_repositoryV3')
        client.add_success_response('ok', '', 'no', 'no', 'no')
        # A real repository instance will be created to determine the network
        # name.
        client.add_success_response_with_body(
            "Bazaar-NG meta directory, format 1\n", 'ok')
        client.add_success_response_with_body(
            reference_format.get_format_string(), 'ok')
        # PackRepository wants to do a stat
        client.add_success_response('stat', '0', '65535')
        remote_transport = RemoteTransport(server_url + 'quack/', medium=False,
            _client=client)
        bzrdir = RemoteBzrDir(remote_transport, RemoteBzrDirFormat(),
            _client=client)
        repo = bzrdir.open_repository()
        self.assertEqual(
            [('call', 'BzrDir.find_repositoryV3', ('quack/',)),
             ('call', 'BzrDir.find_repositoryV2', ('quack/',)),
             ('call_expecting_body', 'get', ('/quack/.bzr/branch-format',)),
             ('call_expecting_body', 'get', ('/quack/.bzr/repository/format',)),
             ('call', 'stat', ('/quack/.bzr/repository',)),
             ],
            client._calls)
        self.assertEqual(network_name, repo._format.network_name())

    def test_current_server(self):
        reference_format = self.get_repo_format()
        network_name = reference_format.network_name()
        transport = MemoryTransport()
        transport.mkdir('quack')
        transport = transport.clone('quack')
        client = FakeClient(transport.base)
        client.add_success_response('ok', '', 'no', 'no', 'no', network_name)
        bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        repo = bzrdir.open_repository()
        self.assertEqual(
            [('call', 'BzrDir.find_repositoryV3', ('quack/',))],
            client._calls)
        self.assertEqual(network_name, repo._format.network_name())


class TestBzrDirFormatInitializeEx(TestRemote):

    def test_success(self):
        """Simple test for typical successful call."""
        fmt = RemoteBzrDirFormat()
        default_format_name = BzrDirFormat.get_default_format().network_name()
        transport = self.get_transport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'BzrDirFormat.initialize_ex_1.16',
                (default_format_name, 'path', 'False', 'False', 'False', '',
                 '', '', '', 'False'),
            'success',
                ('.', 'no', 'no', 'yes', 'repo fmt', 'repo bzrdir fmt',
                 'bzrdir fmt', 'False', '', '', 'repo lock token'))
        # XXX: It would be better to call fmt.initialize_on_transport_ex, but
        # it's currently hard to test that without supplying a real remote
        # transport connected to a real server.
        result = fmt._initialize_on_transport_ex_rpc(client, 'path',
            transport, False, False, False, None, None, None, None, False)
        self.assertFinished(client)

    def test_error(self):
        """Error responses are translated, e.g. 'PermissionDenied' raises the
        corresponding error from the client.
        """
        fmt = RemoteBzrDirFormat()
        default_format_name = BzrDirFormat.get_default_format().network_name()
        transport = self.get_transport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'BzrDirFormat.initialize_ex_1.16',
                (default_format_name, 'path', 'False', 'False', 'False', '',
                 '', '', '', 'False'),
            'error',
                ('PermissionDenied', 'path', 'extra info'))
        # XXX: It would be better to call fmt.initialize_on_transport_ex, but
        # it's currently hard to test that without supplying a real remote
        # transport connected to a real server.
        err = self.assertRaises(errors.PermissionDenied,
            fmt._initialize_on_transport_ex_rpc, client, 'path', transport,
            False, False, False, None, None, None, None, False)
        self.assertEqual('path', err.path)
        self.assertEqual(': extra info', err.extra)
        self.assertFinished(client)

    def test_error_from_real_server(self):
        """Integration test for error translation."""
        transport = self.make_smart_server('foo')
        transport = transport.clone('no-such-path')
        fmt = RemoteBzrDirFormat()
        err = self.assertRaises(errors.NoSuchFile,
            fmt.initialize_on_transport_ex, transport, create_prefix=False)


class OldSmartClient(object):
    """A fake smart client for test_old_version that just returns a version one
    response to the 'hello' (query version) command.
    """

    def get_request(self):
        input_file = StringIO('ok\x011\n')
        output_file = StringIO()
        client_medium = medium.SmartSimplePipesClientMedium(
            input_file, output_file)
        return medium.SmartClientStreamMediumRequest(client_medium)

    def protocol_version(self):
        return 1


class OldServerTransport(object):
    """A fake transport for test_old_server that reports it's smart server
    protocol version as version one.
    """

    def __init__(self):
        self.base = 'fake:'

    def get_smart_client(self):
        return OldSmartClient()


class RemoteBzrDirTestCase(TestRemote):

    def make_remote_bzrdir(self, transport, client):
        """Make a RemotebzrDir using 'client' as the _client."""
        return RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)


class RemoteBranchTestCase(RemoteBzrDirTestCase):

    def lock_remote_branch(self, branch):
        """Trick a RemoteBranch into thinking it is locked."""
        branch._lock_mode = 'w'
        branch._lock_count = 2
        branch._lock_token = 'branch token'
        branch._repo_lock_token = 'repo token'
        branch.repository._lock_mode = 'w'
        branch.repository._lock_count = 2
        branch.repository._lock_token = 'repo token'

    def make_remote_branch(self, transport, client):
        """Make a RemoteBranch using 'client' as its _SmartClient.

        A RemoteBzrDir and RemoteRepository will also be created to fill out
        the RemoteBranch, albeit with stub values for some of their attributes.
        """
        # we do not want bzrdir to make any remote calls, so use False as its
        # _client.  If it tries to make a remote call, this will fail
        # immediately.
        bzrdir = self.make_remote_bzrdir(transport, False)
        repo = RemoteRepository(bzrdir, None, _client=client)
        branch_format = self.get_branch_format()
        format = RemoteBranchFormat(network_name=branch_format.network_name())
        return RemoteBranch(bzrdir, repo, _client=client, format=format)


class TestBranchGetParent(RemoteBranchTestCase):

    def test_no_parent(self):
        # in an empty branch we decode the response properly
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('quack/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.get_parent', ('quack/',),
            'success', ('',))
        transport.mkdir('quack')
        transport = transport.clone('quack')
        branch = self.make_remote_branch(transport, client)
        result = branch.get_parent()
        self.assertFinished(client)
        self.assertEqual(None, result)

    def test_parent_relative(self):
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('kwaak/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.get_parent', ('kwaak/',),
            'success', ('../foo/',))
        transport.mkdir('kwaak')
        transport = transport.clone('kwaak')
        branch = self.make_remote_branch(transport, client)
        result = branch.get_parent()
        self.assertEqual(transport.clone('../foo').base, result)

    def test_parent_absolute(self):
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('kwaak/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.get_parent', ('kwaak/',),
            'success', ('http://foo/',))
        transport.mkdir('kwaak')
        transport = transport.clone('kwaak')
        branch = self.make_remote_branch(transport, client)
        result = branch.get_parent()
        self.assertEqual('http://foo/', result)
        self.assertFinished(client)


class TestBranchSetParentLocation(RemoteBranchTestCase):

    def test_no_parent(self):
        # We call the verb when setting parent to None
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('quack/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.set_parent_location', ('quack/', 'b', 'r', ''),
            'success', ())
        transport.mkdir('quack')
        transport = transport.clone('quack')
        branch = self.make_remote_branch(transport, client)
        branch._lock_token = 'b'
        branch._repo_lock_token = 'r'
        branch._set_parent_location(None)
        self.assertFinished(client)

    def test_parent(self):
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('kwaak/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.set_parent_location', ('kwaak/', 'b', 'r', 'foo'),
            'success', ())
        transport.mkdir('kwaak')
        transport = transport.clone('kwaak')
        branch = self.make_remote_branch(transport, client)
        branch._lock_token = 'b'
        branch._repo_lock_token = 'r'
        branch._set_parent_location('foo')
        self.assertFinished(client)

    def test_backwards_compat(self):
        self.setup_smart_server_with_call_log()
        branch = self.make_branch('.')
        self.reset_smart_call_log()
        verb = 'Branch.set_parent_location'
        self.disable_verb(verb)
        branch.set_parent('http://foo/')
        self.assertLength(12, self.hpss_calls)


class TestBranchGetTagsBytes(RemoteBranchTestCase):

    def test_backwards_compat(self):
        self.setup_smart_server_with_call_log()
        branch = self.make_branch('.')
        self.reset_smart_call_log()
        verb = 'Branch.get_tags_bytes'
        self.disable_verb(verb)
        branch.tags.get_tag_dict()
        call_count = len([call for call in self.hpss_calls if
            call.call.method == verb])
        self.assertEqual(1, call_count)

    def test_trivial(self):
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('quack/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.get_tags_bytes', ('quack/',),
            'success', ('',))
        transport.mkdir('quack')
        transport = transport.clone('quack')
        branch = self.make_remote_branch(transport, client)
        result = branch.tags.get_tag_dict()
        self.assertFinished(client)
        self.assertEqual({}, result)


class TestBranchSetTagsBytes(RemoteBranchTestCase):

    def test_trivial(self):
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('quack/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.set_tags_bytes', ('quack/', 'branch token', 'repo token'),
            'success', ('',))
        transport.mkdir('quack')
        transport = transport.clone('quack')
        branch = self.make_remote_branch(transport, client)
        self.lock_remote_branch(branch)
        branch._set_tags_bytes('tags bytes')
        self.assertFinished(client)
        self.assertEqual('tags bytes', client._calls[-1][-1])

    def test_backwards_compatible(self):
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('quack/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.set_tags_bytes', ('quack/', 'branch token', 'repo token'),
            'unknown', ('Branch.set_tags_bytes',))
        transport.mkdir('quack')
        transport = transport.clone('quack')
        branch = self.make_remote_branch(transport, client)
        self.lock_remote_branch(branch)
        class StubRealBranch(object):
            def __init__(self):
                self.calls = []
            def _set_tags_bytes(self, bytes):
                self.calls.append(('set_tags_bytes', bytes))
        real_branch = StubRealBranch()
        branch._real_branch = real_branch
        branch._set_tags_bytes('tags bytes')
        # Call a second time, to exercise the 'remote version already inferred'
        # code path.
        branch._set_tags_bytes('tags bytes')
        self.assertFinished(client)
        self.assertEqual(
            [('set_tags_bytes', 'tags bytes')] * 2, real_branch.calls)


class TestBranchHeadsToFetch(RemoteBranchTestCase):

    def test_uses_last_revision_info_and_tags_by_default(self):
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('quack/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.last_revision_info', ('quack/',),
            'success', ('ok', '1', 'rev-tip'))
        client.add_expected_call(
            'Branch.get_config_file', ('quack/',),
            'success', ('ok',), '')
        transport.mkdir('quack')
        transport = transport.clone('quack')
        branch = self.make_remote_branch(transport, client)
        result = branch.heads_to_fetch()
        self.assertFinished(client)
        self.assertEqual((set(['rev-tip']), set()), result)

    def test_uses_last_revision_info_and_tags_when_set(self):
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('quack/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.last_revision_info', ('quack/',),
            'success', ('ok', '1', 'rev-tip'))
        client.add_expected_call(
            'Branch.get_config_file', ('quack/',),
            'success', ('ok',), 'branch.fetch_tags = True')
        # XXX: this will break if the default format's serialization of tags
        # changes, or if the RPC for fetching tags changes from get_tags_bytes.
        client.add_expected_call(
            'Branch.get_tags_bytes', ('quack/',),
            'success', ('d5:tag-17:rev-foo5:tag-27:rev-bare',))
        transport.mkdir('quack')
        transport = transport.clone('quack')
        branch = self.make_remote_branch(transport, client)
        result = branch.heads_to_fetch()
        self.assertFinished(client)
        self.assertEqual(
            (set(['rev-tip']), set(['rev-foo', 'rev-bar'])), result)

    def test_uses_rpc_for_formats_with_non_default_heads_to_fetch(self):
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('quack/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.heads_to_fetch', ('quack/',),
            'success', (['tip'], ['tagged-1', 'tagged-2']))
        transport.mkdir('quack')
        transport = transport.clone('quack')
        branch = self.make_remote_branch(transport, client)
        branch._format._use_default_local_heads_to_fetch = lambda: False
        result = branch.heads_to_fetch()
        self.assertFinished(client)
        self.assertEqual((set(['tip']), set(['tagged-1', 'tagged-2'])), result)

    def make_branch_with_tags(self):
        self.setup_smart_server_with_call_log()
        # Make a branch with a single revision.
        builder = self.make_branch_builder('foo')
        builder.start_series()
        builder.build_snapshot('tip', None, [
            ('add', ('', 'root-id', 'directory', ''))])
        builder.finish_series()
        branch = builder.get_branch()
        # Add two tags to that branch
        branch.tags.set_tag('tag-1', 'rev-1')
        branch.tags.set_tag('tag-2', 'rev-2')
        return branch

    def test_backwards_compatible(self):
        branch = self.make_branch_with_tags()
        c = branch.get_config()
        c.set_user_option('branch.fetch_tags', 'True')
        self.addCleanup(branch.lock_read().unlock)
        # Disable the heads_to_fetch verb
        verb = 'Branch.heads_to_fetch'
        self.disable_verb(verb)
        self.reset_smart_call_log()
        result = branch.heads_to_fetch()
        self.assertEqual((set(['tip']), set(['rev-1', 'rev-2'])), result)
        self.assertEqual(
            ['Branch.last_revision_info', 'Branch.get_config_file',
             'Branch.get_tags_bytes'],
            [call.call.method for call in self.hpss_calls])

    def test_backwards_compatible_no_tags(self):
        branch = self.make_branch_with_tags()
        c = branch.get_config()
        c.set_user_option('branch.fetch_tags', 'False')
        self.addCleanup(branch.lock_read().unlock)
        # Disable the heads_to_fetch verb
        verb = 'Branch.heads_to_fetch'
        self.disable_verb(verb)
        self.reset_smart_call_log()
        result = branch.heads_to_fetch()
        self.assertEqual((set(['tip']), set()), result)
        self.assertEqual(
            ['Branch.last_revision_info', 'Branch.get_config_file'],
            [call.call.method for call in self.hpss_calls])


class TestBranchLastRevisionInfo(RemoteBranchTestCase):

    def test_empty_branch(self):
        # in an empty branch we decode the response properly
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('quack/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.last_revision_info', ('quack/',),
            'success', ('ok', '0', 'null:'))
        transport.mkdir('quack')
        transport = transport.clone('quack')
        branch = self.make_remote_branch(transport, client)
        result = branch.last_revision_info()
        self.assertFinished(client)
        self.assertEqual((0, NULL_REVISION), result)

    def test_non_empty_branch(self):
        # in a non-empty branch we also decode the response properly
        revid = u'\xc8'.encode('utf8')
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('kwaak/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.last_revision_info', ('kwaak/',),
            'success', ('ok', '2', revid))
        transport.mkdir('kwaak')
        transport = transport.clone('kwaak')
        branch = self.make_remote_branch(transport, client)
        result = branch.last_revision_info()
        self.assertEqual((2, revid), result)


class TestBranch_get_stacked_on_url(TestRemote):
    """Test Branch._get_stacked_on_url rpc"""

    def test_get_stacked_on_invalid_url(self):
        # test that asking for a stacked on url the server can't access works.
        # This isn't perfect, but then as we're in the same process there
        # really isn't anything we can do to be 100% sure that the server
        # doesn't just open in - this test probably needs to be rewritten using
        # a spawn()ed server.
        stacked_branch = self.make_branch('stacked', format='1.9')
        memory_branch = self.make_branch('base', format='1.9')
        vfs_url = self.get_vfs_only_url('base')
        stacked_branch.set_stacked_on_url(vfs_url)
        transport = stacked_branch.bzrdir.root_transport
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('stacked/',),
            'success', ('ok', vfs_url))
        # XXX: Multiple calls are bad, this second call documents what is
        # today.
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('stacked/',),
            'success', ('ok', vfs_url))
        bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=client)
        repo_fmt = remote.RemoteRepositoryFormat()
        repo_fmt._custom_format = stacked_branch.repository._format
        branch = RemoteBranch(bzrdir, RemoteRepository(bzrdir, repo_fmt),
            _client=client)
        result = branch.get_stacked_on_url()
        self.assertEqual(vfs_url, result)

    def test_backwards_compatible(self):
        # like with bzr1.6 with no Branch.get_stacked_on_url rpc
        base_branch = self.make_branch('base', format='1.6')
        stacked_branch = self.make_branch('stacked', format='1.6')
        stacked_branch.set_stacked_on_url('../base')
        client = FakeClient(self.get_url())
        branch_network_name = self.get_branch_format().network_name()
        client.add_expected_call(
            'BzrDir.open_branchV3', ('stacked/',),
            'success', ('branch', branch_network_name))
        client.add_expected_call(
            'BzrDir.find_repositoryV3', ('stacked/',),
            'success', ('ok', '', 'no', 'no', 'yes',
                stacked_branch.repository._format.network_name()))
        # called twice, once from constructor and then again by us
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('stacked/',),
            'unknown', ('Branch.get_stacked_on_url',))
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('stacked/',),
            'unknown', ('Branch.get_stacked_on_url',))
        # this will also do vfs access, but that goes direct to the transport
        # and isn't seen by the FakeClient.
        bzrdir = RemoteBzrDir(self.get_transport('stacked'),
            RemoteBzrDirFormat(), _client=client)
        branch = bzrdir.open_branch()
        result = branch.get_stacked_on_url()
        self.assertEqual('../base', result)
        self.assertFinished(client)
        # it's in the fallback list both for the RemoteRepository and its vfs
        # repository
        self.assertEqual(1, len(branch.repository._fallback_repositories))
        self.assertEqual(1,
            len(branch.repository._real_repository._fallback_repositories))

    def test_get_stacked_on_real_branch(self):
        base_branch = self.make_branch('base')
        stacked_branch = self.make_branch('stacked')
        stacked_branch.set_stacked_on_url('../base')
        reference_format = self.get_repo_format()
        network_name = reference_format.network_name()
        client = FakeClient(self.get_url())
        branch_network_name = self.get_branch_format().network_name()
        client.add_expected_call(
            'BzrDir.open_branchV3', ('stacked/',),
            'success', ('branch', branch_network_name))
        client.add_expected_call(
            'BzrDir.find_repositoryV3', ('stacked/',),
            'success', ('ok', '', 'yes', 'no', 'yes', network_name))
        # called twice, once from constructor and then again by us
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('stacked/',),
            'success', ('ok', '../base'))
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('stacked/',),
            'success', ('ok', '../base'))
        bzrdir = RemoteBzrDir(self.get_transport('stacked'),
            RemoteBzrDirFormat(), _client=client)
        branch = bzrdir.open_branch()
        result = branch.get_stacked_on_url()
        self.assertEqual('../base', result)
        self.assertFinished(client)
        # it's in the fallback list both for the RemoteRepository.
        self.assertEqual(1, len(branch.repository._fallback_repositories))
        # And we haven't had to construct a real repository.
        self.assertEqual(None, branch.repository._real_repository)


class TestBranchSetLastRevision(RemoteBranchTestCase):

    def test_set_empty(self):
        # _set_last_revision_info('null:') is translated to calling
        # Branch.set_last_revision(path, '') on the wire.
        transport = MemoryTransport()
        transport.mkdir('branch')
        transport = transport.clone('branch')

        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('branch/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.lock_write', ('branch/', '', ''),
            'success', ('ok', 'branch token', 'repo token'))
        client.add_expected_call(
            'Branch.last_revision_info',
            ('branch/',),
            'success', ('ok', '0', 'null:'))
        client.add_expected_call(
            'Branch.set_last_revision', ('branch/', 'branch token', 'repo token', 'null:',),
            'success', ('ok',))
        client.add_expected_call(
            'Branch.unlock', ('branch/', 'branch token', 'repo token'),
            'success', ('ok',))
        branch = self.make_remote_branch(transport, client)
        # This is a hack to work around the problem that RemoteBranch currently
        # unnecessarily invokes _ensure_real upon a call to lock_write.
        branch._ensure_real = lambda: None
        branch.lock_write()
        result = branch._set_last_revision(NULL_REVISION)
        branch.unlock()
        self.assertEqual(None, result)
        self.assertFinished(client)

    def test_set_nonempty(self):
        # set_last_revision_info(N, rev-idN) is translated to calling
        # Branch.set_last_revision(path, rev-idN) on the wire.
        transport = MemoryTransport()
        transport.mkdir('branch')
        transport = transport.clone('branch')

        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('branch/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.lock_write', ('branch/', '', ''),
            'success', ('ok', 'branch token', 'repo token'))
        client.add_expected_call(
            'Branch.last_revision_info',
            ('branch/',),
            'success', ('ok', '0', 'null:'))
        lines = ['rev-id2']
        encoded_body = bz2.compress('\n'.join(lines))
        client.add_success_response_with_body(encoded_body, 'ok')
        client.add_expected_call(
            'Branch.set_last_revision', ('branch/', 'branch token', 'repo token', 'rev-id2',),
            'success', ('ok',))
        client.add_expected_call(
            'Branch.unlock', ('branch/', 'branch token', 'repo token'),
            'success', ('ok',))
        branch = self.make_remote_branch(transport, client)
        # This is a hack to work around the problem that RemoteBranch currently
        # unnecessarily invokes _ensure_real upon a call to lock_write.
        branch._ensure_real = lambda: None
        # Lock the branch, reset the record of remote calls.
        branch.lock_write()
        result = branch._set_last_revision('rev-id2')
        branch.unlock()
        self.assertEqual(None, result)
        self.assertFinished(client)

    def test_no_such_revision(self):
        transport = MemoryTransport()
        transport.mkdir('branch')
        transport = transport.clone('branch')
        # A response of 'NoSuchRevision' is translated into an exception.
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('branch/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.lock_write', ('branch/', '', ''),
            'success', ('ok', 'branch token', 'repo token'))
        client.add_expected_call(
            'Branch.last_revision_info',
            ('branch/',),
            'success', ('ok', '0', 'null:'))
        # get_graph calls to construct the revision history, for the set_rh
        # hook
        lines = ['rev-id']
        encoded_body = bz2.compress('\n'.join(lines))
        client.add_success_response_with_body(encoded_body, 'ok')
        client.add_expected_call(
            'Branch.set_last_revision', ('branch/', 'branch token', 'repo token', 'rev-id',),
            'error', ('NoSuchRevision', 'rev-id'))
        client.add_expected_call(
            'Branch.unlock', ('branch/', 'branch token', 'repo token'),
            'success', ('ok',))

        branch = self.make_remote_branch(transport, client)
        branch.lock_write()
        self.assertRaises(
            errors.NoSuchRevision, branch._set_last_revision, 'rev-id')
        branch.unlock()
        self.assertFinished(client)

    def test_tip_change_rejected(self):
        """TipChangeRejected responses cause a TipChangeRejected exception to
        be raised.
        """
        transport = MemoryTransport()
        transport.mkdir('branch')
        transport = transport.clone('branch')
        client = FakeClient(transport.base)
        rejection_msg_unicode = u'rejection message\N{INTERROBANG}'
        rejection_msg_utf8 = rejection_msg_unicode.encode('utf8')
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('branch/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.lock_write', ('branch/', '', ''),
            'success', ('ok', 'branch token', 'repo token'))
        client.add_expected_call(
            'Branch.last_revision_info',
            ('branch/',),
            'success', ('ok', '0', 'null:'))
        lines = ['rev-id']
        encoded_body = bz2.compress('\n'.join(lines))
        client.add_success_response_with_body(encoded_body, 'ok')
        client.add_expected_call(
            'Branch.set_last_revision', ('branch/', 'branch token', 'repo token', 'rev-id',),
            'error', ('TipChangeRejected', rejection_msg_utf8))
        client.add_expected_call(
            'Branch.unlock', ('branch/', 'branch token', 'repo token'),
            'success', ('ok',))
        branch = self.make_remote_branch(transport, client)
        branch._ensure_real = lambda: None
        branch.lock_write()
        # The 'TipChangeRejected' error response triggered by calling
        # set_last_revision_info causes a TipChangeRejected exception.
        err = self.assertRaises(
            errors.TipChangeRejected,
            branch._set_last_revision, 'rev-id')
        # The UTF-8 message from the response has been decoded into a unicode
        # object.
        self.assertIsInstance(err.msg, unicode)
        self.assertEqual(rejection_msg_unicode, err.msg)
        branch.unlock()
        self.assertFinished(client)


class TestBranchSetLastRevisionInfo(RemoteBranchTestCase):

    def test_set_last_revision_info(self):
        # set_last_revision_info(num, 'rev-id') is translated to calling
        # Branch.set_last_revision_info(num, 'rev-id') on the wire.
        transport = MemoryTransport()
        transport.mkdir('branch')
        transport = transport.clone('branch')
        client = FakeClient(transport.base)
        # get_stacked_on_url
        client.add_error_response('NotStacked')
        # lock_write
        client.add_success_response('ok', 'branch token', 'repo token')
        # query the current revision
        client.add_success_response('ok', '0', 'null:')
        # set_last_revision
        client.add_success_response('ok')
        # unlock
        client.add_success_response('ok')

        branch = self.make_remote_branch(transport, client)
        # Lock the branch, reset the record of remote calls.
        branch.lock_write()
        client._calls = []
        result = branch.set_last_revision_info(1234, 'a-revision-id')
        self.assertEqual(
            [('call', 'Branch.last_revision_info', ('branch/',)),
             ('call', 'Branch.set_last_revision_info',
                ('branch/', 'branch token', 'repo token',
                 '1234', 'a-revision-id'))],
            client._calls)
        self.assertEqual(None, result)

    def test_no_such_revision(self):
        # A response of 'NoSuchRevision' is translated into an exception.
        transport = MemoryTransport()
        transport.mkdir('branch')
        transport = transport.clone('branch')
        client = FakeClient(transport.base)
        # get_stacked_on_url
        client.add_error_response('NotStacked')
        # lock_write
        client.add_success_response('ok', 'branch token', 'repo token')
        # set_last_revision
        client.add_error_response('NoSuchRevision', 'revid')
        # unlock
        client.add_success_response('ok')

        branch = self.make_remote_branch(transport, client)
        # Lock the branch, reset the record of remote calls.
        branch.lock_write()
        client._calls = []

        self.assertRaises(
            errors.NoSuchRevision, branch.set_last_revision_info, 123, 'revid')
        branch.unlock()

    def test_backwards_compatibility(self):
        """If the server does not support the Branch.set_last_revision_info
        verb (which is new in 1.4), then the client falls back to VFS methods.
        """
        # This test is a little messy.  Unlike most tests in this file, it
        # doesn't purely test what a Remote* object sends over the wire, and
        # how it reacts to responses from the wire.  It instead relies partly
        # on asserting that the RemoteBranch will call
        # self._real_branch.set_last_revision_info(...).

        # First, set up our RemoteBranch with a FakeClient that raises
        # UnknownSmartMethod, and a StubRealBranch that logs how it is called.
        transport = MemoryTransport()
        transport.mkdir('branch')
        transport = transport.clone('branch')
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('branch/',),
            'error', ('NotStacked',))
        client.add_expected_call(
            'Branch.last_revision_info',
            ('branch/',),
            'success', ('ok', '0', 'null:'))
        client.add_expected_call(
            'Branch.set_last_revision_info',
            ('branch/', 'branch token', 'repo token', '1234', 'a-revision-id',),
            'unknown', 'Branch.set_last_revision_info')

        branch = self.make_remote_branch(transport, client)
        class StubRealBranch(object):
            def __init__(self):
                self.calls = []
            def set_last_revision_info(self, revno, revision_id):
                self.calls.append(
                    ('set_last_revision_info', revno, revision_id))
            def _clear_cached_state(self):
                pass
        real_branch = StubRealBranch()
        branch._real_branch = real_branch
        self.lock_remote_branch(branch)

        # Call set_last_revision_info, and verify it behaved as expected.
        result = branch.set_last_revision_info(1234, 'a-revision-id')
        self.assertEqual(
            [('set_last_revision_info', 1234, 'a-revision-id')],
            real_branch.calls)
        self.assertFinished(client)

    def test_unexpected_error(self):
        # If the server sends an error the client doesn't understand, it gets
        # turned into an UnknownErrorFromSmartServer, which is presented as a
        # non-internal error to the user.
        transport = MemoryTransport()
        transport.mkdir('branch')
        transport = transport.clone('branch')
        client = FakeClient(transport.base)
        # get_stacked_on_url
        client.add_error_response('NotStacked')
        # lock_write
        client.add_success_response('ok', 'branch token', 'repo token')
        # set_last_revision
        client.add_error_response('UnexpectedError')
        # unlock
        client.add_success_response('ok')

        branch = self.make_remote_branch(transport, client)
        # Lock the branch, reset the record of remote calls.
        branch.lock_write()
        client._calls = []

        err = self.assertRaises(
            errors.UnknownErrorFromSmartServer,
            branch.set_last_revision_info, 123, 'revid')
        self.assertEqual(('UnexpectedError',), err.error_tuple)
        branch.unlock()

    def test_tip_change_rejected(self):
        """TipChangeRejected responses cause a TipChangeRejected exception to
        be raised.
        """
        transport = MemoryTransport()
        transport.mkdir('branch')
        transport = transport.clone('branch')
        client = FakeClient(transport.base)
        # get_stacked_on_url
        client.add_error_response('NotStacked')
        # lock_write
        client.add_success_response('ok', 'branch token', 'repo token')
        # set_last_revision
        client.add_error_response('TipChangeRejected', 'rejection message')
        # unlock
        client.add_success_response('ok')

        branch = self.make_remote_branch(transport, client)
        # Lock the branch, reset the record of remote calls.
        branch.lock_write()
        self.addCleanup(branch.unlock)
        client._calls = []

        # The 'TipChangeRejected' error response triggered by calling
        # set_last_revision_info causes a TipChangeRejected exception.
        err = self.assertRaises(
            errors.TipChangeRejected,
            branch.set_last_revision_info, 123, 'revid')
        self.assertEqual('rejection message', err.msg)


class TestBranchGetSetConfig(RemoteBranchTestCase):

    def test_get_branch_conf(self):
        # in an empty branch we decode the response properly
        client = FakeClient()
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('memory:///',),
            'error', ('NotStacked',),)
        client.add_success_response_with_body('# config file body', 'ok')
        transport = MemoryTransport()
        branch = self.make_remote_branch(transport, client)
        config = branch.get_config()
        config.has_explicit_nickname()
        self.assertEqual(
            [('call', 'Branch.get_stacked_on_url', ('memory:///',)),
             ('call_expecting_body', 'Branch.get_config_file', ('memory:///',))],
            client._calls)

    def test_get_multi_line_branch_conf(self):
        # Make sure that multiple-line branch.conf files are supported
        #
        # https://bugs.launchpad.net/bzr/+bug/354075
        client = FakeClient()
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('memory:///',),
            'error', ('NotStacked',),)
        client.add_success_response_with_body('a = 1\nb = 2\nc = 3\n', 'ok')
        transport = MemoryTransport()
        branch = self.make_remote_branch(transport, client)
        config = branch.get_config()
        self.assertEqual(u'2', config.get_user_option('b'))

    def test_set_option(self):
        client = FakeClient()
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('memory:///',),
            'error', ('NotStacked',),)
        client.add_expected_call(
            'Branch.lock_write', ('memory:///', '', ''),
            'success', ('ok', 'branch token', 'repo token'))
        client.add_expected_call(
            'Branch.set_config_option', ('memory:///', 'branch token',
            'repo token', 'foo', 'bar', ''),
            'success', ())
        client.add_expected_call(
            'Branch.unlock', ('memory:///', 'branch token', 'repo token'),
            'success', ('ok',))
        transport = MemoryTransport()
        branch = self.make_remote_branch(transport, client)
        branch.lock_write()
        config = branch._get_config()
        config.set_option('foo', 'bar')
        branch.unlock()
        self.assertFinished(client)

    def test_set_option_with_dict(self):
        client = FakeClient()
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('memory:///',),
            'error', ('NotStacked',),)
        client.add_expected_call(
            'Branch.lock_write', ('memory:///', '', ''),
            'success', ('ok', 'branch token', 'repo token'))
        encoded_dict_value = 'd5:ascii1:a11:unicode \xe2\x8c\x9a3:\xe2\x80\xbde'
        client.add_expected_call(
            'Branch.set_config_option_dict', ('memory:///', 'branch token',
            'repo token', encoded_dict_value, 'foo', ''),
            'success', ())
        client.add_expected_call(
            'Branch.unlock', ('memory:///', 'branch token', 'repo token'),
            'success', ('ok',))
        transport = MemoryTransport()
        branch = self.make_remote_branch(transport, client)
        branch.lock_write()
        config = branch._get_config()
        config.set_option(
            {'ascii': 'a', u'unicode \N{WATCH}': u'\N{INTERROBANG}'},
            'foo')
        branch.unlock()
        self.assertFinished(client)

    def test_backwards_compat_set_option(self):
        self.setup_smart_server_with_call_log()
        branch = self.make_branch('.')
        verb = 'Branch.set_config_option'
        self.disable_verb(verb)
        branch.lock_write()
        self.addCleanup(branch.unlock)
        self.reset_smart_call_log()
        branch._get_config().set_option('value', 'name')
        self.assertLength(10, self.hpss_calls)
        self.assertEqual('value', branch._get_config().get_option('name'))

    def test_backwards_compat_set_option_with_dict(self):
        self.setup_smart_server_with_call_log()
        branch = self.make_branch('.')
        verb = 'Branch.set_config_option_dict'
        self.disable_verb(verb)
        branch.lock_write()
        self.addCleanup(branch.unlock)
        self.reset_smart_call_log()
        config = branch._get_config()
        value_dict = {'ascii': 'a', u'unicode \N{WATCH}': u'\N{INTERROBANG}'}
        config.set_option(value_dict, 'name')
        self.assertLength(10, self.hpss_calls)
        self.assertEqual(value_dict, branch._get_config().get_option('name'))


class TestBranchLockWrite(RemoteBranchTestCase):

    def test_lock_write_unlockable(self):
        transport = MemoryTransport()
        client = FakeClient(transport.base)
        client.add_expected_call(
            'Branch.get_stacked_on_url', ('quack/',),
            'error', ('NotStacked',),)
        client.add_expected_call(
            'Branch.lock_write', ('quack/', '', ''),
            'error', ('UnlockableTransport',))
        transport.mkdir('quack')
        transport = transport.clone('quack')
        branch = self.make_remote_branch(transport, client)
        self.assertRaises(errors.UnlockableTransport, branch.lock_write)
        self.assertFinished(client)


class TestBzrDirGetSetConfig(RemoteBzrDirTestCase):

    def test__get_config(self):
        client = FakeClient()
        client.add_success_response_with_body('default_stack_on = /\n', 'ok')
        transport = MemoryTransport()
        bzrdir = self.make_remote_bzrdir(transport, client)
        config = bzrdir.get_config()
        self.assertEqual('/', config.get_default_stack_on())
        self.assertEqual(
            [('call_expecting_body', 'BzrDir.get_config_file', ('memory:///',))],
            client._calls)

    def test_set_option_uses_vfs(self):
        self.setup_smart_server_with_call_log()
        bzrdir = self.make_bzrdir('.')
        self.reset_smart_call_log()
        config = bzrdir.get_config()
        config.set_default_stack_on('/')
        self.assertLength(3, self.hpss_calls)

    def test_backwards_compat_get_option(self):
        self.setup_smart_server_with_call_log()
        bzrdir = self.make_bzrdir('.')
        verb = 'BzrDir.get_config_file'
        self.disable_verb(verb)
        self.reset_smart_call_log()
        self.assertEqual(None,
            bzrdir._get_config().get_option('default_stack_on'))
        self.assertLength(3, self.hpss_calls)


class TestTransportIsReadonly(tests.TestCase):

    def test_true(self):
        client = FakeClient()
        client.add_success_response('yes')
        transport = RemoteTransport('bzr://example.com/', medium=False,
                                    _client=client)
        self.assertEqual(True, transport.is_readonly())
        self.assertEqual(
            [('call', 'Transport.is_readonly', ())],
            client._calls)

    def test_false(self):
        client = FakeClient()
        client.add_success_response('no')
        transport = RemoteTransport('bzr://example.com/', medium=False,
                                    _client=client)
        self.assertEqual(False, transport.is_readonly())
        self.assertEqual(
            [('call', 'Transport.is_readonly', ())],
            client._calls)

    def test_error_from_old_server(self):
        """bzr 0.15 and earlier servers don't recognise the is_readonly verb.

        Clients should treat it as a "no" response, because is_readonly is only
        advisory anyway (a transport could be read-write, but then the
        underlying filesystem could be readonly anyway).
        """
        client = FakeClient()
        client.add_unknown_method_response('Transport.is_readonly')
        transport = RemoteTransport('bzr://example.com/', medium=False,
                                    _client=client)
        self.assertEqual(False, transport.is_readonly())
        self.assertEqual(
            [('call', 'Transport.is_readonly', ())],
            client._calls)


class TestTransportMkdir(tests.TestCase):

    def test_permissiondenied(self):
        client = FakeClient()
        client.add_error_response('PermissionDenied', 'remote path', 'extra')
        transport = RemoteTransport('bzr://example.com/', medium=False,
                                    _client=client)
        exc = self.assertRaises(
            errors.PermissionDenied, transport.mkdir, 'client path')
        expected_error = errors.PermissionDenied('/client path', 'extra')
        self.assertEqual(expected_error, exc)


class TestRemoteSSHTransportAuthentication(tests.TestCaseInTempDir):

    def test_defaults_to_none(self):
        t = RemoteSSHTransport('bzr+ssh://example.com')
        self.assertIs(None, t._get_credentials()[0])

    def test_uses_authentication_config(self):
        conf = config.AuthenticationConfig()
        conf._get_config().update(
            {'bzr+sshtest': {'scheme': 'ssh', 'user': 'bar', 'host':
            'example.com'}})
        conf._save()
        t = RemoteSSHTransport('bzr+ssh://example.com')
        self.assertEqual('bar', t._get_credentials()[0])


class TestRemoteRepository(TestRemote):
    """Base for testing RemoteRepository protocol usage.

    These tests contain frozen requests and responses.  We want any changes to
    what is sent or expected to be require a thoughtful update to these tests
    because they might break compatibility with different-versioned servers.
    """

    def setup_fake_client_and_repository(self, transport_path):
        """Create the fake client and repository for testing with.

        There's no real server here; we just have canned responses sent
        back one by one.

        :param transport_path: Path below the root of the MemoryTransport
            where the repository will be created.
        """
        transport = MemoryTransport()
        transport.mkdir(transport_path)
        client = FakeClient(transport.base)
        transport = transport.clone(transport_path)
        # we do not want bzrdir to make any remote calls
        bzrdir = RemoteBzrDir(transport, RemoteBzrDirFormat(),
            _client=False)
        repo = RemoteRepository(bzrdir, None, _client=client)
        return repo, client


def remoted_description(format):
    return 'Remote: ' + format.get_format_description()


class TestBranchFormat(tests.TestCase):

    def test_get_format_description(self):
        remote_format = RemoteBranchFormat()
        real_format = branch.format_registry.get_default()
        remote_format._network_name = real_format.network_name()
        self.assertEqual(remoted_description(real_format),
            remote_format.get_format_description())


class TestRepositoryFormat(TestRemoteRepository):

    def test_fast_delta(self):
        true_name = groupcompress_repo.RepositoryFormat2a().network_name()
        true_format = RemoteRepositoryFormat()
        true_format._network_name = true_name
        self.assertEqual(True, true_format.fast_deltas)
        false_name = knitpack_repo.RepositoryFormatKnitPack1().network_name()
        false_format = RemoteRepositoryFormat()
        false_format._network_name = false_name
        self.assertEqual(False, false_format.fast_deltas)

    def test_get_format_description(self):
        remote_repo_format = RemoteRepositoryFormat()
        real_format = repository.format_registry.get_default()
        remote_repo_format._network_name = real_format.network_name()
        self.assertEqual(remoted_description(real_format),
            remote_repo_format.get_format_description())


class TestRepositoryGatherStats(TestRemoteRepository):

    def test_revid_none(self):
        # ('ok',), body with revisions and size
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response_with_body(
            'revisions: 2\nsize: 18\n', 'ok')
        result = repo.gather_stats(None)
        self.assertEqual(
            [('call_expecting_body', 'Repository.gather_stats',
             ('quack/','','no'))],
            client._calls)
        self.assertEqual({'revisions': 2, 'size': 18}, result)

    def test_revid_no_committers(self):
        # ('ok',), body without committers
        body = ('firstrev: 123456.300 3600\n'
                'latestrev: 654231.400 0\n'
                'revisions: 2\n'
                'size: 18\n')
        transport_path = 'quick'
        revid = u'\xc8'.encode('utf8')
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response_with_body(body, 'ok')
        result = repo.gather_stats(revid)
        self.assertEqual(
            [('call_expecting_body', 'Repository.gather_stats',
              ('quick/', revid, 'no'))],
            client._calls)
        self.assertEqual({'revisions': 2, 'size': 18,
                          'firstrev': (123456.300, 3600),
                          'latestrev': (654231.400, 0),},
                         result)

    def test_revid_with_committers(self):
        # ('ok',), body with committers
        body = ('committers: 128\n'
                'firstrev: 123456.300 3600\n'
                'latestrev: 654231.400 0\n'
                'revisions: 2\n'
                'size: 18\n')
        transport_path = 'buick'
        revid = u'\xc8'.encode('utf8')
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response_with_body(body, 'ok')
        result = repo.gather_stats(revid, True)
        self.assertEqual(
            [('call_expecting_body', 'Repository.gather_stats',
              ('buick/', revid, 'yes'))],
            client._calls)
        self.assertEqual({'revisions': 2, 'size': 18,
                          'committers': 128,
                          'firstrev': (123456.300, 3600),
                          'latestrev': (654231.400, 0),},
                         result)


class TestRepositoryGetGraph(TestRemoteRepository):

    def test_get_graph(self):
        # get_graph returns a graph with a custom parents provider.
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        graph = repo.get_graph()
        self.assertNotEqual(graph._parents_provider, repo)


class TestRepositoryGetParentMap(TestRemoteRepository):

    def test_get_parent_map_caching(self):
        # get_parent_map returns from cache until unlock()
        # setup a reponse with two revisions
        r1 = u'\u0e33'.encode('utf8')
        r2 = u'\u0dab'.encode('utf8')
        lines = [' '.join([r2, r1]), r1]
        encoded_body = bz2.compress('\n'.join(lines))

        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response_with_body(encoded_body, 'ok')
        client.add_success_response_with_body(encoded_body, 'ok')
        repo.lock_read()
        graph = repo.get_graph()
        parents = graph.get_parent_map([r2])
        self.assertEqual({r2: (r1,)}, parents)
        # locking and unlocking deeper should not reset
        repo.lock_read()
        repo.unlock()
        parents = graph.get_parent_map([r1])
        self.assertEqual({r1: (NULL_REVISION,)}, parents)
        self.assertEqual(
            [('call_with_body_bytes_expecting_body',
              'Repository.get_parent_map', ('quack/', 'include-missing:', r2),
              '\n\n0')],
            client._calls)
        repo.unlock()
        # now we call again, and it should use the second response.
        repo.lock_read()
        graph = repo.get_graph()
        parents = graph.get_parent_map([r1])
        self.assertEqual({r1: (NULL_REVISION,)}, parents)
        self.assertEqual(
            [('call_with_body_bytes_expecting_body',
              'Repository.get_parent_map', ('quack/', 'include-missing:', r2),
              '\n\n0'),
             ('call_with_body_bytes_expecting_body',
              'Repository.get_parent_map', ('quack/', 'include-missing:', r1),
              '\n\n0'),
            ],
            client._calls)
        repo.unlock()

    def test_get_parent_map_reconnects_if_unknown_method(self):
        transport_path = 'quack'
        rev_id = 'revision-id'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_unknown_method_response('Repository.get_parent_map')
        client.add_success_response_with_body(rev_id, 'ok')
        self.assertFalse(client._medium._is_remote_before((1, 2)))
        parents = repo.get_parent_map([rev_id])
        self.assertEqual(
            [('call_with_body_bytes_expecting_body',
              'Repository.get_parent_map',
              ('quack/', 'include-missing:', rev_id), '\n\n0'),
             ('disconnect medium',),
             ('call_expecting_body', 'Repository.get_revision_graph',
              ('quack/', ''))],
            client._calls)
        # The medium is now marked as being connected to an older server
        self.assertTrue(client._medium._is_remote_before((1, 2)))
        self.assertEqual({rev_id: ('null:',)}, parents)

    def test_get_parent_map_fallback_parentless_node(self):
        """get_parent_map falls back to get_revision_graph on old servers.  The
        results from get_revision_graph are tweaked to match the get_parent_map
        API.

        Specifically, a {key: ()} result from get_revision_graph means "no
        parents" for that key, which in get_parent_map results should be
        represented as {key: ('null:',)}.

        This is the test for https://bugs.launchpad.net/bzr/+bug/214894
        """
        rev_id = 'revision-id'
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response_with_body(rev_id, 'ok')
        client._medium._remember_remote_is_before((1, 2))
        parents = repo.get_parent_map([rev_id])
        self.assertEqual(
            [('call_expecting_body', 'Repository.get_revision_graph',
             ('quack/', ''))],
            client._calls)
        self.assertEqual({rev_id: ('null:',)}, parents)

    def test_get_parent_map_unexpected_response(self):
        repo, client = self.setup_fake_client_and_repository('path')
        client.add_success_response('something unexpected!')
        self.assertRaises(
            errors.UnexpectedSmartServerResponse,
            repo.get_parent_map, ['a-revision-id'])

    def test_get_parent_map_negative_caches_missing_keys(self):
        self.setup_smart_server_with_call_log()
        repo = self.make_repository('foo')
        self.assertIsInstance(repo, RemoteRepository)
        repo.lock_read()
        self.addCleanup(repo.unlock)
        self.reset_smart_call_log()
        graph = repo.get_graph()
        self.assertEqual({},
            graph.get_parent_map(['some-missing', 'other-missing']))
        self.assertLength(1, self.hpss_calls)
        # No call if we repeat this
        self.reset_smart_call_log()
        graph = repo.get_graph()
        self.assertEqual({},
            graph.get_parent_map(['some-missing', 'other-missing']))
        self.assertLength(0, self.hpss_calls)
        # Asking for more unknown keys makes a request.
        self.reset_smart_call_log()
        graph = repo.get_graph()
        self.assertEqual({},
            graph.get_parent_map(['some-missing', 'other-missing',
                'more-missing']))
        self.assertLength(1, self.hpss_calls)

    def disableExtraResults(self):
        self.overrideAttr(SmartServerRepositoryGetParentMap,
                          'no_extra_results', True)

    def test_null_cached_missing_and_stop_key(self):
        self.setup_smart_server_with_call_log()
        # Make a branch with a single revision.
        builder = self.make_branch_builder('foo')
        builder.start_series()
        builder.build_snapshot('first', None, [
            ('add', ('', 'root-id', 'directory', ''))])
        builder.finish_series()
        branch = builder.get_branch()
        repo = branch.repository
        self.assertIsInstance(repo, RemoteRepository)
        # Stop the server from sending extra results.
        self.disableExtraResults()
        repo.lock_read()
        self.addCleanup(repo.unlock)
        self.reset_smart_call_log()
        graph = repo.get_graph()
        # Query for 'first' and 'null:'.  Because 'null:' is a parent of
        # 'first' it will be a candidate for the stop_keys of subsequent
        # requests, and because 'null:' was queried but not returned it will be
        # cached as missing.
        self.assertEqual({'first': ('null:',)},
            graph.get_parent_map(['first', 'null:']))
        # Now query for another key.  This request will pass along a recipe of
        # start and stop keys describing the already cached results, and this
        # recipe's revision count must be correct (or else it will trigger an
        # error from the server).
        self.assertEqual({}, graph.get_parent_map(['another-key']))
        # This assertion guards against disableExtraResults silently failing to
        # work, thus invalidating the test.
        self.assertLength(2, self.hpss_calls)

    def test_get_parent_map_gets_ghosts_from_result(self):
        # asking for a revision should negatively cache close ghosts in its
        # ancestry.
        self.setup_smart_server_with_call_log()
        tree = self.make_branch_and_memory_tree('foo')
        tree.lock_write()
        try:
            builder = treebuilder.TreeBuilder()
            builder.start_tree(tree)
            builder.build([])
            builder.finish_tree()
            tree.set_parent_ids(['non-existant'], allow_leftmost_as_ghost=True)
            rev_id = tree.commit('')
        finally:
            tree.unlock()
        tree.lock_read()
        self.addCleanup(tree.unlock)
        repo = tree.branch.repository
        self.assertIsInstance(repo, RemoteRepository)
        # ask for rev_id
        repo.get_parent_map([rev_id])
        self.reset_smart_call_log()
        # Now asking for rev_id's ghost parent should not make calls
        self.assertEqual({}, repo.get_parent_map(['non-existant']))
        self.assertLength(0, self.hpss_calls)

    def test_exposes_get_cached_parent_map(self):
        """RemoteRepository exposes get_cached_parent_map from
        _unstacked_provider
        """
        r1 = u'\u0e33'.encode('utf8')
        r2 = u'\u0dab'.encode('utf8')
        lines = [' '.join([r2, r1]), r1]
        encoded_body = bz2.compress('\n'.join(lines))

        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response_with_body(encoded_body, 'ok')
        repo.lock_read()
        # get_cached_parent_map should *not* trigger an RPC
        self.assertEqual({}, repo.get_cached_parent_map([r1]))
        self.assertEqual([], client._calls)
        self.assertEqual({r2: (r1,)}, repo.get_parent_map([r2]))
        self.assertEqual({r1: (NULL_REVISION,)},
            repo.get_cached_parent_map([r1]))
        self.assertEqual(
            [('call_with_body_bytes_expecting_body',
              'Repository.get_parent_map', ('quack/', 'include-missing:', r2),
              '\n\n0')],
            client._calls)
        repo.unlock()


class TestGetParentMapAllowsNew(tests.TestCaseWithTransport):

    def test_allows_new_revisions(self):
        """get_parent_map's results can be updated by commit."""
        smart_server = test_server.SmartTCPServer_for_testing()
        self.start_server(smart_server)
        self.make_branch('branch')
        branch = Branch.open(smart_server.get_url() + '/branch')
        tree = branch.create_checkout('tree', lightweight=True)
        tree.lock_write()
        self.addCleanup(tree.unlock)
        graph = tree.branch.repository.get_graph()
        # This provides an opportunity for the missing rev-id to be cached.
        self.assertEqual({}, graph.get_parent_map(['rev1']))
        tree.commit('message', rev_id='rev1')
        graph = tree.branch.repository.get_graph()
        self.assertEqual({'rev1': ('null:',)}, graph.get_parent_map(['rev1']))


class TestRepositoryGetRevisionGraph(TestRemoteRepository):

    def test_null_revision(self):
        # a null revision has the predictable result {}, we should have no wire
        # traffic when calling it with this argument
        transport_path = 'empty'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response('notused')
        # actual RemoteRepository.get_revision_graph is gone, but there's an
        # equivalent private method for testing
        result = repo._get_revision_graph(NULL_REVISION)
        self.assertEqual([], client._calls)
        self.assertEqual({}, result)

    def test_none_revision(self):
        # with none we want the entire graph
        r1 = u'\u0e33'.encode('utf8')
        r2 = u'\u0dab'.encode('utf8')
        lines = [' '.join([r2, r1]), r1]
        encoded_body = '\n'.join(lines)

        transport_path = 'sinhala'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response_with_body(encoded_body, 'ok')
        # actual RemoteRepository.get_revision_graph is gone, but there's an
        # equivalent private method for testing
        result = repo._get_revision_graph(None)
        self.assertEqual(
            [('call_expecting_body', 'Repository.get_revision_graph',
             ('sinhala/', ''))],
            client._calls)
        self.assertEqual({r1: (), r2: (r1, )}, result)

    def test_specific_revision(self):
        # with a specific revision we want the graph for that
        # with none we want the entire graph
        r11 = u'\u0e33'.encode('utf8')
        r12 = u'\xc9'.encode('utf8')
        r2 = u'\u0dab'.encode('utf8')
        lines = [' '.join([r2, r11, r12]), r11, r12]
        encoded_body = '\n'.join(lines)

        transport_path = 'sinhala'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response_with_body(encoded_body, 'ok')
        result = repo._get_revision_graph(r2)
        self.assertEqual(
            [('call_expecting_body', 'Repository.get_revision_graph',
             ('sinhala/', r2))],
            client._calls)
        self.assertEqual({r11: (), r12: (), r2: (r11, r12), }, result)

    def test_no_such_revision(self):
        revid = '123'
        transport_path = 'sinhala'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_error_response('nosuchrevision', revid)
        # also check that the right revision is reported in the error
        self.assertRaises(errors.NoSuchRevision,
            repo._get_revision_graph, revid)
        self.assertEqual(
            [('call_expecting_body', 'Repository.get_revision_graph',
             ('sinhala/', revid))],
            client._calls)

    def test_unexpected_error(self):
        revid = '123'
        transport_path = 'sinhala'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_error_response('AnUnexpectedError')
        e = self.assertRaises(errors.UnknownErrorFromSmartServer,
            repo._get_revision_graph, revid)
        self.assertEqual(('AnUnexpectedError',), e.error_tuple)


class TestRepositoryGetRevIdForRevno(TestRemoteRepository):

    def test_ok(self):
        repo, client = self.setup_fake_client_and_repository('quack')
        client.add_expected_call(
            'Repository.get_rev_id_for_revno', ('quack/', 5, (42, 'rev-foo')),
            'success', ('ok', 'rev-five'))
        result = repo.get_rev_id_for_revno(5, (42, 'rev-foo'))
        self.assertEqual((True, 'rev-five'), result)
        self.assertFinished(client)

    def test_history_incomplete(self):
        repo, client = self.setup_fake_client_and_repository('quack')
        client.add_expected_call(
            'Repository.get_rev_id_for_revno', ('quack/', 5, (42, 'rev-foo')),
            'success', ('history-incomplete', 10, 'rev-ten'))
        result = repo.get_rev_id_for_revno(5, (42, 'rev-foo'))
        self.assertEqual((False, (10, 'rev-ten')), result)
        self.assertFinished(client)

    def test_history_incomplete_with_fallback(self):
        """A 'history-incomplete' response causes the fallback repository to be
        queried too, if one is set.
        """
        # Make a repo with a fallback repo, both using a FakeClient.
        format = remote.response_tuple_to_repo_format(
            ('yes', 'no', 'yes', self.get_repo_format().network_name()))
        repo, client = self.setup_fake_client_and_repository('quack')
        repo._format = format
        fallback_repo, ignored = self.setup_fake_client_and_repository(
            'fallback')
        fallback_repo._client = client
        fallback_repo._format = format
        repo.add_fallback_repository(fallback_repo)
        # First the client should ask the primary repo
        client.add_expected_call(
            'Repository.get_rev_id_for_revno', ('quack/', 1, (42, 'rev-foo')),
            'success', ('history-incomplete', 2, 'rev-two'))
        # Then it should ask the fallback, using revno/revid from the
        # history-incomplete response as the known revno/revid.
        client.add_expected_call(
            'Repository.get_rev_id_for_revno',('fallback/', 1, (2, 'rev-two')),
            'success', ('ok', 'rev-one'))
        result = repo.get_rev_id_for_revno(1, (42, 'rev-foo'))
        self.assertEqual((True, 'rev-one'), result)
        self.assertFinished(client)

    def test_nosuchrevision(self):
        # 'nosuchrevision' is returned when the known-revid is not found in the
        # remote repo.  The client translates that response to NoSuchRevision.
        repo, client = self.setup_fake_client_and_repository('quack')
        client.add_expected_call(
            'Repository.get_rev_id_for_revno', ('quack/', 5, (42, 'rev-foo')),
            'error', ('nosuchrevision', 'rev-foo'))
        self.assertRaises(
            errors.NoSuchRevision,
            repo.get_rev_id_for_revno, 5, (42, 'rev-foo'))
        self.assertFinished(client)

    def test_branch_fallback_locking(self):
        """RemoteBranch.get_rev_id takes a read lock, and tries to call the
        get_rev_id_for_revno verb.  If the verb is unknown the VFS fallback
        will be invoked, which will fail if the repo is unlocked.
        """
        self.setup_smart_server_with_call_log()
        tree = self.make_branch_and_memory_tree('.')
        tree.lock_write()
        tree.add('')
        rev1 = tree.commit('First')
        rev2 = tree.commit('Second')
        tree.unlock()
        branch = tree.branch
        self.assertFalse(branch.is_locked())
        self.reset_smart_call_log()
        verb = 'Repository.get_rev_id_for_revno'
        self.disable_verb(verb)
        self.assertEqual(rev1, branch.get_rev_id(1))
        self.assertLength(1, [call for call in self.hpss_calls if
                              call.call.method == verb])


class TestRepositoryHasSignatureForRevisionId(TestRemoteRepository):

    def test_has_signature_for_revision_id(self):
        # ('yes', ) for Repository.has_signature_for_revision_id -> 'True'.
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response('yes')
        result = repo.has_signature_for_revision_id('A')
        self.assertEqual(
            [('call', 'Repository.has_signature_for_revision_id',
              ('quack/', 'A'))],
            client._calls)
        self.assertEqual(True, result)

    def test_is_not_shared(self):
        # ('no', ) for Repository.has_signature_for_revision_id -> 'False'.
        transport_path = 'qwack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response('no')
        result = repo.has_signature_for_revision_id('A')
        self.assertEqual(
            [('call', 'Repository.has_signature_for_revision_id',
              ('qwack/', 'A'))],
            client._calls)
        self.assertEqual(False, result)


class TestRepositoryIsShared(TestRemoteRepository):

    def test_is_shared(self):
        # ('yes', ) for Repository.is_shared -> 'True'.
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response('yes')
        result = repo.is_shared()
        self.assertEqual(
            [('call', 'Repository.is_shared', ('quack/',))],
            client._calls)
        self.assertEqual(True, result)

    def test_is_not_shared(self):
        # ('no', ) for Repository.is_shared -> 'False'.
        transport_path = 'qwack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response('no')
        result = repo.is_shared()
        self.assertEqual(
            [('call', 'Repository.is_shared', ('qwack/',))],
            client._calls)
        self.assertEqual(False, result)


class TestRepositoryMakeWorkingTrees(TestRemoteRepository):

    def test_make_working_trees(self):
        # ('yes', ) for Repository.make_working_trees -> 'True'.
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response('yes')
        result = repo.make_working_trees()
        self.assertEqual(
            [('call', 'Repository.make_working_trees', ('quack/',))],
            client._calls)
        self.assertEqual(True, result)

    def test_no_working_trees(self):
        # ('no', ) for Repository.make_working_trees -> 'False'.
        transport_path = 'qwack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response('no')
        result = repo.make_working_trees()
        self.assertEqual(
            [('call', 'Repository.make_working_trees', ('qwack/',))],
            client._calls)
        self.assertEqual(False, result)


class TestRepositoryLockWrite(TestRemoteRepository):

    def test_lock_write(self):
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response('ok', 'a token')
        token = repo.lock_write().repository_token
        self.assertEqual(
            [('call', 'Repository.lock_write', ('quack/', ''))],
            client._calls)
        self.assertEqual('a token', token)

    def test_lock_write_already_locked(self):
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_error_response('LockContention')
        self.assertRaises(errors.LockContention, repo.lock_write)
        self.assertEqual(
            [('call', 'Repository.lock_write', ('quack/', ''))],
            client._calls)

    def test_lock_write_unlockable(self):
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_error_response('UnlockableTransport')
        self.assertRaises(errors.UnlockableTransport, repo.lock_write)
        self.assertEqual(
            [('call', 'Repository.lock_write', ('quack/', ''))],
            client._calls)


class TestRepositorySetMakeWorkingTrees(TestRemoteRepository):

    def test_backwards_compat(self):
        self.setup_smart_server_with_call_log()
        repo = self.make_repository('.')
        self.reset_smart_call_log()
        verb = 'Repository.set_make_working_trees'
        self.disable_verb(verb)
        repo.set_make_working_trees(True)
        call_count = len([call for call in self.hpss_calls if
            call.call.method == verb])
        self.assertEqual(1, call_count)

    def test_current(self):
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_expected_call(
            'Repository.set_make_working_trees', ('quack/', 'True'),
            'success', ('ok',))
        client.add_expected_call(
            'Repository.set_make_working_trees', ('quack/', 'False'),
            'success', ('ok',))
        repo.set_make_working_trees(True)
        repo.set_make_working_trees(False)


class TestRepositoryUnlock(TestRemoteRepository):

    def test_unlock(self):
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response('ok', 'a token')
        client.add_success_response('ok')
        repo.lock_write()
        repo.unlock()
        self.assertEqual(
            [('call', 'Repository.lock_write', ('quack/', '')),
             ('call', 'Repository.unlock', ('quack/', 'a token'))],
            client._calls)

    def test_unlock_wrong_token(self):
        # If somehow the token is wrong, unlock will raise TokenMismatch.
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response('ok', 'a token')
        client.add_error_response('TokenMismatch')
        repo.lock_write()
        self.assertRaises(errors.TokenMismatch, repo.unlock)


class TestRepositoryHasRevision(TestRemoteRepository):

    def test_none(self):
        # repo.has_revision(None) should not cause any traffic.
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)

        # The null revision is always there, so has_revision(None) == True.
        self.assertEqual(True, repo.has_revision(NULL_REVISION))

        # The remote repo shouldn't be accessed.
        self.assertEqual([], client._calls)


class TestRepositoryInsertStreamBase(TestRemoteRepository):
    """Base class for Repository.insert_stream and .insert_stream_1.19
    tests.
    """
    
    def checkInsertEmptyStream(self, repo, client):
        """Insert an empty stream, checking the result.

        This checks that there are no resume_tokens or missing_keys, and that
        the client is finished.
        """
        sink = repo._get_sink()
        fmt = repository.format_registry.get_default()
        resume_tokens, missing_keys = sink.insert_stream([], fmt, [])
        self.assertEqual([], resume_tokens)
        self.assertEqual(set(), missing_keys)
        self.assertFinished(client)


class TestRepositoryInsertStream(TestRepositoryInsertStreamBase):
    """Tests for using Repository.insert_stream verb when the _1.19 variant is
    not available.

    This test case is very similar to TestRepositoryInsertStream_1_19.
    """

    def setUp(self):
        TestRemoteRepository.setUp(self)
        self.disable_verb('Repository.insert_stream_1.19')

    def test_unlocked_repo(self):
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_expected_call(
            'Repository.insert_stream_1.19', ('quack/', ''),
            'unknown', ('Repository.insert_stream_1.19',))
        client.add_expected_call(
            'Repository.insert_stream', ('quack/', ''),
            'success', ('ok',))
        client.add_expected_call(
            'Repository.insert_stream', ('quack/', ''),
            'success', ('ok',))
        self.checkInsertEmptyStream(repo, client)

    def test_locked_repo_with_no_lock_token(self):
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_expected_call(
            'Repository.lock_write', ('quack/', ''),
            'success', ('ok', ''))
        client.add_expected_call(
            'Repository.insert_stream_1.19', ('quack/', ''),
            'unknown', ('Repository.insert_stream_1.19',))
        client.add_expected_call(
            'Repository.insert_stream', ('quack/', ''),
            'success', ('ok',))
        client.add_expected_call(
            'Repository.insert_stream', ('quack/', ''),
            'success', ('ok',))
        repo.lock_write()
        self.checkInsertEmptyStream(repo, client)

    def test_locked_repo_with_lock_token(self):
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_expected_call(
            'Repository.lock_write', ('quack/', ''),
            'success', ('ok', 'a token'))
        client.add_expected_call(
            'Repository.insert_stream_1.19', ('quack/', '', 'a token'),
            'unknown', ('Repository.insert_stream_1.19',))
        client.add_expected_call(
            'Repository.insert_stream_locked', ('quack/', '', 'a token'),
            'success', ('ok',))
        client.add_expected_call(
            'Repository.insert_stream_locked', ('quack/', '', 'a token'),
            'success', ('ok',))
        repo.lock_write()
        self.checkInsertEmptyStream(repo, client)

    def test_stream_with_inventory_deltas(self):
        """'inventory-deltas' substreams cannot be sent to the
        Repository.insert_stream verb, because not all servers that implement
        that verb will accept them.  So when one is encountered the RemoteSink
        immediately stops using that verb and falls back to VFS insert_stream.
        """
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_expected_call(
            'Repository.insert_stream_1.19', ('quack/', ''),
            'unknown', ('Repository.insert_stream_1.19',))
        client.add_expected_call(
            'Repository.insert_stream', ('quack/', ''),
            'success', ('ok',))
        client.add_expected_call(
            'Repository.insert_stream', ('quack/', ''),
            'success', ('ok',))
        # Create a fake real repository for insert_stream to fall back on, so
        # that we can directly see the records the RemoteSink passes to the
        # real sink.
        class FakeRealSink:
            def __init__(self):
                self.records = []
            def insert_stream(self, stream, src_format, resume_tokens):
                for substream_kind, substream in stream:
                    self.records.append(
                        (substream_kind, [record.key for record in substream]))
                return ['fake tokens'], ['fake missing keys']
        fake_real_sink = FakeRealSink()
        class FakeRealRepository:
            def _get_sink(self):
                return fake_real_sink
            def is_in_write_group(self):
                return False
            def refresh_data(self):
                return True
        repo._real_repository = FakeRealRepository()
        sink = repo._get_sink()
        fmt = repository.format_registry.get_default()
        stream = self.make_stream_with_inv_deltas(fmt)
        resume_tokens, missing_keys = sink.insert_stream(stream, fmt, [])
        # Every record from the first inventory delta should have been sent to
        # the VFS sink.
        expected_records = [
            ('inventory-deltas', [('rev2',), ('rev3',)]),
            ('texts', [('some-rev', 'some-file')])]
        self.assertEqual(expected_records, fake_real_sink.records)
        # The return values from the real sink's insert_stream are propagated
        # back to the original caller.
        self.assertEqual(['fake tokens'], resume_tokens)
        self.assertEqual(['fake missing keys'], missing_keys)
        self.assertFinished(client)

    def make_stream_with_inv_deltas(self, fmt):
        """Make a simple stream with an inventory delta followed by more
        records and more substreams to test that all records and substreams
        from that point on are used.

        This sends, in order:
           * inventories substream: rev1, rev2, rev3.  rev2 and rev3 are
             inventory-deltas.
           * texts substream: (some-rev, some-file)
        """
        # Define a stream using generators so that it isn't rewindable.
        inv = inventory.Inventory(revision_id='rev1')
        inv.root.revision = 'rev1'
        def stream_with_inv_delta():
            yield ('inventories', inventories_substream())
            yield ('inventory-deltas', inventory_delta_substream())
            yield ('texts', [
                versionedfile.FulltextContentFactory(
                    ('some-rev', 'some-file'), (), None, 'content')])
        def inventories_substream():
            # An empty inventory fulltext.  This will be streamed normally.
            text = fmt._serializer.write_inventory_to_string(inv)
            yield versionedfile.FulltextContentFactory(
                ('rev1',), (), None, text)
        def inventory_delta_substream():
            # An inventory delta.  This can't be streamed via this verb, so it
            # will trigger a fallback to VFS insert_stream.
            entry = inv.make_entry(
                'directory', 'newdir', inv.root.file_id, 'newdir-id')
            entry.revision = 'ghost'
            delta = [(None, 'newdir', 'newdir-id', entry)]
            serializer = inventory_delta.InventoryDeltaSerializer(
                versioned_root=True, tree_references=False)
            lines = serializer.delta_to_lines('rev1', 'rev2', delta)
            yield versionedfile.ChunkedContentFactory(
                ('rev2',), (('rev1',)), None, lines)
            # Another delta.
            lines = serializer.delta_to_lines('rev1', 'rev3', delta)
            yield versionedfile.ChunkedContentFactory(
                ('rev3',), (('rev1',)), None, lines)
        return stream_with_inv_delta()


class TestRepositoryInsertStream_1_19(TestRepositoryInsertStreamBase):

    def test_unlocked_repo(self):
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_expected_call(
            'Repository.insert_stream_1.19', ('quack/', ''),
            'success', ('ok',))
        client.add_expected_call(
            'Repository.insert_stream_1.19', ('quack/', ''),
            'success', ('ok',))
        self.checkInsertEmptyStream(repo, client)

    def test_locked_repo_with_no_lock_token(self):
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_expected_call(
            'Repository.lock_write', ('quack/', ''),
            'success', ('ok', ''))
        client.add_expected_call(
            'Repository.insert_stream_1.19', ('quack/', ''),
            'success', ('ok',))
        client.add_expected_call(
            'Repository.insert_stream_1.19', ('quack/', ''),
            'success', ('ok',))
        repo.lock_write()
        self.checkInsertEmptyStream(repo, client)

    def test_locked_repo_with_lock_token(self):
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_expected_call(
            'Repository.lock_write', ('quack/', ''),
            'success', ('ok', 'a token'))
        client.add_expected_call(
            'Repository.insert_stream_1.19', ('quack/', '', 'a token'),
            'success', ('ok',))
        client.add_expected_call(
            'Repository.insert_stream_1.19', ('quack/', '', 'a token'),
            'success', ('ok',))
        repo.lock_write()
        self.checkInsertEmptyStream(repo, client)


class TestRepositoryTarball(TestRemoteRepository):

    # This is a canned tarball reponse we can validate against
    tarball_content = (
        'QlpoOTFBWSZTWdGkj3wAAWF/k8aQACBIB//A9+8cIX/v33AACEAYABAECEACNz'
        'JqsgJJFPTSnk1A3qh6mTQAAAANPUHkagkSTEkaA09QaNAAAGgAAAcwCYCZGAEY'
        'mJhMJghpiaYBUkKammSHqNMZQ0NABkNAeo0AGneAevnlwQoGzEzNVzaYxp/1Uk'
        'xXzA1CQX0BJMZZLcPBrluJir5SQyijWHYZ6ZUtVqqlYDdB2QoCwa9GyWwGYDMA'
        'OQYhkpLt/OKFnnlT8E0PmO8+ZNSo2WWqeCzGB5fBXZ3IvV7uNJVE7DYnWj6qwB'
        'k5DJDIrQ5OQHHIjkS9KqwG3mc3t+F1+iujb89ufyBNIKCgeZBWrl5cXxbMGoMs'
        'c9JuUkg5YsiVcaZJurc6KLi6yKOkgCUOlIlOpOoXyrTJjK8ZgbklReDdwGmFgt'
        'dkVsAIslSVCd4AtACSLbyhLHryfb14PKegrVDba+U8OL6KQtzdM5HLjAc8/p6n'
        '0lgaWU8skgO7xupPTkyuwheSckejFLK5T4ZOo0Gda9viaIhpD1Qn7JqqlKAJqC'
        'QplPKp2nqBWAfwBGaOwVrz3y1T+UZZNismXHsb2Jq18T+VaD9k4P8DqE3g70qV'
        'JLurpnDI6VS5oqDDPVbtVjMxMxMg4rzQVipn2Bv1fVNK0iq3Gl0hhnnHKm/egy'
        'nWQ7QH/F3JFOFCQ0aSPfA='
        ).decode('base64')

    def test_repository_tarball(self):
        # Test that Repository.tarball generates the right operations
        transport_path = 'repo'
        expected_calls = [('call_expecting_body', 'Repository.tarball',
                           ('repo/', 'bz2',),),
            ]
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_success_response_with_body(self.tarball_content, 'ok')
        # Now actually ask for the tarball
        tarball_file = repo._get_tarball('bz2')
        try:
            self.assertEqual(expected_calls, client._calls)
            self.assertEqual(self.tarball_content, tarball_file.read())
        finally:
            tarball_file.close()


class TestRemoteRepositoryCopyContent(tests.TestCaseWithTransport):
    """RemoteRepository.copy_content_into optimizations"""

    def test_copy_content_remote_to_local(self):
        self.transport_server = test_server.SmartTCPServer_for_testing
        src_repo = self.make_repository('repo1')
        src_repo = repository.Repository.open(self.get_url('repo1'))
        # At the moment the tarball-based copy_content_into can't write back
        # into a smart server.  It would be good if it could upload the
        # tarball; once that works we'd have to create repositories of
        # different formats. -- mbp 20070410
        dest_url = self.get_vfs_only_url('repo2')
        dest_bzrdir = BzrDir.create(dest_url)
        dest_repo = dest_bzrdir.create_repository()
        self.assertFalse(isinstance(dest_repo, RemoteRepository))
        self.assertTrue(isinstance(src_repo, RemoteRepository))
        src_repo.copy_content_into(dest_repo)


class _StubRealPackRepository(object):

    def __init__(self, calls):
        self.calls = calls
        self._pack_collection = _StubPackCollection(calls)

    def is_in_write_group(self):
        return False

    def refresh_data(self):
        self.calls.append(('pack collection reload_pack_names',))


class _StubPackCollection(object):

    def __init__(self, calls):
        self.calls = calls

    def autopack(self):
        self.calls.append(('pack collection autopack',))


class TestRemotePackRepositoryAutoPack(TestRemoteRepository):
    """Tests for RemoteRepository.autopack implementation."""

    def test_ok(self):
        """When the server returns 'ok' and there's no _real_repository, then
        nothing else happens: the autopack method is done.
        """
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_expected_call(
            'PackRepository.autopack', ('quack/',), 'success', ('ok',))
        repo.autopack()
        self.assertFinished(client)

    def test_ok_with_real_repo(self):
        """When the server returns 'ok' and there is a _real_repository, then
        the _real_repository's reload_pack_name's method will be called.
        """
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_expected_call(
            'PackRepository.autopack', ('quack/',),
            'success', ('ok',))
        repo._real_repository = _StubRealPackRepository(client._calls)
        repo.autopack()
        self.assertEqual(
            [('call', 'PackRepository.autopack', ('quack/',)),
             ('pack collection reload_pack_names',)],
            client._calls)

    def test_backwards_compatibility(self):
        """If the server does not recognise the PackRepository.autopack verb,
        fallback to the real_repository's implementation.
        """
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_unknown_method_response('PackRepository.autopack')
        def stub_ensure_real():
            client._calls.append(('_ensure_real',))
            repo._real_repository = _StubRealPackRepository(client._calls)
        repo._ensure_real = stub_ensure_real
        repo.autopack()
        self.assertEqual(
            [('call', 'PackRepository.autopack', ('quack/',)),
             ('_ensure_real',),
             ('pack collection autopack',)],
            client._calls)

    def test_oom_error_reporting(self):
        """An out-of-memory condition on the server is reported clearly"""
        transport_path = 'quack'
        repo, client = self.setup_fake_client_and_repository(transport_path)
        client.add_expected_call(
            'PackRepository.autopack', ('quack/',),
            'error', ('MemoryError',))
        err = self.assertRaises(errors.BzrError, repo.autopack)
        self.assertContainsRe(str(err), "^remote server out of mem")


class TestErrorTranslationBase(tests.TestCaseWithMemoryTransport):
    """Base class for unit tests for bzrlib.remote._translate_error."""

    def translateTuple(self, error_tuple, **context):
        """Call _translate_error with an ErrorFromSmartServer built from the
        given error_tuple.

        :param error_tuple: A tuple of a smart server response, as would be
            passed to an ErrorFromSmartServer.
        :kwargs context: context items to call _translate_error with.

        :returns: The error raised by _translate_error.
        """
        # Raise the ErrorFromSmartServer before passing it as an argument,
        # because _translate_error may need to re-raise it with a bare 'raise'
        # statement.
        server_error = errors.ErrorFromSmartServer(error_tuple)
        translated_error = self.translateErrorFromSmartServer(
            server_error, **context)
        return translated_error

    def translateErrorFromSmartServer(self, error_object, **context):
        """Like translateTuple, but takes an already constructed
        ErrorFromSmartServer rather than a tuple.
        """
        try:
            raise error_object
        except errors.ErrorFromSmartServer, server_error:
            translated_error = self.assertRaises(
                errors.BzrError, remote._translate_error, server_error,
                **context)
        return translated_error


class TestErrorTranslationSuccess(TestErrorTranslationBase):
    """Unit tests for bzrlib.remote._translate_error.

    Given an ErrorFromSmartServer (which has an error tuple from a smart
    server) and some context, _translate_error raises more specific errors from
    bzrlib.errors.

    This test case covers the cases where _translate_error succeeds in
    translating an ErrorFromSmartServer to something better.  See
    TestErrorTranslationRobustness for other cases.
    """

    def test_NoSuchRevision(self):
        branch = self.make_branch('')
        revid = 'revid'
        translated_error = self.translateTuple(
            ('NoSuchRevision', revid), branch=branch)
        expected_error = errors.NoSuchRevision(branch, revid)
        self.assertEqual(expected_error, translated_error)

    def test_nosuchrevision(self):
        repository = self.make_repository('')
        revid = 'revid'
        translated_error = self.translateTuple(
            ('nosuchrevision', revid), repository=repository)
        expected_error = errors.NoSuchRevision(repository, revid)
        self.assertEqual(expected_error, translated_error)

    def test_nobranch(self):
        bzrdir = self.make_bzrdir('')
        translated_error = self.translateTuple(('nobranch',), bzrdir=bzrdir)
        expected_error = errors.NotBranchError(path=bzrdir.root_transport.base)
        self.assertEqual(expected_error, translated_error)

    def test_nobranch_one_arg(self):
        bzrdir = self.make_bzrdir('')
        translated_error = self.translateTuple(
            ('nobranch', 'extra detail'), bzrdir=bzrdir)
        expected_error = errors.NotBranchError(
            path=bzrdir.root_transport.base,
            detail='extra detail')
        self.assertEqual(expected_error, translated_error)

    def test_norepository(self):
        bzrdir = self.make_bzrdir('')
        translated_error = self.translateTuple(('norepository',),
            bzrdir=bzrdir)
        expected_error = errors.NoRepositoryPresent(bzrdir)
        self.assertEqual(expected_error, translated_error)

    def test_LockContention(self):
        translated_error = self.translateTuple(('LockContention',))
        expected_error = errors.LockContention('(remote lock)')
        self.assertEqual(expected_error, translated_error)

    def test_UnlockableTransport(self):
        bzrdir = self.make_bzrdir('')
        translated_error = self.translateTuple(
            ('UnlockableTransport',), bzrdir=bzrdir)
        expected_error = errors.UnlockableTransport(bzrdir.root_transport)
        self.assertEqual(expected_error, translated_error)

    def test_LockFailed(self):
        lock = 'str() of a server lock'
        why = 'str() of why'
        translated_error = self.translateTuple(('LockFailed', lock, why))
        expected_error = errors.LockFailed(lock, why)
        self.assertEqual(expected_error, translated_error)

    def test_TokenMismatch(self):
        token = 'a lock token'
        translated_error = self.translateTuple(('TokenMismatch',), token=token)
        expected_error = errors.TokenMismatch(token, '(remote token)')
        self.assertEqual(expected_error, translated_error)

    def test_Diverged(self):
        branch = self.make_branch('a')
        other_branch = self.make_branch('b')
        translated_error = self.translateTuple(
            ('Diverged',), branch=branch, other_branch=other_branch)
        expected_error = errors.DivergedBranches(branch, other_branch)
        self.assertEqual(expected_error, translated_error)

    def test_NotStacked(self):
        branch = self.make_branch('')
        translated_error = self.translateTuple(('NotStacked',), branch=branch)
        expected_error = errors.NotStacked(branch)
        self.assertEqual(expected_error, translated_error)

    def test_ReadError_no_args(self):
        path = 'a path'
        translated_error = self.translateTuple(('ReadError',), path=path)
        expected_error = errors.ReadError(path)
        self.assertEqual(expected_error, translated_error)

    def test_ReadError(self):
        path = 'a path'
        translated_error = self.translateTuple(('ReadError', path))
        expected_error = errors.ReadError(path)
        self.assertEqual(expected_error, translated_error)

    def test_IncompatibleRepositories(self):
        translated_error = self.translateTuple(('IncompatibleRepositories',
            "repo1", "repo2", "details here"))
        expected_error = errors.IncompatibleRepositories("repo1", "repo2",
            "details here")
        self.assertEqual(expected_error, translated_error)

    def test_PermissionDenied_no_args(self):
        path = 'a path'
        translated_error = self.translateTuple(('PermissionDenied',),
            path=path)
        expected_error = errors.PermissionDenied(path)
        self.assertEqual(expected_error, translated_error)

    def test_PermissionDenied_one_arg(self):
        path = 'a path'
        translated_error = self.translateTuple(('PermissionDenied', path))
        expected_error = errors.PermissionDenied(path)
        self.assertEqual(expected_error, translated_error)

    def test_PermissionDenied_one_arg_and_context(self):
        """Given a choice between a path from the local context and a path on
        the wire, _translate_error prefers the path from the local context.
        """
        local_path = 'local path'
        remote_path = 'remote path'
        translated_error = self.translateTuple(
            ('PermissionDenied', remote_path), path=local_path)
        expected_error = errors.PermissionDenied(local_path)
        self.assertEqual(expected_error, translated_error)

    def test_PermissionDenied_two_args(self):
        path = 'a path'
        extra = 'a string with extra info'
        translated_error = self.translateTuple(
            ('PermissionDenied', path, extra))
        expected_error = errors.PermissionDenied(path, extra)
        self.assertEqual(expected_error, translated_error)

    # GZ 2011-03-02: TODO test for PermissionDenied with non-ascii 'extra'

    def test_NoSuchFile_context_path(self):
        local_path = "local path"
        translated_error = self.translateTuple(('ReadError', "remote path"),
            path=local_path)
        expected_error = errors.ReadError(local_path)
        self.assertEqual(expected_error, translated_error)

    def test_NoSuchFile_without_context(self):
        remote_path = "remote path"
        translated_error = self.translateTuple(('ReadError', remote_path))
        expected_error = errors.ReadError(remote_path)
        self.assertEqual(expected_error, translated_error)

    def test_ReadOnlyError(self):
        translated_error = self.translateTuple(('ReadOnlyError',))
        expected_error = errors.TransportNotPossible("readonly transport")
        self.assertEqual(expected_error, translated_error)

    def test_MemoryError(self):
        translated_error = self.translateTuple(('MemoryError',))
        self.assertStartsWith(str(translated_error),
            "remote server out of memory")

    def test_generic_IndexError_no_classname(self):
        err = errors.ErrorFromSmartServer(('error', "list index out of range"))
        translated_error = self.translateErrorFromSmartServer(err)
        expected_error = errors.UnknownErrorFromSmartServer(err)
        self.assertEqual(expected_error, translated_error)

    # GZ 2011-03-02: TODO test generic non-ascii error string

    def test_generic_KeyError(self):
        err = errors.ErrorFromSmartServer(('error', 'KeyError', "1"))
        translated_error = self.translateErrorFromSmartServer(err)
        expected_error = errors.UnknownErrorFromSmartServer(err)
        self.assertEqual(expected_error, translated_error)


class TestErrorTranslationRobustness(TestErrorTranslationBase):
    """Unit tests for bzrlib.remote._translate_error's robustness.

    TestErrorTranslationSuccess is for cases where _translate_error can
    translate successfully.  This class about how _translate_err behaves when
    it fails to translate: it re-raises the original error.
    """

    def test_unrecognised_server_error(self):
        """If the error code from the server is not recognised, the original
        ErrorFromSmartServer is propagated unmodified.
        """
        error_tuple = ('An unknown error tuple',)
        server_error = errors.ErrorFromSmartServer(error_tuple)
        translated_error = self.translateErrorFromSmartServer(server_error)
        expected_error = errors.UnknownErrorFromSmartServer(server_error)
        self.assertEqual(expected_error, translated_error)

    def test_context_missing_a_key(self):
        """In case of a bug in the client, or perhaps an unexpected response
        from a server, _translate_error returns the original error tuple from
        the server and mutters a warning.
        """
        # To translate a NoSuchRevision error _translate_error needs a 'branch'
        # in the context dict.  So let's give it an empty context dict instead
        # to exercise its error recovery.
        empty_context = {}
        error_tuple = ('NoSuchRevision', 'revid')
        server_error = errors.ErrorFromSmartServer(error_tuple)
        translated_error = self.translateErrorFromSmartServer(server_error)
        self.assertEqual(server_error, translated_error)
        # In addition to re-raising ErrorFromSmartServer, some debug info has
        # been muttered to the log file for developer to look at.
        self.assertContainsRe(
            self.get_log(),
            "Missing key 'branch' in context")

    def test_path_missing(self):
        """Some translations (PermissionDenied, ReadError) can determine the
        'path' variable from either the wire or the local context.  If neither
        has it, then an error is raised.
        """
        error_tuple = ('ReadError',)
        server_error = errors.ErrorFromSmartServer(error_tuple)
        translated_error = self.translateErrorFromSmartServer(server_error)
        self.assertEqual(server_error, translated_error)
        # In addition to re-raising ErrorFromSmartServer, some debug info has
        # been muttered to the log file for developer to look at.
        self.assertContainsRe(self.get_log(), "Missing key 'path' in context")


class TestStacking(tests.TestCaseWithTransport):
    """Tests for operations on stacked remote repositories.

    The underlying format type must support stacking.
    """

    def test_access_stacked_remote(self):
        # based on <http://launchpad.net/bugs/261315>
        # make a branch stacked on another repository containing an empty
        # revision, then open it over hpss - we should be able to see that
        # revision.
        base_transport = self.get_transport()
        base_builder = self.make_branch_builder('base', format='1.9')
        base_builder.start_series()
        base_revid = base_builder.build_snapshot('rev-id', None,
            [('add', ('', None, 'directory', None))],
            'message')
        base_builder.finish_series()
        stacked_branch = self.make_branch('stacked', format='1.9')
        stacked_branch.set_stacked_on_url('../base')
        # start a server looking at this
        smart_server = test_server.SmartTCPServer_for_testing()
        self.start_server(smart_server)
        remote_bzrdir = BzrDir.open(smart_server.get_url() + '/stacked')
        # can get its branch and repository
        remote_branch = remote_bzrdir.open_branch()
        remote_repo = remote_branch.repository
        remote_repo.lock_read()
        try:
            # it should have an appropriate fallback repository, which should also
            # be a RemoteRepository
            self.assertLength(1, remote_repo._fallback_repositories)
            self.assertIsInstance(remote_repo._fallback_repositories[0],
                RemoteRepository)
            # and it has the revision committed to the underlying repository;
            # these have varying implementations so we try several of them
            self.assertTrue(remote_repo.has_revisions([base_revid]))
            self.assertTrue(remote_repo.has_revision(base_revid))
            self.assertEqual(remote_repo.get_revision(base_revid).message,
                'message')
        finally:
            remote_repo.unlock()

    def prepare_stacked_remote_branch(self):
        """Get stacked_upon and stacked branches with content in each."""
        self.setup_smart_server_with_call_log()
        tree1 = self.make_branch_and_tree('tree1', format='1.9')
        tree1.commit('rev1', rev_id='rev1')
        tree2 = tree1.branch.bzrdir.sprout('tree2', stacked=True
            ).open_workingtree()
        local_tree = tree2.branch.create_checkout('local')
        local_tree.commit('local changes make me feel good.')
        branch2 = Branch.open(self.get_url('tree2'))
        branch2.lock_read()
        self.addCleanup(branch2.unlock)
        return tree1.branch, branch2

    def test_stacked_get_parent_map(self):
        # the public implementation of get_parent_map obeys stacking
        _, branch = self.prepare_stacked_remote_branch()
        repo = branch.repository
        self.assertEqual(['rev1'], repo.get_parent_map(['rev1']).keys())

    def test_unstacked_get_parent_map(self):
        # _unstacked_provider.get_parent_map ignores stacking
        _, branch = self.prepare_stacked_remote_branch()
        provider = branch.repository._unstacked_provider
        self.assertEqual([], provider.get_parent_map(['rev1']).keys())

    def fetch_stream_to_rev_order(self, stream):
        result = []
        for kind, substream in stream:
            if not kind == 'revisions':
                list(substream)
            else:
                for content in substream:
                    result.append(content.key[-1])
        return result

    def get_ordered_revs(self, format, order, branch_factory=None):
        """Get a list of the revisions in a stream to format format.

        :param format: The format of the target.
        :param order: the order that target should have requested.
        :param branch_factory: A callable to create a trunk and stacked branch
            to fetch from. If none, self.prepare_stacked_remote_branch is used.
        :result: The revision ids in the stream, in the order seen,
            the topological order of revisions in the source.
        """
        unordered_format = bzrdir.format_registry.get(format)()
        target_repository_format = unordered_format.repository_format
        # Cross check
        self.assertEqual(order, target_repository_format._fetch_order)
        if branch_factory is None:
            branch_factory = self.prepare_stacked_remote_branch
        _, stacked = branch_factory()
        source = stacked.repository._get_source(target_repository_format)
        tip = stacked.last_revision()
        stacked.repository._ensure_real()
        graph = stacked.repository.get_graph()
        revs = [r for (r,ps) in graph.iter_ancestry([tip])
                if r != NULL_REVISION]
        revs.reverse()
        search = _mod_graph.PendingAncestryResult([tip], stacked.repository)
        self.reset_smart_call_log()
        stream = source.get_stream(search)
        # We trust that if a revision is in the stream the rest of the new
        # content for it is too, as per our main fetch tests; here we are
        # checking that the revisions are actually included at all, and their
        # order.
        return self.fetch_stream_to_rev_order(stream), revs

    def test_stacked_get_stream_unordered(self):
        # Repository._get_source.get_stream() from a stacked repository with
        # unordered yields the full data from both stacked and stacked upon
        # sources.
        rev_ord, expected_revs = self.get_ordered_revs('1.9', 'unordered')
        self.assertEqual(set(expected_revs), set(rev_ord))
        # Getting unordered results should have made a streaming data request
        # from the server, then one from the backing branch.
        self.assertLength(2, self.hpss_calls)

    def test_stacked_on_stacked_get_stream_unordered(self):
        # Repository._get_source.get_stream() from a stacked repository which
        # is itself stacked yields the full data from all three sources.
        def make_stacked_stacked():
            _, stacked = self.prepare_stacked_remote_branch()
            tree = stacked.bzrdir.sprout('tree3', stacked=True
                ).open_workingtree()
            local_tree = tree.branch.create_checkout('local-tree3')
            local_tree.commit('more local changes are better')
            branch = Branch.open(self.get_url('tree3'))
            branch.lock_read()
            self.addCleanup(branch.unlock)
            return None, branch
        rev_ord, expected_revs = self.get_ordered_revs('1.9', 'unordered',
            branch_factory=make_stacked_stacked)
        self.assertEqual(set(expected_revs), set(rev_ord))
        # Getting unordered results should have made a streaming data request
        # from the server, and one from each backing repo
        self.assertLength(3, self.hpss_calls)

    def test_stacked_get_stream_topological(self):
        # Repository._get_source.get_stream() from a stacked repository with
        # topological sorting yields the full data from both stacked and
        # stacked upon sources in topological order.
        rev_ord, expected_revs = self.get_ordered_revs('knit', 'topological')
        self.assertEqual(expected_revs, rev_ord)
        # Getting topological sort requires VFS calls still - one of which is
        # pushing up from the bound branch.
        self.assertLength(14, self.hpss_calls)

    def test_stacked_get_stream_groupcompress(self):
        # Repository._get_source.get_stream() from a stacked repository with
        # groupcompress sorting yields the full data from both stacked and
        # stacked upon sources in groupcompress order.
        raise tests.TestSkipped('No groupcompress ordered format available')
        rev_ord, expected_revs = self.get_ordered_revs('dev5', 'groupcompress')
        self.assertEqual(expected_revs, reversed(rev_ord))
        # Getting unordered results should have made a streaming data request
        # from the backing branch, and one from the stacked on branch.
        self.assertLength(2, self.hpss_calls)

    def test_stacked_pull_more_than_stacking_has_bug_360791(self):
        # When pulling some fixed amount of content that is more than the
        # source has (because some is coming from a fallback branch, no error
        # should be received. This was reported as bug 360791.
        # Need three branches: a trunk, a stacked branch, and a preexisting
        # branch pulling content from stacked and trunk.
        self.setup_smart_server_with_call_log()
        trunk = self.make_branch_and_tree('trunk', format="1.9-rich-root")
        r1 = trunk.commit('start')
        stacked_branch = trunk.branch.create_clone_on_transport(
            self.get_transport('stacked'), stacked_on=trunk.branch.base)
        local = self.make_branch('local', format='1.9-rich-root')
        local.repository.fetch(stacked_branch.repository,
            stacked_branch.last_revision())


class TestRemoteBranchEffort(tests.TestCaseWithTransport):

    def setUp(self):
        super(TestRemoteBranchEffort, self).setUp()
        # Create a smart server that publishes whatever the backing VFS server
        # does.
        self.smart_server = test_server.SmartTCPServer_for_testing()
        self.start_server(self.smart_server, self.get_server())
        # Log all HPSS calls into self.hpss_calls.
        _SmartClient.hooks.install_named_hook(
            'call', self.capture_hpss_call, None)
        self.hpss_calls = []

    def capture_hpss_call(self, params):
        self.hpss_calls.append(params.method)

    def test_copy_content_into_avoids_revision_history(self):
        local = self.make_branch('local')
        builder = self.make_branch_builder('remote')
        builder.build_commit(message="Commit.")
        remote_branch_url = self.smart_server.get_url() + 'remote'
        remote_branch = bzrdir.BzrDir.open(remote_branch_url).open_branch()
        local.repository.fetch(remote_branch.repository)
        self.hpss_calls = []
        remote_branch.copy_content_into(local)
        self.assertFalse('Branch.revision_history' in self.hpss_calls)

    def test_fetch_everything_needs_just_one_call(self):
        local = self.make_branch('local')
        builder = self.make_branch_builder('remote')
        builder.build_commit(message="Commit.")
        remote_branch_url = self.smart_server.get_url() + 'remote'
        remote_branch = bzrdir.BzrDir.open(remote_branch_url).open_branch()
        self.hpss_calls = []
        local.repository.fetch(
            remote_branch.repository,
            fetch_spec=_mod_graph.EverythingResult(remote_branch.repository))
        self.assertEqual(['Repository.get_stream_1.19'], self.hpss_calls)

    def override_verb(self, verb_name, verb):
        request_handlers = request.request_handlers
        orig_verb = request_handlers.get(verb_name)
        request_handlers.register(verb_name, verb, override_existing=True)
        self.addCleanup(request_handlers.register, verb_name, orig_verb,
                override_existing=True)

    def test_fetch_everything_backwards_compat(self):
        """Can fetch with EverythingResult even with pre 2.4 servers.
        
        Pre-2.4 do not support 'everything' searches with the
        Repository.get_stream_1.19 verb.
        """
        verb_log = []
        class OldGetStreamVerb(SmartServerRepositoryGetStream_1_19):
            """A version of the Repository.get_stream_1.19 verb patched to
            reject 'everything' searches the way 2.3 and earlier do.
            """
            def recreate_search(self, repository, search_bytes,
                                discard_excess=False):
                verb_log.append(search_bytes.split('\n', 1)[0])
                if search_bytes == 'everything':
                    return (None,
                            request.FailedSmartServerResponse(('BadSearch',)))
                return super(OldGetStreamVerb,
                        self).recreate_search(repository, search_bytes,
                            discard_excess=discard_excess)
        self.override_verb('Repository.get_stream_1.19', OldGetStreamVerb)
        local = self.make_branch('local')
        builder = self.make_branch_builder('remote')
        builder.build_commit(message="Commit.")
        remote_branch_url = self.smart_server.get_url() + 'remote'
        remote_branch = bzrdir.BzrDir.open(remote_branch_url).open_branch()
        self.hpss_calls = []
        local.repository.fetch(
            remote_branch.repository,
            fetch_spec=_mod_graph.EverythingResult(remote_branch.repository))
        # make sure the overridden verb was used
        self.assertLength(1, verb_log)
        # more than one HPSS call is needed, but because it's a VFS callback
        # its hard to predict exactly how many.
        self.assertTrue(len(self.hpss_calls) > 1)


class TestUpdateBoundBranchWithModifiedBoundLocation(
    tests.TestCaseWithTransport):
    """Ensure correct handling of bound_location modifications.

    This is tested against a smart server as http://pad.lv/786980 was about a
    ReadOnlyError (write attempt during a read-only transaction) which can only
    happen in this context.
    """

    def setUp(self):
        super(TestUpdateBoundBranchWithModifiedBoundLocation, self).setUp()
        self.transport_server = test_server.SmartTCPServer_for_testing

    def make_master_and_checkout(self, master_name, checkout_name):
        # Create the master branch and its associated checkout
        self.master = self.make_branch_and_tree(master_name)
        self.checkout = self.master.branch.create_checkout(checkout_name)
        # Modify the master branch so there is something to update
        self.master.commit('add stuff')
        self.last_revid = self.master.commit('even more stuff')
        self.bound_location = self.checkout.branch.get_bound_location()

    def assertUpdateSucceeds(self, new_location):
        self.checkout.branch.set_bound_location(new_location)
        self.checkout.update()
        self.assertEquals(self.last_revid, self.checkout.last_revision())

    def test_without_final_slash(self):
        self.make_master_and_checkout('master', 'checkout')
        # For unclear reasons some users have a bound_location without a final
        # '/', simulate that by forcing such a value
        self.assertEndsWith(self.bound_location, '/')
        self.assertUpdateSucceeds(self.bound_location.rstrip('/'))

    def test_plus_sign(self):
        self.make_master_and_checkout('+master', 'checkout')
        self.assertUpdateSucceeds(self.bound_location.replace('%2B', '+', 1))

    def test_tilda(self):
        # Embed ~ in the middle of the path just to avoid any $HOME
        # interpretation
        self.make_master_and_checkout('mas~ter', 'checkout')
        self.assertUpdateSucceeds(self.bound_location.replace('%2E', '~', 1))
