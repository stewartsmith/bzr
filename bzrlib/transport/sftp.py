# Copyright (C) 2005 Robey Pointer <robey@lag.net>
# Copyright (C) 2005, 2006, 2007 Canonical Ltd
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

"""Implementation of Transport over SFTP, using paramiko."""

# TODO: Remove the transport-based lock_read and lock_write methods.  They'll
# then raise TransportNotPossible, which will break remote access to any
# formats which rely on OS-level locks.  That should be fine as those formats
# are pretty old, but these combinations may have to be removed from the test
# suite.  Those formats all date back to 0.7; so we should be able to remove
# these methods when we officially drop support for those formats.

import errno
import os
import random
import select
import socket
import stat
import sys
import time
import urllib
import urlparse
import weakref

from bzrlib import (
    errors,
    urlutils,
    )
from bzrlib.errors import (FileExists,
                           NoSuchFile, PathNotChild,
                           TransportError,
                           LockError,
                           PathError,
                           ParamikoNotPresent,
                           )
from bzrlib.osutils import pathjoin, fancy_rename, getcwd
from bzrlib.trace import mutter, warning
from bzrlib.transport import (
    register_urlparse_netloc_protocol,
    Server,
    split_url,
    ssh,
    Transport,
    )

try:
    import paramiko
except ImportError, e:
    raise ParamikoNotPresent(e)
else:
    from paramiko.sftp import (SFTP_FLAG_WRITE, SFTP_FLAG_CREATE,
                               SFTP_FLAG_EXCL, SFTP_FLAG_TRUNC,
                               CMD_HANDLE, CMD_OPEN)
    from paramiko.sftp_attr import SFTPAttributes
    from paramiko.sftp_file import SFTPFile


register_urlparse_netloc_protocol('sftp')


# This is a weakref dictionary, so that we can reuse connections
# that are still active. Long term, it might be nice to have some
# sort of expiration policy, such as disconnect if inactive for
# X seconds. But that requires a lot more fanciness.
_connected_hosts = weakref.WeakValueDictionary()


_paramiko_version = getattr(paramiko, '__version_info__', (0, 0, 0))
# don't use prefetch unless paramiko version >= 1.5.5 (there were bugs earlier)
_default_do_prefetch = (_paramiko_version >= (1, 5, 5))


def clear_connection_cache():
    """Remove all hosts from the SFTP connection cache.

    Primarily useful for test cases wanting to force garbage collection.
    """
    _connected_hosts.clear()


class SFTPLock(object):
    """This fakes a lock in a remote location.
    
    A present lock is indicated just by the existence of a file.  This
    doesn't work well on all transports and they are only used in 
    deprecated storage formats.
    """
    
    __slots__ = ['path', 'lock_path', 'lock_file', 'transport']

    def __init__(self, path, transport):
        assert isinstance(transport, SFTPTransport)

        self.lock_file = None
        self.path = path
        self.lock_path = path + '.write-lock'
        self.transport = transport
        try:
            # RBC 20060103 FIXME should we be using private methods here ?
            abspath = transport._remote_path(self.lock_path)
            self.lock_file = transport._sftp_open_exclusive(abspath)
        except FileExists:
            raise LockError('File %r already locked' % (self.path,))

    def __del__(self):
        """Should this warn, or actually try to cleanup?"""
        if self.lock_file:
            warning("SFTPLock %r not explicitly unlocked" % (self.path,))
            self.unlock()

    def unlock(self):
        if not self.lock_file:
            return
        self.lock_file.close()
        self.lock_file = None
        try:
            self.transport.delete(self.lock_path)
        except (NoSuchFile,):
            # What specific errors should we catch here?
            pass


class SFTPUrlHandling(Transport):
    """Mix-in that does common handling of SSH/SFTP URLs."""

    def __init__(self, base):
        self._parse_url(base)
        base = self._unparse_url(self._path)
        if base[-1] != '/':
            base += '/'
        super(SFTPUrlHandling, self).__init__(base)

    def _parse_url(self, url):
        (self._scheme,
         self._username, self._password,
         self._host, self._port, self._path) = self._split_url(url)

    def _unparse_url(self, path):
        """Return a URL for a path relative to this transport.
        """
        path = urllib.quote(path)
        # handle homedir paths
        if not path.startswith('/'):
            path = "/~/" + path
        netloc = urllib.quote(self._host)
        if self._username is not None:
            netloc = '%s@%s' % (urllib.quote(self._username), netloc)
        if self._port is not None:
            netloc = '%s:%d' % (netloc, self._port)
        return urlparse.urlunparse((self._scheme, netloc, path, '', '', ''))

    def _split_url(self, url):
        (scheme, username, password, host, port, path) = split_url(url)
        ## assert scheme == 'sftp'

        # the initial slash should be removed from the path, and treated
        # as a homedir relative path (the path begins with a double slash
        # if it is absolute).
        # see draft-ietf-secsh-scp-sftp-ssh-uri-03.txt
        # RBC 20060118 we are not using this as its too user hostile. instead
        # we are following lftp and using /~/foo to mean '~/foo'.
        # handle homedir paths
        if path.startswith('/~/'):
            path = path[3:]
        elif path == '/~':
            path = ''
        return (scheme, username, password, host, port, path)

    def abspath(self, relpath):
        """Return the full url to the given relative path.
        
        @param relpath: the relative path or path components
        @type relpath: str or list
        """
        return self._unparse_url(self._remote_path(relpath))
    
    def _remote_path(self, relpath):
        """Return the path to be passed along the sftp protocol for relpath.
        
        :param relpath: is a urlencoded string.
        """
        return self._combine_paths(self._path, relpath)


