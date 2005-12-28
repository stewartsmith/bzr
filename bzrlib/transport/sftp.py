# Copyright (C) 2005 Robey Pointer <robey@lag.net>, Canonical Ltd

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Implementation of Transport over SFTP, using paramiko."""

import errno
import getpass
import os
import re
import stat
import sys
import urllib
import urlparse
import time
import random
import subprocess
import weakref

from bzrlib.errors import (ConnectionError,
                           FileExists, 
                           TransportNotPossible, NoSuchFile, PathNotChild,
                           TransportError,
                           LockError
                           )
from bzrlib.config import config_dir
from bzrlib.trace import mutter, warning, error
from bzrlib.transport import Transport, Server, urlescape
import bzrlib.ui

try:
    import paramiko
except ImportError:
    error('The SFTP transport requires paramiko.')
    raise
else:
    from paramiko.sftp import (SFTP_FLAG_WRITE, SFTP_FLAG_CREATE,
                               SFTP_FLAG_EXCL, SFTP_FLAG_TRUNC,
                               CMD_HANDLE, CMD_OPEN)
    from paramiko.sftp_attr import SFTPAttributes
    from paramiko.sftp_file import SFTPFile
    from paramiko.sftp_client import SFTPClient

if 'sftp' not in urlparse.uses_netloc: urlparse.uses_netloc.append('sftp')


_close_fds = True
if sys.platform == 'win32':
    # close_fds not supported on win32
    _close_fds = False

_ssh_vendor = None
def _get_ssh_vendor():
    """Find out what version of SSH is on the system."""
    global _ssh_vendor
    if _ssh_vendor is not None:
        return _ssh_vendor

    _ssh_vendor = 'none'

    try:
        p = subprocess.Popen(['ssh', '-V'],
                             close_fds=_close_fds,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        returncode = p.returncode
        stdout, stderr = p.communicate()
    except OSError:
        returncode = -1
        stdout = stderr = ''
    if 'OpenSSH' in stderr:
        mutter('ssh implementation is OpenSSH')
        _ssh_vendor = 'openssh'
    elif 'SSH Secure Shell' in stderr:
        mutter('ssh implementation is SSH Corp.')
        _ssh_vendor = 'ssh'

    if _ssh_vendor != 'none':
        return _ssh_vendor

    # XXX: 20051123 jamesh
    # A check for putty's plink or lsh would go here.

    mutter('falling back to paramiko implementation')
    return _ssh_vendor


class SFTPSubprocess:
    """A socket-like object that talks to an ssh subprocess via pipes."""
    def __init__(self, hostname, vendor, port=None, user=None):
        assert vendor in ['openssh', 'ssh']
        if vendor == 'openssh':
            args = ['ssh',
                    '-oForwardX11=no', '-oForwardAgent=no',
                    '-oClearAllForwardings=yes', '-oProtocol=2',
                    '-oNoHostAuthenticationForLocalhost=yes']
            if port is not None:
                args.extend(['-p', str(port)])
            if user is not None:
                args.extend(['-l', user])
            args.extend(['-s', hostname, 'sftp'])
        elif vendor == 'ssh':
            args = ['ssh', '-x']
            if port is not None:
                args.extend(['-p', str(port)])
            if user is not None:
                args.extend(['-l', user])
            args.extend(['-s', 'sftp', hostname])

        self.proc = subprocess.Popen(args, close_fds=_close_fds,
                                     stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE)

    def send(self, data):
        return os.write(self.proc.stdin.fileno(), data)

    def recv(self, count):
        return os.read(self.proc.stdout.fileno(), count)

    def close(self):
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.proc.wait()


SYSTEM_HOSTKEYS = {}
BZR_HOSTKEYS = {}

# This is a weakref dictionary, so that we can reuse connections
# that are still active. Long term, it might be nice to have some
# sort of expiration policy, such as disconnect if inactive for
# X seconds. But that requires a lot more fanciness.
_connected_hosts = weakref.WeakValueDictionary()

def load_host_keys():
    """
    Load system host keys (probably doesn't work on windows) and any
    "discovered" keys from previous sessions.
    """
    global SYSTEM_HOSTKEYS, BZR_HOSTKEYS
    try:
        SYSTEM_HOSTKEYS = paramiko.util.load_host_keys(os.path.expanduser('~/.ssh/known_hosts'))
    except Exception, e:
        mutter('failed to load system host keys: ' + str(e))
    bzr_hostkey_path = os.path.join(config_dir(), 'ssh_host_keys')
    try:
        BZR_HOSTKEYS = paramiko.util.load_host_keys(bzr_hostkey_path)
    except Exception, e:
        mutter('failed to load bzr host keys: ' + str(e))
        save_host_keys()

def save_host_keys():
    """
    Save "discovered" host keys in $(config)/ssh_host_keys/.
    """
    global SYSTEM_HOSTKEYS, BZR_HOSTKEYS
    bzr_hostkey_path = os.path.join(config_dir(), 'ssh_host_keys')
    if not os.path.isdir(config_dir()):
        os.mkdir(config_dir())
    try:
        f = open(bzr_hostkey_path, 'w')
        f.write('# SSH host keys collected by bzr\n')
        for hostname, keys in BZR_HOSTKEYS.iteritems():
            for keytype, key in keys.iteritems():
                f.write('%s %s %s\n' % (hostname, keytype, key.get_base64()))
        f.close()
    except IOError, e:
        mutter('failed to save bzr host keys: ' + str(e))


class SFTPLock(object):
    """This fakes a lock in a remote location."""
    __slots__ = ['path', 'lock_path', 'lock_file', 'transport']
    def __init__(self, path, transport):
        assert isinstance(transport, SFTPTransport)

        self.lock_file = None
        self.path = path
        self.lock_path = path + '.write-lock'
        self.transport = transport
        try:
            self.lock_file = transport._sftp_open_exclusive(self.lock_path)
        except FileExists:
            raise LockError('File %r already locked' % (self.path,))

    def __del__(self):
        """Should this warn, or actually try to cleanup?"""
        if self.lock_file:
            warn("SFTPLock %r not explicitly unlocked" % (self.path,))
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



class SFTPTransport (Transport):
    """
    Transport implementation for SFTP access.
    """
    _do_prefetch = False # Right now Paramiko's prefetch support causes things to hang

    def __init__(self, base, clone_from=None):
        assert base.startswith('sftp://')
        self._parse_url(base)
        base = self._unparse_url()
        if base[-1] != '/':
            base = base + '/'
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

    def abspath(self, relpath):
        """
        Return the full url to the given relative path.
        
        @param relpath: the relative path or path components
        @type relpath: str or list
        """
        return self._unparse_url(self._remote_path(relpath))
    
    def _remote_path(self, relpath):
        """Return the path to be passed along the sftp protocol for relpath.
        
        relpath is a urlencoded string.
        """
        # FIXME: share the common code across transports
        assert isinstance(relpath, basestring)
        relpath = urllib.unquote(relpath).split('/')
        basepath = self._path.split('/')
        if len(basepath) > 0 and basepath[-1] == '':
            basepath = basepath[:-1]

        for p in relpath:
            if p == '..':
                if len(basepath) == 0:
                    # In most filesystems, a request for the parent
                    # of root, just returns root.
                    continue
                basepath.pop()
            elif p == '.':
                continue # No-op
            else:
                basepath.append(p)

        path = '/'.join(basepath)
        return path

    def relpath(self, abspath):
        username, password, host, port, path = self._split_url(abspath)
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

    def get(self, relpath, decode=False):
        """
        Get the file at the given relative path.

        :param relpath: The relative path to the file
        """
        try:
            path = self._remote_path(relpath)
            f = self._sftp.file(path)
            if self._do_prefetch and hasattr(f, 'prefetch'):
                f.prefetch()
            return f
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, path, ': error retrieving')

    def get_partial(self, relpath, start, length=None):
        """
        Get just part of a file.

        :param relpath: Path to the file, relative to base
        :param start: The starting position to read from
        :param length: The length to read. A length of None indicates
                       read to the end of the file.
        :return: A file-like object containing at least the specified bytes.
                 Some implementations may return objects which can be read
                 past this length, but this is not guaranteed.
        """
        # TODO: implement get_partial_multi to help with knit support
        f = self.get(relpath)
        f.seek(start)
        if self._do_prefetch and hasattr(f, 'prefetch'):
            f.prefetch()
        return f

    def put(self, relpath, f):
        """
        Copy the file-like or string object into the location.

        :param relpath: Location to put the contents, relative to base.
        :param f:       File-like or string object.
        """
        final_path = self._remote_path(relpath)
        tmp_relpath = '%s.tmp.%.9f.%d.%d' % (relpath, time.time(),
                        os.getpid(), random.randint(0,0x7FFFFFFF))
        tmp_abspath = self._remote_path(tmp_relpath)
        fout = self._sftp_open_exclusive(tmp_relpath)

        try:
            try:
                self._pump(f, fout)
            except (paramiko.SSHException, IOError), e:
                self._translate_io_exception(e, relpath, ': unable to write')
        except Exception, e:
            # If we fail, try to clean up the temporary file
            # before we throw the exception
            # but don't let another exception mess things up
            try:
                fout.close()
                self._sftp.remove(tmp_abspath)
            except:
                pass
            raise e
        else:
            # sftp rename doesn't allow overwriting, so play tricks:
            tmp_safety = 'bzr.tmp.%.9f.%d.%d' % (time.time(), os.getpid(), random.randint(0, 0x7FFFFFFF))
            tmp_safety = self._remote_path(tmp_safety)
            try:
                self._sftp.rename(final_path, tmp_safety)
                file_existed = True
            except:
                file_existed = False
            success = False
            try:
                try:
                    self._sftp.rename(tmp_abspath, final_path)
                except (paramiko.SSHException, IOError), e:
                    self._translate_io_exception(e, relpath, ': unable to rename')
                else:
                    success = True
            finally:
                if file_existed:
                    if success:
                        self._sftp.unlink(tmp_safety)
                    else:
                        self._sftp.rename(tmp_safety, final_path)

    def iter_files_recursive(self):
        """Walk the relative paths of all files in this transport."""
        queue = list(self.list_dir('.'))
        while queue:
            relpath = urllib.quote(queue.pop(0))
            st = self.stat(relpath)
            if stat.S_ISDIR(st.st_mode):
                for i, basename in enumerate(self.list_dir(relpath)):
                    queue.insert(i, relpath+'/'+basename)
            else:
                yield relpath

    def mkdir(self, relpath):
        """Create a directory at the given path."""
        try:
            path = self._remote_path(relpath)
            self._sftp.mkdir(path)
        except (paramiko.SSHException, IOError), e:
            self._translate_io_exception(e, relpath, ': unable to mkdir',
                failure_exc=FileExists)

    def _translate_io_exception(self, e, path, more_info='', failure_exc=NoSuchFile):
        """Translate a paramiko or IOError into a friendlier exception.

        :param e: The original exception
        :param path: The path in question when the error is raised
        :param more_info: Extra information that can be included,
                          such as what was going on
        :param failure_exc: Paramiko has the super fun ability to raise completely
                           opaque errors that just set "e.args = ('Failure',)" with
                           no more information.
                           This sometimes means FileExists, but it also sometimes
                           means NoSuchFile
        """
        # paramiko seems to generate detailless errors.
        self._translate_error(e, path, raise_generic=False)
        if hasattr(e, 'args'):
            if (e.args == ('No such file or directory',) or
                e.args == ('No such file',)):
                raise NoSuchFile(path, str(e) + more_info)
            if (e.args == ('mkdir failed',)):
                raise FileExists(path, str(e) + more_info)
            # strange but true, for the paramiko server.
            if (e.args == ('Failure',)):
                raise failure_exc(path, str(e) + more_info)
        raise e

    def append(self, relpath, f):
        """
        Append the text in the file-like object into the final
        location.
        """
        try:
            path = self._remote_path(relpath)
            fout = self._sftp.file(path, 'ab')
            self._pump(f, fout)
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, relpath, ': unable to append')

    def copy(self, rel_from, rel_to):
        """Copy the item at rel_from to the location at rel_to"""
        path_from = self._remote_path(rel_from)
        path_to = self._remote_path(rel_to)
        self._copy_abspaths(path_from, path_to)

    def _copy_abspaths(self, path_from, path_to):
        """Copy files given an absolute path

        :param path_from: Path on remote server to read
        :param path_to: Path on remote server to write
        :return: None

        TODO: Should the destination location be atomically created?
              This has not been specified
        TODO: This should use some sort of remote copy, rather than
              pulling the data locally, and then writing it remotely
        """
        try:
            fin = self._sftp.file(path_from, 'rb')
            try:
                fout = self._sftp.file(path_to, 'wb')
                try:
                    fout.set_pipelined(True)
                    self._pump(fin, fout)
                finally:
                    fout.close()
            finally:
                fin.close()
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, path_from, ': unable copy to: %r' % path_to)

    def copy_to(self, relpaths, other, pb=None):
        """Copy a set of entries from self into another Transport.

        :param relpaths: A list/generator of entries to be copied.
        """
        if isinstance(other, SFTPTransport) and other._sftp is self._sftp:
            # Both from & to are on the same remote filesystem
            # We can use a remote copy, instead of pulling locally, and pushing

            total = self._get_total(relpaths)
            count = 0
            for path in relpaths:
                path_from = self._remote_path(relpath)
                path_to = other._remote_path(relpath)
                self._update_pb(pb, 'copy-to', count, total)
                self._copy_abspaths(path_from, path_to)
                count += 1
            return count
        else:
            return super(SFTPTransport, self).copy_to(relpaths, other, pb=pb)

        # The dummy implementation just does a simple get + put
        def copy_entry(path):
            other.put(path, self.get(path))

        return self._iterate_over(relpaths, copy_entry, pb, 'copy_to', expand=False)

    def move(self, rel_from, rel_to):
        """Move the item at rel_from to the location at rel_to"""
        path_from = self._remote_path(rel_from)
        path_to = self._remote_path(rel_to)
        try:
            self._sftp.rename(path_from, path_to)
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, path_from, ': unable to move to: %r' % path_to)

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
        path = self._remote_path(relpath)
        try:
            return self._sftp.listdir(path)
        except (IOError, paramiko.SSHException), e:
            self._translate_io_exception(e, path, ': failed to list_dir')

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


    def _unparse_url(self, path=None):
        if path is None:
            path = self._path
        path = urllib.quote(path)
        if path.startswith('/'):
            path = '/%2F' + path[1:]
        else:
            path = '/' + path
        netloc = urllib.quote(self._host)
        if self._username is not None:
            netloc = '%s@%s' % (urllib.quote(self._username), netloc)
        if self._port is not None:
            netloc = '%s:%d' % (netloc, self._port)

        return urlparse.urlunparse(('sftp', netloc, path, '', '', ''))

    def _split_url(self, url):
        if isinstance(url, unicode):
            url = url.encode('utf-8')
        (scheme, netloc, path, params,
         query, fragment) = urlparse.urlparse(url, allow_fragments=False)
        assert scheme == 'sftp'
        username = password = host = port = None
        if '@' in netloc:
            username, host = netloc.split('@', 1)
            if ':' in username:
                username, password = username.split(':', 1)
                password = urllib.unquote(password)
            username = urllib.unquote(username)
        else:
            host = netloc

        if ':' in host:
            host, port = host.rsplit(':', 1)
            try:
                port = int(port)
            except ValueError:
                # TODO: Should this be ConnectionError?
                raise TransportError('%s: invalid port number' % port)
        host = urllib.unquote(host)

        path = urllib.unquote(path)

        # the initial slash should be removed from the path, and treated
        # as a homedir relative path (the path begins with a double slash
        # if it is absolute).
        # see draft-ietf-secsh-scp-sftp-ssh-uri-03.txt
        if path.startswith('/'):
            path = path[1:]

        return (username, password, host, port, path)

    def _parse_url(self, url):
        (self._username, self._password,
         self._host, self._port, self._path) = self._split_url(url)

    def _sftp_connect(self):
        """Connect to the remote sftp server.
        After this, self._sftp should have a valid connection (or
        we raise an TransportError 'could not connect').

        TODO: Raise a more reasonable ConnectionFailed exception
        """
        global _connected_hosts

        idx = (self._host, self._port, self._username)
        try:
            self._sftp = _connected_hosts[idx]
            return
        except KeyError:
            pass
        
        vendor = _get_ssh_vendor()
        if vendor != 'none':
            sock = SFTPSubprocess(self._host, vendor, self._port,
                                  self._username)
            self._sftp = SFTPClient(sock)
        else:
            self._paramiko_connect()

        _connected_hosts[idx] = self._sftp

    def _paramiko_connect(self):
        global SYSTEM_HOSTKEYS, BZR_HOSTKEYS
        
        load_host_keys()

        try:
            t = paramiko.Transport((self._host, self._port or 22))
            t.start_client()
        except paramiko.SSHException, e:
            raise ConnectionError('Unable to reach SSH host %s:%d' %
                                  (self._host, self._port), e)
            
        server_key = t.get_remote_server_key()
        server_key_hex = paramiko.util.hexify(server_key.get_fingerprint())
        keytype = server_key.get_name()
        if SYSTEM_HOSTKEYS.has_key(self._host) and SYSTEM_HOSTKEYS[self._host].has_key(keytype):
            our_server_key = SYSTEM_HOSTKEYS[self._host][keytype]
            our_server_key_hex = paramiko.util.hexify(our_server_key.get_fingerprint())
        elif BZR_HOSTKEYS.has_key(self._host) and BZR_HOSTKEYS[self._host].has_key(keytype):
            our_server_key = BZR_HOSTKEYS[self._host][keytype]
            our_server_key_hex = paramiko.util.hexify(our_server_key.get_fingerprint())
        else:
            warning('Adding %s host key for %s: %s' % (keytype, self._host, server_key_hex))
            if not BZR_HOSTKEYS.has_key(self._host):
                BZR_HOSTKEYS[self._host] = {}
            BZR_HOSTKEYS[self._host][keytype] = server_key
            our_server_key = server_key
            our_server_key_hex = paramiko.util.hexify(our_server_key.get_fingerprint())
            save_host_keys()
        if server_key != our_server_key:
            filename1 = os.path.expanduser('~/.ssh/known_hosts')
            filename2 = os.path.join(config_dir(), 'ssh_host_keys')
            raise TransportError('Host keys for %s do not match!  %s != %s' % \
                (self._host, our_server_key_hex, server_key_hex),
                ['Try editing %s or %s' % (filename1, filename2)])

        self._sftp_auth(t)
        
        try:
            self._sftp = t.open_sftp_client()
        except paramiko.SSHException, e:
            raise ConnectionError('Unable to start sftp client %s:%d' %
                                  (self._host, self._port), e)

    def _sftp_auth(self, transport):
        # paramiko requires a username, but it might be none if nothing was supplied
        # use the local username, just in case.
        # We don't override self._username, because if we aren't using paramiko,
        # the username might be specified in ~/.ssh/config and we don't want to
        # force it to something else
        # Also, it would mess up the self.relpath() functionality
        username = self._username or getpass.getuser()

        # Paramiko tries to open a socket.AF_UNIX in order to connect
        # to ssh-agent. That attribute doesn't exist on win32 (it does in cygwin)
        # so we get an AttributeError exception. For now, just don't try to
        # connect to an agent if we are on win32
        if sys.platform != 'win32':
            agent = paramiko.Agent()
            for key in agent.get_keys():
                mutter('Trying SSH agent key %s' % paramiko.util.hexify(key.get_fingerprint()))
                try:
                    transport.auth_publickey(username, key)
                    return
                except paramiko.SSHException, e:
                    pass
        
        # okay, try finding id_rsa or id_dss?  (posix only)
        if self._try_pkey_auth(transport, paramiko.RSAKey, username, 'id_rsa'):
            return
        if self._try_pkey_auth(transport, paramiko.DSSKey, username, 'id_dsa'):
            return

        if self._password:
            try:
                transport.auth_password(username, self._password)
                return
            except paramiko.SSHException, e:
                pass

            # FIXME: Don't keep a password held in memory if you can help it
            #self._password = None

        # give up and ask for a password
        password = bzrlib.ui.ui_factory.get_password(
                prompt='SSH %(user)s@%(host)s password',
                user=username, host=self._host)
        try:
            transport.auth_password(username, password)
        except paramiko.SSHException, e:
            raise ConnectionError('Unable to authenticate to SSH host as %s@%s' %
                                  (username, self._host), e)

    def _try_pkey_auth(self, transport, pkey_class, username, filename):
        filename = os.path.expanduser('~/.ssh/' + filename)
        try:
            key = pkey_class.from_private_key_file(filename)
            transport.auth_publickey(username, key)
            return True
        except paramiko.PasswordRequiredException:
            password = bzrlib.ui.ui_factory.get_password(
                    prompt='SSH %(filename)s password',
                    filename=filename)
            try:
                key = pkey_class.from_private_key_file(filename, password)
                transport.auth_publickey(username, key)
                return True
            except paramiko.SSHException:
                mutter('SSH authentication via %s key failed.' % (os.path.basename(filename),))
        except paramiko.SSHException:
            mutter('SSH authentication via %s key failed.' % (os.path.basename(filename),))
        except IOError:
            pass
        return False

    def _sftp_open_exclusive(self, relpath):
        """Open a remote path exclusively.

        SFTP supports O_EXCL (SFTP_FLAG_EXCL), which fails if
        the file already exists. However it does not expose this
        at the higher level of SFTPClient.open(), so we have to
        sneak away with it.

        WARNING: This breaks the SFTPClient abstraction, so it
        could easily break against an updated version of paramiko.

        :param relpath: The relative path, where the file should be opened
        """
        path = self._sftp._adjust_cwd(self._remote_path(relpath))
        attr = SFTPAttributes()
        mode = (SFTP_FLAG_WRITE | SFTP_FLAG_CREATE 
                | SFTP_FLAG_TRUNC | SFTP_FLAG_EXCL)
        try:
            t, msg = self._sftp._request(CMD_OPEN, path, mode, attr)
            if t != CMD_HANDLE:
                raise TransportError('Expected an SFTP handle')
            handle = msg.get_string()
            return SFTPFile(self._sftp, handle, 'w', -1)
        except (paramiko.SSHException, IOError), e:
            self._translate_io_exception(e, relpath, ': unable to open',
                failure_exc=FileExists)


# ------------- server test implementation --------------
import socket
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
    

class SingleListener(threading.Thread):

    def __init__(self, callback):
        threading.Thread.__init__(self)
        self._callback = callback
        self._socket = socket.socket()
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(('localhost', 0))
        self._socket.listen(1)
        self.port = self._socket.getsockname()[1]
        self.stop_event = threading.Event()

    def run(self):
        s, _ = self._socket.accept()
        # now close the listen socket
        self._socket.close()
        self._callback(s, self.stop_event)
    
    def stop(self):
        self.stop_event.set()
        # We should consider waiting for the other thread
        # to stop, because otherwise we get spurious
        #   bzr: ERROR: Socket exception: Connection reset by peer (54)
        # because the test suite finishes before the thread has a chance
        # to close. (Especially when only running a few tests)
        
        
class SFTPServer(Server):
    """Common code for SFTP server facilities."""

    def _get_sftp_url(self, path):
        """Calculate a sftp url to this server for path."""
        return 'sftp://foo:bar@localhost:%d/%s' % (self._listener.port, path)

    def __init__(self):
        self._original_vendor = None
        self._homedir = None
        self._listener = None
        self._root = None
        # sftp server logs
        self.logs = []

    def log(self, message):
        """What to do here? do we need this? Its for the StubServer.."""
        self.logs.append(message)

    def _run_server(self, s, stop_event):
        ssh_server = paramiko.Transport(s)
        key_file = os.path.join(self._homedir, 'test_rsa.key')
        file(key_file, 'w').write(STUB_SERVER_KEY)
        host_key = paramiko.RSAKey.from_private_key_file(key_file)
        ssh_server.add_server_key(host_key)
        server = StubServer(self)
        ssh_server.set_subsystem_handler('sftp', paramiko.SFTPServer,
                                         StubSFTPServer, root=self._root,
                                         home=self._homedir)
        event = threading.Event()
        ssh_server.start_server(event, server)
        event.wait(5.0)
        stop_event.wait(30.0)

    def setUp(self):
        """See bzrlib.transport.Server.setUp."""
        # XXX: 20051124 jamesh
        # The tests currently pop up a password prompt when an external ssh
        # is used.  This forces the use of the paramiko implementation.
        global _ssh_vendor
        self._original_vendor = _ssh_vendor
        _ssh_vendor = 'none'
        self._homedir = os.getcwdu()
        self._root = '/'
        # FIXME WINDOWS: _root should be _homedir[0]:/
        self._listener = SingleListener(self._run_server)
        self._listener.setDaemon(True)
        self._listener.start()

    def tearDown(self):
        """See bzrlib.transport.Server.tearDown."""
        global _ssh_vendor
        self._listener.stop()
        _ssh_vendor = self._original_vendor


class SFTPAbsoluteServer(SFTPServer):
    """A test server for sftp transports, using absolute urls."""

    def get_url(self):
        """See bzrlib.transport.Server.get_url."""
        return self._get_sftp_url("%%2f%s" % 
                urlescape(self._homedir[1:]))


class SFTPHomeDirServer(SFTPServer):
    """A test server for sftp transports, using homedir relative urls."""

    def get_url(self):
        """See bzrlib.transport.Server.get_url."""
        return self._get_sftp_url("")