class SFTPTransport(SFTPUrlHandling):
    """Transport implementation for SFTP access."""

    _do_prefetch = _default_do_prefetch
    # TODO: jam 20060717 Conceivably these could be configurable, either
    #       by auto-tuning at run-time, or by a configuration (per host??)
    #       but the performance curve is pretty flat, so just going with
    #       reasonable defaults.
    _max_readv_combine = 200
    # Having to round trip to the server means waiting for a response,
    # so it is better to download extra bytes.
    # 8KiB had good performance for both local and remote network operations
    _bytes_to_read_before_seek = 8192

    # The sftp spec says that implementations SHOULD allow reads
    # to be at least 32K. paramiko.readv() does an async request
    # for the chunks. So we need to keep it within a single request
    # size for paramiko <= 1.6.1. paramiko 1.6.2 will probably chop
    # up the request itself, rather than us having to worry about it
    _max_request_size = 32768

    def __init__(self, base, clone_from=None):
        super(SFTPTransport, self).__init__(base)
        if clone_from is None:
            self._sftp_connect()
        else:
            # use the same ssh connection, etc
            self._sftp = clone_from._sftp
        # super saves 'self.base'
    
    def should_cache(self):
        """
        Return True if the data pulled across should be cached locally.
        """
        return True

    def clone(self, offset=None):
        """
        Return a new SFTPTransport with root at self.base + offset.
        We share the same SFTP session between such transports, because it's
        fairly expensive to set them up.
        """
        if offset is None:
            return SFTPTransport(self.base, self)
        else:
            return SFTPTransport(self.abspath(offset), self)

    def _remote_path(self, relpath):
        """Return the path to be passed along the sftp protocol for relpath.
        
        relpath is a urlencoded string.

        :return: a path prefixed with / for regular abspath-based urls, or a
            path that does not begin with / for urls which begin with /~/.
        """
        # how does this work? 
        # it processes relpath with respect to 
        # our state:
        # firstly we create a path to evaluate: 
        # if relpath is an abspath or homedir path, its the entire thing
        # otherwise we join our base with relpath
        # then we eliminate all empty segments (double //'s) outside the first
        # two elements of the list. This avoids problems with trailing 
        # slashes, or other abnormalities.
        # finally we evaluate the entire path in a single pass
        # '.'s are stripped,
        # '..' result in popping the left most already 
        # processed path (which can never be empty because of the check for
        # abspath and homedir meaning that its not, or that we've used our
        # path. If the pop would pop the root, we ignore it.

        # Specific case examinations:
        # remove the special casefor ~: if the current root is ~/ popping of it
        # = / thus our seed for a ~ based path is ['', '~']
        # and if we end up with [''] then we had basically ('', '..') (which is
        # '/..' so we append '' if the length is one, and assert that the first
        # element is still ''. Lastly, if we end with ['', '~'] as a prefix for
        # the output, we've got a homedir path, so we strip that prefix before
        # '/' joining the resulting list.
        #
        # case one: '/' -> ['', ''] cannot shrink
        # case two: '/' + '../foo' -> ['', 'foo'] (take '', '', '..', 'foo')
        #           and pop the second '' for the '..', append 'foo'
        # case three: '/~/' -> ['', '~', ''] 
        # case four: '/~/' + '../foo' -> ['', '~', '', '..', 'foo'],
        #           and we want to get '/foo' - the empty path in the middle
        #           needs to be stripped, then normal path manipulation will 
        #           work.
        # case five: '/..' ['', '..'], we want ['', '']
        #            stripping '' outside the first two is ok
        #            ignore .. if its too high up
        #
        # lastly this code is possibly reusable by FTP, but not reusable by
        # local paths: ~ is resolvable correctly, nor by HTTP or the smart
        # server: ~ is resolved remotely.
        # 
        # however, a version of this that acts on self.base is possible to be
        # written which manipulates the URL in canonical form, and would be
        # reusable for all transports, if a flag for allowing ~/ at all was
        # provided.
        assert isinstance(relpath, basestring)
        relpath = urlutils.unescape(relpath)

        # case 1)
        if relpath.startswith('/'):
            # abspath - normal split is fine.
            current_path = relpath.split('/')
        elif relpath.startswith('~/'):
            # root is homedir based: normal split and prefix '' to remote the
            # special case
            current_path = [''].extend(relpath.split('/'))
        else:
            # root is from the current directory:
            if self._path.startswith('/'):
                # abspath, take the regular split
                current_path = []
            else:
                # homedir based, add the '', '~' not present in self._path
                current_path = ['', '~']
            # add our current dir
            current_path.extend(self._path.split('/'))
            # add the users relpath
            current_path.extend(relpath.split('/'))
        # strip '' segments that are not in the first one - the leading /.
        to_process = current_path[:1]
        for segment in current_path[1:]:
            if segment != '':
                to_process.append(segment)

        # process '.' and '..' segments into output_path.
        output_path = []
        for segment in to_process:
            if segment == '..':
                # directory pop. Remove a directory 
                # as long as we are not at the root
                if len(output_path) > 1:
                    output_path.pop()
                # else: pass
                # cannot pop beyond the root, so do nothing
            elif segment == '.':
                continue # strip the '.' from the output.
            else:
                # this will append '' to output_path for the root elements,
                # which is appropriate: its why we strip '' in the first pass.
                output_path.append(segment)

        # check output special cases:
        if output_path == ['']:
            # [''] -> ['', '']
            output_path = ['', '']
        elif output_path[:2] == ['', '~']:
            # ['', '~', ...] -> ...
            output_path = output_path[2:]
        path = '/'.join(output_path)
        return path

    def relpath(self, abspath):
        scheme, username, password, host, port, path = self._split_url(abspath)
        error = []
        if (username != self._username):
            error.append('username mismatch')
        if (host != self._host):
            error.append('host mismatch')
        if (port != self._port):
            error.append('port mismatch')
        if (not path.startswith(self._path)):
            error.append('path mismatch')
        if error:
            extra = ': ' + ', '.join(error)
            raise PathNotChild(abspath, self.base, extra=extra)
        pl = len(self._path)
        return path[pl:].strip('/')

    def has(self, relpath):
        """
        Does the target location exist?
        """
        try:
            self._sftp.stat(self._remote_path(relpath))
            return True
        except IOError:
            return False

    def get(self, relpath):
        """
        Get the file at the given relative path.

        :param relpath: The relative path to the file
        """
        try:
            path = self._remote_path(relpath)
            f = self._sftp.file(path, mode='rb')
            if self._do_prefetch and (getattr(f, 'prefetch', None) is not None):
                f.prefetch()
            return f
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, path, ': error retrieving')

    def readv(self, relpath, offsets):
        """See Transport.readv()"""
        # We overload the default readv() because we want to use a file
        # that does not have prefetch enabled.
        # Also, if we have a new paramiko, it implements an async readv()
        if not offsets:
            return

        try:
            path = self._remote_path(relpath)
            fp = self._sftp.file(path, mode='rb')
            readv = getattr(fp, 'readv', None)
            if readv:
                return self._sftp_readv(fp, offsets, relpath)
            mutter('seek and read %s offsets', len(offsets))
            return self._seek_and_read(fp, offsets, relpath)
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, path, ': error retrieving')

    def _sftp_readv(self, fp, offsets, relpath='<unknown>'):
        """Use the readv() member of fp to do async readv.

        And then read them using paramiko.readv(). paramiko.readv()
        does not support ranges > 64K, so it caps the request size, and
        just reads until it gets all the stuff it wants
        """
        offsets = list(offsets)
        sorted_offsets = sorted(offsets)

        # The algorithm works as follows:
        # 1) Coalesce nearby reads into a single chunk
        #    This generates a list of combined regions, the total size
        #    and the size of the sub regions. This coalescing step is limited
        #    in the number of nearby chunks to combine, and is allowed to
        #    skip small breaks in the requests. Limiting it makes sure that
        #    we can start yielding some data earlier, and skipping means we
        #    make fewer requests. (Beneficial even when using async)
        # 2) Break up this combined regions into chunks that are smaller
        #    than 64KiB. Technically the limit is 65536, but we are a
        #    little bit conservative. This is because sftp has a maximum
        #    return chunk size of 64KiB (max size of an unsigned short)
        # 3) Issue a readv() to paramiko to create an async request for
        #    all of this data
        # 4) Read in the data as it comes back, until we've read one
        #    continuous section as determined in step 1
        # 5) Break up the full sections into hunks for the original requested
        #    offsets. And put them in a cache
        # 6) Check if the next request is in the cache, and if it is, remove
        #    it from the cache, and yield its data. Continue until no more
        #    entries are in the cache.
        # 7) loop back to step 4 until all data has been read
        #
        # TODO: jam 20060725 This could be optimized one step further, by
        #       attempting to yield whatever data we have read, even before
        #       the first coallesced section has been fully processed.

        # When coalescing for use with readv(), we don't really need to
        # use any fudge factor, because the requests are made asynchronously
        coalesced = list(self._coalesce_offsets(sorted_offsets,
                               limit=self._max_readv_combine,
                               fudge_factor=0,
                               ))
        requests = []
        for c_offset in coalesced:
            start = c_offset.start
            size = c_offset.length

            # We need to break this up into multiple requests
            while size > 0:
                next_size = min(size, self._max_request_size)
                requests.append((start, next_size))
                size -= next_size
                start += next_size

        mutter('SFTP.readv() %s offsets => %s coalesced => %s requests',
                len(offsets), len(coalesced), len(requests))

        # Queue the current read until we have read the full coalesced section
        cur_data = []
        cur_data_len = 0
        cur_coalesced_stack = iter(coalesced)
        cur_coalesced = cur_coalesced_stack.next()

        # Cache the results, but only until they have been fulfilled
        data_map = {}
        # turn the list of offsets into a stack
        offset_stack = iter(offsets)
        cur_offset_and_size = offset_stack.next()

        for data in fp.readv(requests):
            cur_data += data
            cur_data_len += len(data)

            if cur_data_len < cur_coalesced.length:
                continue
            assert cur_data_len == cur_coalesced.length, \
                "Somehow we read too much: %s != %s" % (cur_data_len,
                                                        cur_coalesced.length)
            all_data = ''.join(cur_data)
            cur_data = []
            cur_data_len = 0

            for suboffset, subsize in cur_coalesced.ranges:
                key = (cur_coalesced.start+suboffset, subsize)
                data_map[key] = all_data[suboffset:suboffset+subsize]

            # Now that we've read some data, see if we can yield anything back
            while cur_offset_and_size in data_map:
                this_data = data_map.pop(cur_offset_and_size)
                yield cur_offset_and_size[0], this_data
                cur_offset_and_size = offset_stack.next()

            # We read a coalesced entry, so mark it as done
            cur_coalesced = None
            # Now that we've read all of the data for this coalesced section
            # on to the next
            cur_coalesced = cur_coalesced_stack.next()

        if cur_coalesced is not None:
            raise errors.ShortReadvError(relpath, cur_coalesced.start,
                cur_coalesced.length, len(data))

    def put_file(self, relpath, f, mode=None):
        """
        Copy the file-like object into the location.

        :param relpath: Location to put the contents, relative to base.
        :param f:       File-like object.
        :param mode: The final mode for the file
        """
        final_path = self._remote_path(relpath)
        self._put(final_path, f, mode=mode)

    def _put(self, abspath, f, mode=None):
        """Helper function so both put() and copy_abspaths can reuse the code"""
        tmp_abspath = '%s.tmp.%.9f.%d.%d' % (abspath, time.time(),
                        os.getpid(), random.randint(0,0x7FFFFFFF))
        fout = self._sftp_open_exclusive(tmp_abspath, mode=mode)
        closed = False
        try:
            try:
                fout.set_pipelined(True)
                self._pump(f, fout)
            except (IOError, paramiko.SSHException), e:
                self._translate_io_exception(e, tmp_abspath)
            # XXX: This doesn't truly help like we would like it to.
            #      The problem is that openssh strips sticky bits. So while we
            #      can properly set group write permission, we lose the group
            #      sticky bit. So it is probably best to stop chmodding, and
            #      just tell users that they need to set the umask correctly.
            #      The attr.st_mode = mode, in _sftp_open_exclusive
            #      will handle when the user wants the final mode to be more 
            #      restrictive. And then we avoid a round trip. Unless 
            #      paramiko decides to expose an async chmod()

            # This is designed to chmod() right before we close.
            # Because we set_pipelined() earlier, theoretically we might 
            # avoid the round trip for fout.close()
            if mode is not None:
                self._sftp.chmod(tmp_abspath, mode)
            fout.close()
            closed = True
            self._rename_and_overwrite(tmp_abspath, abspath)
        except Exception, e:
            # If we fail, try to clean up the temporary file
            # before we throw the exception
            # but don't let another exception mess things up
            # Write out the traceback, because otherwise
            # the catch and throw destroys it
            import traceback
            mutter(traceback.format_exc())
            try:
                if not closed:
                    fout.close()
                self._sftp.remove(tmp_abspath)
            except:
                # raise the saved except
                raise e
            # raise the original with its traceback if we can.
            raise

    def _put_non_atomic_helper(self, relpath, writer, mode=None,
                               create_parent_dir=False,
                               dir_mode=None):
        abspath = self._remote_path(relpath)

        # TODO: jam 20060816 paramiko doesn't publicly expose a way to
        #       set the file mode at create time. If it does, use it.
        #       But for now, we just chmod later anyway.

        def _open_and_write_file():
            """Try to open the target file, raise error on failure"""
            fout = None
            try:
                try:
                    fout = self._sftp.file(abspath, mode='wb')
                    fout.set_pipelined(True)
                    writer(fout)
                except (paramiko.SSHException, IOError), e:
                    self._translate_io_exception(e, abspath,
                                                 ': unable to open')

                # This is designed to chmod() right before we close.
                # Because we set_pipelined() earlier, theoretically we might 
                # avoid the round trip for fout.close()
                if mode is not None:
                    self._sftp.chmod(abspath, mode)
            finally:
                if fout is not None:
                    fout.close()

        if not create_parent_dir:
            _open_and_write_file()
            return

        # Try error handling to create the parent directory if we need to
        try:
            _open_and_write_file()
        except NoSuchFile:
            # Try to create the parent directory, and then go back to
            # writing the file
            parent_dir = os.path.dirname(abspath)
            self._mkdir(parent_dir, dir_mode)
            _open_and_write_file()

    def put_file_non_atomic(self, relpath, f, mode=None,
                            create_parent_dir=False,
                            dir_mode=None):
        """Copy the file-like object into the target location.

        This function is not strictly safe to use. It is only meant to
        be used when you already know that the target does not exist.
        It is not safe, because it will open and truncate the remote
        file. So there may be a time when the file has invalid contents.

        :param relpath: The remote location to put the contents.
        :param f:       File-like object.
        :param mode:    Possible access permissions for new file.
                        None means do not set remote permissions.
        :param create_parent_dir: If we cannot create the target file because
                        the parent directory does not exist, go ahead and
                        create it, and then try again.
        """
        def writer(fout):
            self._pump(f, fout)
        self._put_non_atomic_helper(relpath, writer, mode=mode,
                                    create_parent_dir=create_parent_dir,
                                    dir_mode=dir_mode)

    def put_bytes_non_atomic(self, relpath, bytes, mode=None,
                             create_parent_dir=False,
                             dir_mode=None):
        def writer(fout):
            fout.write(bytes)
        self._put_non_atomic_helper(relpath, writer, mode=mode,
                                    create_parent_dir=create_parent_dir,
                                    dir_mode=dir_mode)

    def iter_files_recursive(self):
        """Walk the relative paths of all files in this transport."""
        queue = list(self.list_dir('.'))
        while queue:
            relpath = queue.pop(0)
            st = self.stat(relpath)
            if stat.S_ISDIR(st.st_mode):
                for i, basename in enumerate(self.list_dir(relpath)):
                    queue.insert(i, relpath+'/'+basename)
            else:
                yield relpath

    def _mkdir(self, abspath, mode=None):
        if mode is None:
            local_mode = 0777
        else:
            local_mode = mode
        try:
            self._sftp.mkdir(abspath, local_mode)
            if mode is not None:
                self._sftp.chmod(abspath, mode=mode)
        except (paramiko.SSHException, IOError), e:
            self._translate_io_exception(e, abspath, ': unable to mkdir',
                failure_exc=FileExists)

    def mkdir(self, relpath, mode=None):
        """Create a directory at the given path."""
        self._mkdir(self._remote_path(relpath), mode=mode)

    def _translate_io_exception(self, e, path, more_info='', 
                                failure_exc=PathError):
        """Translate a paramiko or IOError into a friendlier exception.

        :param e: The original exception
        :param path: The path in question when the error is raised
        :param more_info: Extra information that can be included,
                          such as what was going on
        :param failure_exc: Paramiko has the super fun ability to raise completely
                           opaque errors that just set "e.args = ('Failure',)" with
                           no more information.
                           If this parameter is set, it defines the exception 
                           to raise in these cases.
        """
        # paramiko seems to generate detailless errors.
        self._translate_error(e, path, raise_generic=False)
        if getattr(e, 'args', None) is not None:
            if (e.args == ('No such file or directory',) or
                e.args == ('No such file',)):
                raise NoSuchFile(path, str(e) + more_info)
            if (e.args == ('mkdir failed',)):
                raise FileExists(path, str(e) + more_info)
            # strange but true, for the paramiko server.
            if (e.args == ('Failure',)):
                raise failure_exc(path, str(e) + more_info)
            mutter('Raising exception with args %s', e.args)
        if getattr(e, 'errno', None) is not None:
            mutter('Raising exception with errno %s', e.errno)
        raise e

    def append_file(self, relpath, f, mode=None):
        """
        Append the text in the file-like object into the final
        location.
        """
        try:
            path = self._remote_path(relpath)
            fout = self._sftp.file(path, 'ab')
            if mode is not None:
                self._sftp.chmod(path, mode)
            result = fout.tell()
            self._pump(f, fout)
            return result
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, relpath, ': unable to append')

    def rename(self, rel_from, rel_to):
        """Rename without special overwriting"""
        try:
            self._sftp.rename(self._remote_path(rel_from),
                              self._remote_path(rel_to))
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, rel_from,
                    ': unable to rename to %r' % (rel_to))

    def _rename_and_overwrite(self, abs_from, abs_to):
        """Do a fancy rename on the remote server.
        
        Using the implementation provided by osutils.
        """
        try:
            fancy_rename(abs_from, abs_to,
                    rename_func=self._sftp.rename,
                    unlink_func=self._sftp.remove)
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, abs_from, ': unable to rename to %r' % (abs_to))

    def move(self, rel_from, rel_to):
        """Move the item at rel_from to the location at rel_to"""
        path_from = self._remote_path(rel_from)
        path_to = self._remote_path(rel_to)
        self._rename_and_overwrite(path_from, path_to)

    def delete(self, relpath):
        """Delete the item at relpath"""
        path = self._remote_path(relpath)
        try:
            self._sftp.remove(path)
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, path, ': unable to delete')
            
    def listable(self):
        """Return True if this store supports listing."""
        return True

    def list_dir(self, relpath):
        """
        Return a list of all files at the given location.
        """
        # does anything actually use this?
        # -- Unknown
        # This is at least used by copy_tree for remote upgrades.
        # -- David Allouche 2006-08-11
        path = self._remote_path(relpath)
        try:
            entries = self._sftp.listdir(path)
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, path, ': failed to list_dir')
        return [urlutils.escape(entry) for entry in entries]

    def rmdir(self, relpath):
        """See Transport.rmdir."""
        path = self._remote_path(relpath)
        try:
            return self._sftp.rmdir(path)
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, path, ': failed to rmdir')

    def stat(self, relpath):
        """Return the stat information for a file."""
        path = self._remote_path(relpath)
        try:
            return self._sftp.stat(path)
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, path, ': unable to stat')

    def lock_read(self, relpath):
        """
        Lock the given file for shared (read) access.
        :return: A lock object, which has an unlock() member function
        """
        # FIXME: there should be something clever i can do here...
        class BogusLock(object):
            def __init__(self, path):
                self.path = path
            def unlock(self):
                pass
        return BogusLock(relpath)

    def lock_write(self, relpath):
        """
        Lock the given file for exclusive (write) access.
        WARNING: many transports do not support this, so trying avoid using it

        :return: A lock object, which has an unlock() member function
        """
        # This is a little bit bogus, but basically, we create a file
        # which should not already exist, and if it does, we assume
        # that there is a lock, and if it doesn't, the we assume
        # that we have taken the lock.
        return SFTPLock(relpath, self)

    def _sftp_connect(self):
        """Connect to the remote sftp server.
        After this, self._sftp should have a valid connection (or
        we raise an TransportError 'could not connect').

        TODO: Raise a more reasonable ConnectionFailed exception
        """
        self._sftp = _sftp_connect(self._host, self._port, self._username,
                self._password)

    def _sftp_open_exclusive(self, abspath, mode=None):
        """Open a remote path exclusively.

        SFTP supports O_EXCL (SFTP_FLAG_EXCL), which fails if
        the file already exists. However it does not expose this
        at the higher level of SFTPClient.open(), so we have to
        sneak away with it.

        WARNING: This breaks the SFTPClient abstraction, so it
        could easily break against an updated version of paramiko.

        :param abspath: The remote absolute path where the file should be opened
        :param mode: The mode permissions bits for the new file
        """
        # TODO: jam 20060816 Paramiko >= 1.6.2 (probably earlier) supports
        #       using the 'x' flag to indicate SFTP_FLAG_EXCL.
        #       However, there is no way to set the permission mode at open 
        #       time using the sftp_client.file() functionality.
        path = self._sftp._adjust_cwd(abspath)
        # mutter('sftp abspath %s => %s', abspath, path)
        attr = SFTPAttributes()
        if mode is not None:
            attr.st_mode = mode
        omode = (SFTP_FLAG_WRITE | SFTP_FLAG_CREATE 
                | SFTP_FLAG_TRUNC | SFTP_FLAG_EXCL)
        try:
            t, msg = self._sftp._request(CMD_OPEN, path, omode, attr)
            if t != CMD_HANDLE:
                raise TransportError('Expected an SFTP handle')
            handle = msg.get_string()
            return SFTPFile(self._sftp, handle, 'wb', -1)
        except (paramiko.SSHException, IOError), e:
            self._translate_io_exception(e, abspath, ': unable to open',
                failure_exc=FileExists)

    def _can_roundtrip_unix_modebits(self):
        if sys.platform == 'win32':
            # anyone else?
            return False
        else:
            return True

# ------------- server test implementation --------------
import threading

from bzrlib.tests.stub_sftp import StubServer, StubSFTPServer

STUB_SERVER_KEY = """
-----BEGIN RSA PRIVATE KEY-----
MIICWgIBAAKBgQDTj1bqB4WmayWNPB+8jVSYpZYk80Ujvj680pOTh2bORBjbIAyz
oWGW+GUjzKxTiiPvVmxFgx5wdsFvF03v34lEVVhMpouqPAYQ15N37K/ir5XY+9m/
d8ufMCkjeXsQkKqFbAlQcnWMCRnOoPHS3I4vi6hmnDDeeYTSRvfLbW0fhwIBIwKB
gBIiOqZYaoqbeD9OS9z2K9KR2atlTxGxOJPXiP4ESqP3NVScWNwyZ3NXHpyrJLa0
EbVtzsQhLn6rF+TzXnOlcipFvjsem3iYzCpuChfGQ6SovTcOjHV9z+hnpXvQ/fon
soVRZY65wKnF7IAoUwTmJS9opqgrN6kRgCd3DASAMd1bAkEA96SBVWFt/fJBNJ9H
tYnBKZGw0VeHOYmVYbvMSstssn8un+pQpUm9vlG/bp7Oxd/m+b9KWEh2xPfv6zqU
avNwHwJBANqzGZa/EpzF4J8pGti7oIAPUIDGMtfIcmqNXVMckrmzQ2vTfqtkEZsA
4rE1IERRyiJQx6EJsz21wJmGV9WJQ5kCQQDwkS0uXqVdFzgHO6S++tjmjYcxwr3g
H0CoFYSgbddOT6miqRskOQF3DZVkJT3kyuBgU2zKygz52ukQZMqxCb1fAkASvuTv
qfpH87Qq5kQhNKdbbwbmd2NxlNabazPijWuphGTdW0VfJdWfklyS2Kr+iqrs/5wV
HhathJt636Eg7oIjAkA8ht3MQ+XSl9yIJIS8gVpbPxSw5OMfw0PjVE7tBdQruiSc
nvuQES5C9BMHjF39LZiGH1iLQy7FgdHyoP+eodI7
-----END RSA PRIVATE KEY-----
"""


class SocketListener(threading.Thread):

    def __init__(self, callback):
        threading.Thread.__init__(self)
        self._callback = callback
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(('localhost', 0))
        self._socket.listen(1)
        self.port = self._socket.getsockname()[1]
        self._stop_event = threading.Event()

    def stop(self):
        # called from outside this thread
        self._stop_event.set()
        # use a timeout here, because if the test fails, the server thread may
        # never notice the stop_event.
        self.join(5.0)
        self._socket.close()

    def run(self):
        while True:
            readable, writable_unused, exception_unused = \
                select.select([self._socket], [], [], 0.1)
            if self._stop_event.isSet():
                return
            if len(readable) == 0:
                continue
            try:
                s, addr_unused = self._socket.accept()
                # because the loopback socket is inline, and transports are
                # never explicitly closed, best to launch a new thread.
                threading.Thread(target=self._callback, args=(s,)).start()
            except socket.error, x:
                sys.excepthook(*sys.exc_info())
                warning('Socket error during accept() within unit test server'
                        ' thread: %r' % x)
            except Exception, x:
                # probably a failed test; unit test thread will log the
                # failure/error
                sys.excepthook(*sys.exc_info())
                warning('Exception from within unit test server thread: %r' % 
                        x)


class SocketDelay(object):
    """A socket decorator to make TCP appear slower.

    This changes recv, send, and sendall to add a fixed latency to each python
    call if a new roundtrip is detected. That is, when a recv is called and the
    flag new_roundtrip is set, latency is charged. Every send and send_all
    sets this flag.

    In addition every send, sendall and recv sleeps a bit per character send to
    simulate bandwidth.

    Not all methods are implemented, this is deliberate as this class is not a
    replacement for the builtin sockets layer. fileno is not implemented to
    prevent the proxy being bypassed. 
    """

    simulated_time = 0
    _proxied_arguments = dict.fromkeys([
        "close", "getpeername", "getsockname", "getsockopt", "gettimeout",
        "setblocking", "setsockopt", "settimeout", "shutdown"])

    def __init__(self, sock, latency, bandwidth=1.0, 
                 really_sleep=True):
        """ 
        :param bandwith: simulated bandwith (MegaBit)
        :param really_sleep: If set to false, the SocketDelay will just
        increase a counter, instead of calling time.sleep. This is useful for
        unittesting the SocketDelay.
        """
        self.sock = sock
        self.latency = latency
        self.really_sleep = really_sleep
        self.time_per_byte = 1 / (bandwidth / 8.0 * 1024 * 1024) 
        self.new_roundtrip = False

    def sleep(self, s):
        if self.really_sleep:
            time.sleep(s)
        else:
            SocketDelay.simulated_time += s

    def __getattr__(self, attr):
        if attr in SocketDelay._proxied_arguments:
            return getattr(self.sock, attr)
        raise AttributeError("'SocketDelay' object has no attribute %r" %
                             attr)

    def dup(self):
        return SocketDelay(self.sock.dup(), self.latency, self.time_per_byte,
                           self._sleep)

    def recv(self, *args):
        data = self.sock.recv(*args)
        if data and self.new_roundtrip:
            self.new_roundtrip = False
            self.sleep(self.latency)
        self.sleep(len(data) * self.time_per_byte)
        return data

    def sendall(self, data, flags=0):
        if not self.new_roundtrip:
            self.new_roundtrip = True
            self.sleep(self.latency)
        self.sleep(len(data) * self.time_per_byte)
        return self.sock.sendall(data, flags)

    def send(self, data, flags=0):
        if not self.new_roundtrip:
            self.new_roundtrip = True
            self.sleep(self.latency)
        bytes_sent = self.sock.send(data, flags)
        self.sleep(bytes_sent * self.time_per_byte)
        return bytes_sent


class SFTPServer(Server):
    """Common code for SFTP server facilities."""

    def __init__(self, server_interface=StubServer):
        self._original_vendor = None
        self._homedir = None
        self._server_homedir = None
        self._listener = None
        self._root = None
        self._vendor = ssh.ParamikoVendor()
        self._server_interface = server_interface
        # sftp server logs
        self.logs = []
        self.add_latency = 0

    def _get_sftp_url(self, path):
        """Calculate an sftp url to this server for path."""
        return 'sftp://foo:bar@localhost:%d/%s' % (self._listener.port, path)

    def log(self, message):
        """StubServer uses this to log when a new server is created."""
        self.logs.append(message)

    def _run_server_entry(self, sock):
        """Entry point for all implementations of _run_server.
        
        If self.add_latency is > 0.000001 then sock is given a latency adding
        decorator.
        """
        if self.add_latency > 0.000001:
            sock = SocketDelay(sock, self.add_latency)
        return self._run_server(sock)

    def _run_server(self, s):
        ssh_server = paramiko.Transport(s)
        key_file = pathjoin(self._homedir, 'test_rsa.key')
        f = open(key_file, 'w')
        f.write(STUB_SERVER_KEY)
        f.close()
        host_key = paramiko.RSAKey.from_private_key_file(key_file)
        ssh_server.add_server_key(host_key)
        server = self._server_interface(self)
        ssh_server.set_subsystem_handler('sftp', paramiko.SFTPServer,
                                         StubSFTPServer, root=self._root,
                                         home=self._server_homedir)
        event = threading.Event()
        ssh_server.start_server(event, server)
        event.wait(5.0)
    
    def setUp(self):
        self._original_vendor = ssh._ssh_vendor_manager._cached_ssh_vendor
        ssh._ssh_vendor_manager._cached_ssh_vendor = self._vendor
        if sys.platform == 'win32':
            # Win32 needs to use the UNICODE api
            self._homedir = getcwd()
        else:
            # But Linux SFTP servers should just deal in bytestreams
            self._homedir = os.getcwd()
        if self._server_homedir is None:
            self._server_homedir = self._homedir
        self._root = '/'
        if sys.platform == 'win32':
            self._root = ''
        self._listener = SocketListener(self._run_server_entry)
        self._listener.setDaemon(True)
        self._listener.start()

    def tearDown(self):
        """See bzrlib.transport.Server.tearDown."""
        self._listener.stop()
        ssh._ssh_vendor_manager._cached_ssh_vendor = self._original_vendor

    def get_bogus_url(self):
        """See bzrlib.transport.Server.get_bogus_url."""
        # this is chosen to try to prevent trouble with proxies, wierd dns, etc
        # we bind a random socket, so that we get a guaranteed unused port
        # we just never listen on that port
        s = socket.socket()
        s.bind(('localhost', 0))
        return 'sftp://%s:%s/' % s.getsockname()


class SFTPFullAbsoluteServer(SFTPServer):
    """A test server for sftp transports, using absolute urls and ssh."""

    def get_url(self):
        """See bzrlib.transport.Server.get_url."""
        return self._get_sftp_url(urlutils.escape(self._homedir[1:]))


class SFTPServerWithoutSSH(SFTPServer):
    """An SFTP server that uses a simple TCP socket pair rather than SSH."""

    def __init__(self):
        super(SFTPServerWithoutSSH, self).__init__()
        self._vendor = ssh.LoopbackVendor()

    def _run_server(self, sock):
        # Re-import these as locals, so that they're still accessible during
        # interpreter shutdown (when all module globals get set to None, leading
        # to confusing errors like "'NoneType' object has no attribute 'error'".
        class FakeChannel(object):
            def get_transport(self):
                return self
            def get_log_channel(self):
                return 'paramiko'
            def get_name(self):
                return '1'
            def get_hexdump(self):
                return False
            def close(self):
                pass

        server = paramiko.SFTPServer(FakeChannel(), 'sftp', StubServer(self), StubSFTPServer,
                                     root=self._root, home=self._server_homedir)
        try:
            server.start_subsystem('sftp', None, sock)
        except socket.error, e:
            if (len(e.args) > 0) and (e.args[0] == errno.EPIPE):
                # it's okay for the client to disconnect abruptly
                # (bug in paramiko 1.6: it should absorb this exception)
                pass
            else:
                raise
        except Exception, e:
            # This typically seems to happen during interpreter shutdown, so
            # most of the useful ways to report this error are won't work.
            # Writing the exception type, and then the text of the exception,
            # seems to be the best we can do.
            import sys
            sys.stderr.write('\nEXCEPTION %r: ' % (e.__class__,))
            sys.stderr.write('%s\n\n' % (e,))
        server.finish_subsystem()


class SFTPAbsoluteServer(SFTPServerWithoutSSH):
    """A test server for sftp transports, using absolute urls."""

    def get_url(self):
        """See bzrlib.transport.Server.get_url."""
        if sys.platform == 'win32':
            return self._get_sftp_url(urlutils.escape(self._homedir))
        else:
            return self._get_sftp_url(urlutils.escape(self._homedir[1:]))


class SFTPHomeDirServer(SFTPServerWithoutSSH):
    """A test server for sftp transports, using homedir relative urls."""

    def get_url(self):
        """See bzrlib.transport.Server.get_url."""
        return self._get_sftp_url("~/")


class SFTPSiblingAbsoluteServer(SFTPAbsoluteServer):
    """A test servere for sftp transports, using absolute urls to non-home."""

    def setUp(self):
        self._server_homedir = '/dev/noone/runs/tests/here'
        super(SFTPSiblingAbsoluteServer, self).setUp()


def _sftp_connect(host, port, username, password):
    """Connect to the remote sftp server.

    :raises: a TransportError 'could not connect'.

    :returns: an paramiko.sftp_client.SFTPClient

    TODO: Raise a more reasonable ConnectionFailed exception
    """
    idx = (host, port, username)
    try:
        return _connected_hosts[idx]
    except KeyError:
        pass
    
    sftp = _sftp_connect_uncached(host, port, username, password)
    _connected_hosts[idx] = sftp
    return sftp

def _sftp_connect_uncached(host, port, username, password):
    vendor = ssh._get_ssh_vendor()
    sftp = vendor.connect_sftp(username, password, host, port)
    return sftp


def get_test_permutations():
    """Return the permutations to be used in testing."""
    return [(SFTPTransport, SFTPAbsoluteServer),
            (SFTPTransport, SFTPHomeDirServer),
            (SFTPTransport, SFTPSiblingAbsoluteServer),
            ]
