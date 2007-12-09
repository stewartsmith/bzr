# Copyright (C) 2006, 2007 Canonical Ltd
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

"""Implementaion of urllib2 tailored to bzr needs

This file complements the urllib2 class hierarchy with custom classes.

For instance, we create a new HTTPConnection and HTTPSConnection that inherit
from the original urllib2.HTTP(s)Connection objects, but also have a new base
which implements a custom getresponse and cleanup_pipe handlers.

And then we implement custom HTTPHandler and HTTPSHandler classes, that use
the custom HTTPConnection classes.

We have a custom Response class, which lets us maintain a keep-alive
connection even for requests that urllib2 doesn't expect to contain body data.

And a custom Request class that lets us track redirections, and
handle authentication schemes.
"""

# TODO: now that we have -Dhttp most of the needs should be covered in a more
# accessible way (i.e. no need to edit the source), if experience confirms
# that, delete all DEBUG uses -- vila20071130 (happy birthday).
DEBUG = 0

# FIXME: Oversimplifying, two kind of exceptions should be
# raised, once a request is issued: URLError before we have been
# able to process the response, HTTPError after that. Process the
# response means we are able to leave the socket clean, so if we
# are not able to do that, we should close the connection. The
# actual code more or less do that, tests should be written to
# ensure that.

import httplib
import socket
import urllib
import urllib2
import urlparse
import re
import sys
import time

from bzrlib import __version__ as bzrlib_version
from bzrlib import (
    config,
    debug,
    errors,
    osutils,
    trace,
    transport,
    ui,
    )


# We define our own Response class to keep our httplib pipe clean
class Response(httplib.HTTPResponse):
    """Custom HTTPResponse, to avoid the need to decorate.

    httplib prefers to decorate the returned objects, rather
    than using a custom object.
    """

    # Some responses have bodies in which we have no interest
    _body_ignored_responses = [301,302, 303, 307, 401, 403, 404]

    def __init__(self, *args, **kwargs):
        httplib.HTTPResponse.__init__(self, *args, **kwargs)

    def begin(self):
        """Begin to read the response from the server.

        httplib assumes that some responses get no content and do
        not even attempt to read the body in that case, leaving
        the body in the socket, blocking the next request. Let's
        try to workaround that.
        """
        httplib.HTTPResponse.begin(self)
        if self.status in self._body_ignored_responses:
            if self.debuglevel > 0:
                print "For status: [%s]," % self.status,
                print "will ready body, length: ",
                if  self.length is not None:
                    print "[%d]" % self.length
                else:
                    print "None"
            if not (self.length is None or self.will_close):
                # In some cases, we just can't read the body not
                # even try or we may encounter a 104, 'Connection
                # reset by peer' error if there is indeed no body
                # and the server closed the connection just after
                # having issued the response headers (even if the
                # headers indicate a Content-Type...)
                body = self.fp.read(self.length)
                if self.debuglevel > 3:
                    # This one can be huge and is generally not interesting
                    print "Consumed body: [%s]" % body
            self.close()
        elif self.status == 200:
            # Whatever the request is, it went ok, so we surely don't want to
            # close the connection. Some cases are not correctly detected by
            # httplib.HTTPConnection.getresponse (called by
            # httplib.HTTPResponse.begin). The CONNECT response for the https
            # through proxy case is one.  Note: the 'will_close' below refers
            # to the "true" socket between us and the server, whereas the
            # 'close()' above refers to the copy of that socket created by
            # httplib for the response itself. So, in the if above we close the
            # socket to indicate that we are done with the response whereas
            # below we keep the socket with the server opened.
            self.will_close = False

    def finish(self):
        """Finish reading the body.

        In some cases, the client may have left some bytes to read in the
        body. That will block the next request to succeed if we use a
        persistent connection. If we don't use a persistent connection, well,
        nothing will block the next request since a new connection will be
        issued anyway.
        """
        if not self.isclosed():
            # Make sure nothing was left to be read on the socket
            data = self.read(self.length)
            self.close()


# Not inheriting from 'object' because httplib.HTTPConnection doesn't.
class AbstractHTTPConnection:
    """A custom HTTP(S) Connection, which can reset itself on a bad response"""

    response_class = Response

    def __init__(self):
        self._response = None

    def _mutter_connect(self):
        netloc = self.host
        if self.port is not None:
            netloc += ':%d' % self.port
        if self.proxied_host is not None:
            netloc += '(proxy for %s)' % self.proxied_host
        trace.mutter('* About to connect() to %s' % netloc)

    def getresponse(self):
        """Capture the response to be able to cleanup"""
        self._response = httplib.HTTPConnection.getresponse(self)
        return self._response

    def cleanup_pipe(self):
        """Make the connection believes the response have been fully handled.

        That makes the httplib.HTTPConnection happy
        """
        if self._response is not None:
            self._response.finish()
            self._response = None
        # Preserve our preciousss
        sock = self.sock
        self.sock = None
        # Let httplib.HTTPConnection do its housekeeping 
        self.close()
        # Restore our preciousss
        self.sock = sock


class HTTPConnection(AbstractHTTPConnection, httplib.HTTPConnection):

    # XXX: Needs refactoring at the caller level.
    def __init__(self, host, port=None, proxied_host=None):
        AbstractHTTPConnection.__init__(self)
        # Use strict=True since we don't support HTTP/0.9
        httplib.HTTPConnection.__init__(self, host, port, strict=True)
        self.proxied_host = proxied_host

    def connect(self):
        if 'http' in debug.debug_flags:
            self._mutter_connect()
        httplib.HTTPConnection.connect(self)


# Build the appropriate socket wrapper for ssl
try:
    import ssl # python 2.6
    _ssl_wrap_socket = ssl.wrap_socket
except ImportError:
    def _ssl_wrap_socket(sock, key_file, cert_file):
        ssl_sock = socket.ssl(sock, key_file, cert_file)
        return httplib.FakeSocket(sock, ssl_sock)


class HTTPSConnection(AbstractHTTPConnection, httplib.HTTPSConnection):

    def __init__(self, host, port=None, key_file=None, cert_file=None,
                 proxied_host=None):
        AbstractHTTPConnection.__init__(self)
        # Use strict=True since we don't support HTTP/0.9
        httplib.HTTPSConnection.__init__(self, host, port,
                                         key_file, cert_file, strict=True)
        self.proxied_host = proxied_host

    def connect(self):
        if 'http' in debug.debug_flags:
            self._mutter_connect()
        httplib.HTTPConnection.connect(self)
        if self.proxied_host is None:
            self.connect_to_origin()

    def connect_to_origin(self):
        self.sock = _ssl_wrap_socket(self.sock, self.key_file, self.cert_file)


class Request(urllib2.Request):
    """A custom Request object.

    urllib2 determines the request method heuristically (based on
    the presence or absence of data). We set the method
    statically.

    The Request object tracks:
    - the connection the request will be made on.
    - the authentication parameters needed to preventively set
      the authentication header once a first authentication have
       been made.
    """

    def __init__(self, method, url, data=None, headers={},
                 origin_req_host=None, unverifiable=False,
                 connection=None, parent=None,
                 accepted_errors=None):
        urllib2.Request.__init__(self, url, data, headers,
                                 origin_req_host, unverifiable)
        self.method = method
        self.connection = connection
        self.accepted_errors = accepted_errors
        # To handle redirections
        self.parent = parent
        self.redirected_to = None
        # Unless told otherwise, redirections are not followed
        self.follow_redirections = False
        # auth and proxy_auth are dicts containing, at least
        # (scheme, host, port, realm, user, password, protocol, path).
        # The dict entries are mostly handled by the AuthHandler.
        # Some authentication schemes may add more entries.
        self.auth = {}
        self.proxy_auth = {}
        self.proxied_host = None

    def get_method(self):
        return self.method

    def set_proxy(self, proxy, type):
        """Set the proxy and remember the proxied host."""
        self.proxied_host = self.get_host()
        urllib2.Request.set_proxy(self, proxy, type)


class _ConnectRequest(Request):

    def __init__(self, request):
        """Constructor
        
        :param request: the first request sent to the proxied host, already
            processed by the opener (i.e. proxied_host is already set).
        """
        # We give a fake url and redefine get_selector or urllib2 will be
        # confused
        Request.__init__(self, 'CONNECT', request.get_full_url(),
                         connection=request.connection)
        assert request.proxied_host is not None
        self.proxied_host = request.proxied_host

    def get_selector(self):
        return self.proxied_host

    def set_proxy(self, proxy, type):
        """Set the proxy without remembering the proxied host.

        We already know the proxied host by definition, the CONNECT request
        occurs only when the connection goes through a proxy. The usual
        processing (masquerade the request so that the connection is done to
        the proxy while the request is targeted at another host) does not apply
        here. In fact, the connection is already established with proxy and we
        just want to enable the SSL tunneling.
        """
        urllib2.Request.set_proxy(self, proxy, type)


class ConnectionHandler(urllib2.BaseHandler):
    """Provides connection-sharing by pre-processing requests.

    urllib2 provides no way to access the HTTPConnection object
    internally used. But we need it in order to achieve
    connection sharing. So, we add it to the request just before
    it is processed, and then we override the do_open method for
    http[s] requests in AbstractHTTPHandler.
    """

    handler_order = 1000 # after all pre-processings

    def create_connection(self, request, http_connection_class):
        host = request.get_host()
        if not host:
            # Just a bit of paranoia here, this should have been
            # handled in the higher levels
            raise errors.InvalidURL(request.get_full_url(), 'no host given.')

        # We create a connection (but it will not connect until the first
        # request is made)
        try:
            connection = http_connection_class(
                host, proxied_host=request.proxied_host)
        except httplib.InvalidURL, exception:
            # There is only one occurrence of InvalidURL in httplib
            raise errors.InvalidURL(request.get_full_url(),
                                    extra='nonnumeric port')

        return connection

    def capture_connection(self, request, http_connection_class):
        """Capture or inject the request connection.

        Two cases:
        - the request have no connection: create a new one,

        - the request have a connection: this one have been used
          already, let's capture it, so that we can give it to
          another transport to be reused. We don't do that
          ourselves: the Transport object get the connection from
          a first request and then propagate it, from request to
          request or to cloned transports.
        """
        connection = request.connection
        if connection is None:
            # Create a new one
            connection = self.create_connection(request, http_connection_class)
            request.connection = connection

        # All connections will pass here, propagate debug level
        connection.set_debuglevel(DEBUG)
        return request

    def http_request(self, request):
        return self.capture_connection(request, HTTPConnection)

    def https_request(self, request):
        return self.capture_connection(request, HTTPSConnection)


class AbstractHTTPHandler(urllib2.AbstractHTTPHandler):
    """A custom handler for HTTP(S) requests.

    We overrive urllib2.AbstractHTTPHandler to get a better
    control of the connection, the ability to implement new
    request types and return a response able to cope with
    persistent connections.
    """

    # We change our order to be before urllib2 HTTP[S]Handlers
    # and be chosen instead of them (the first http_open called
    # wins).
    handler_order = 400

    _default_headers = {'Pragma': 'no-cache',
                        'Cache-control': 'max-age=0',
                        'Connection': 'Keep-Alive',
                        # FIXME: Spell it User-*A*gent once we
                        # know how to properly avoid bogus
                        # urllib2 using capitalize() for headers
                        # instead of title(sp?).
                        'User-agent': 'bzr/%s (urllib)' % bzrlib_version,
                        'Accept': '*/*',
                        }

    def __init__(self):
        urllib2.AbstractHTTPHandler.__init__(self, debuglevel=DEBUG)

    def http_request(self, request):
        """Common headers setting"""

        request.headers.update(self._default_headers.copy())
        # FIXME: We may have to add the Content-Length header if
        # we have data to send.
        return request

    def retry_or_raise(self, http_class, request, first_try):
        """Retry the request (once) or raise the exception.

        urllib2 raises exception of application level kind, we
        just have to translate them.

        httplib can raise exceptions of transport level (badly
        formatted dialog, loss of connexion or socket level
        problems). In that case we should issue the request again
        (httplib will close and reopen a new connection if
        needed).
        """
        # When an exception occurs, we give back the original
        # Traceback or the bugs are hard to diagnose.
        exc_type, exc_val, exc_tb = sys.exc_info()
        if exc_type == socket.gaierror:
            # No need to retry, that will not help
            raise errors.ConnectionError("Couldn't resolve host '%s'"
                                         % request.get_origin_req_host(),
                                         orig_error=exc_val)
        elif isinstance(exc_val, httplib.ImproperConnectionState):
            # The httplib pipeline is in incorrect state, it's a bug in our
            # implementation.
            raise exc_type, exc_val, exc_tb
        else:
            if first_try:
                if self._debuglevel > 0:
                    print 'Received exception: [%r]' % exc_val
                    print '  On connection: [%r]' % request.connection
                    method = request.get_method()
                    url = request.get_full_url()
                    print '  Will retry, %s %r' % (method, url)
                request.connection.close()
                response = self.do_open(http_class, request, False)
            else:
                if self._debuglevel > 0:
                    print 'Received second exception: [%r]' % exc_val
                    print '  On connection: [%r]' % request.connection
                if exc_type in (httplib.BadStatusLine, httplib.UnknownProtocol):
                    # httplib.BadStatusLine and
                    # httplib.UnknownProtocol indicates that a
                    # bogus server was encountered or a bad
                    # connection (i.e. transient errors) is
                    # experimented, we have already retried once
                    # for that request so we raise the exception.
                    my_exception = errors.InvalidHttpResponse(
                        request.get_full_url(),
                        'Bad status line received',
                        orig_error=exc_val)
                else:
                    # All other exception are considered connection related.

                    # socket errors generally occurs for reasons
                    # far outside our scope, so closing the
                    # connection and retrying is the best we can
                    # do.

                    my_exception = errors.ConnectionError(
                        msg= 'while sending %s %s:' % (request.get_method(),
                                                       request.get_selector()),
                        orig_error=exc_val)

                if self._debuglevel > 0:
                    print 'On connection: [%r]' % request.connection
                    method = request.get_method()
                    url = request.get_full_url()
                    print '  Failed again, %s %r' % (method, url)
                    print '  Will raise: [%r]' % my_exception
                raise my_exception, None, exc_tb
        return response

    def do_open(self, http_class, request, first_try=True):
        """See urllib2.AbstractHTTPHandler.do_open for the general idea.

        The request will be retried once if it fails.
        """
        connection = request.connection
        assert connection is not None, \
            'Cannot process a request without a connection'

        # Get all the headers
        headers = {}
        headers.update(request.header_items())
        headers.update(request.unredirected_hdrs)

        try:
            method = request.get_method()
            url = request.get_selector()
            connection._send_request(method, url,
                                     # FIXME: implements 100-continue
                                     #None, # We don't send the body yet
                                     request.get_data(),
                                     headers)
            if 'http' in debug.debug_flags:
                trace.mutter('> %s %s' % (method, url))
                hdrs = ['%s: %s' % (k, v) for k,v in headers.items()]
                trace.mutter('> ' + '\n> '.join(hdrs) + '\n')
            if self._debuglevel > 0:
                print 'Request sent: [%r]' % request
            response = connection.getresponse()
            convert_to_addinfourl = True
        except (socket.gaierror, httplib.BadStatusLine, httplib.UnknownProtocol,
                socket.error, httplib.HTTPException):
            response = self.retry_or_raise(http_class, request, first_try)
            convert_to_addinfourl = False

# FIXME: HTTPConnection does not fully support 100-continue (the
# server responses are just ignored)

#        if code == 100:
#            mutter('Will send the body')
#            # We can send the body now
#            body = request.get_data()
#            if body is None:
#                raise URLError("No data given")
#            connection.send(body)
#            response = connection.getresponse()

        if self._debuglevel > 0:
            print 'Receives response: %r' % response
            print '  For: %r(%r)' % (request.get_method(),
                                     request.get_full_url())

        if convert_to_addinfourl:
            # Shamelessly copied from urllib2
            req = request
            r = response
            r.recv = r.read
            fp = socket._fileobject(r)
            resp = urllib2.addinfourl(fp, r.msg, req.get_full_url())
            resp.code = r.status
            resp.msg = r.reason
            resp.version = r.version
            if self._debuglevel > 0:
                print 'Create addinfourl: %r' % resp
                print '  For: %r(%r)' % (request.get_method(),
                                         request.get_full_url())
            if 'http' in debug.debug_flags:
                version = 'HTTP/%d.%d'
                try:
                    version = version % (resp.version / 10,
                                         resp.version % 10)
                except:
                    version = 'HTTP/%r' % resp.version
                trace.mutter('< %s %s %s' % (version, resp.code,
                                             resp.msg))
                # Use the raw header lines instead of treating resp.info() as a
                # dict since we may miss duplicated headers otherwise.
                hdrs = [h.rstrip('\r\n') for h in resp.info().headers]
                trace.mutter('< ' + '\n< '.join(hdrs) + '\n')
        else:
            resp = response
        return resp


class HTTPHandler(AbstractHTTPHandler):
    """A custom handler that just thunks into HTTPConnection"""

    def http_open(self, request):
        return self.do_open(HTTPConnection, request)


class HTTPSHandler(AbstractHTTPHandler):
    """A custom handler that just thunks into HTTPSConnection"""

    https_request = AbstractHTTPHandler.http_request

    def https_open(self, request):
        connection = request.connection
        assert isinstance(connection, HTTPSConnection)
        if connection.sock is None and \
                connection.proxied_host is not None and \
                request.get_method() != 'CONNECT' : # Don't loop
            # FIXME: We need a gazillion connection tests here, but we still
            # miss a https server :-( :
            # - with and without proxy
            # - with and without certificate
            # - with self-signed certificate
            # - with and without authentication
            # - with good and bad credentials (especially the proxy auth around
            #   CONNECT)
            # - with basic and digest schemes
            # - reconnection on errors
            # - connection persistence behaviour (including reconnection)

            # We are about to connect for the first time via a proxy, we must
            # issue a CONNECT request first to establish the encrypted link
            connect = _ConnectRequest(request)
            response = self.parent.open(connect)
            if response.code != 200:
                raise ConnectionError("Can't connect to %s via proxy %s" % (
                        connect.proxied_host, self.host))
            # Housekeeping
            connection.cleanup_pipe()
            # Establish the connection encryption 
            connection.connect_to_origin()
            # Propagate the connection to the original request
            request.connection = connection
        return self.do_open(HTTPSConnection, request)

class HTTPRedirectHandler(urllib2.HTTPRedirectHandler):
    """Handles redirect requests.

    We have to implement our own scheme because we use a specific
    Request object and because we want to implement a specific
    policy.
    """
    _debuglevel = DEBUG
    # RFC2616 says that only read requests should be redirected
    # without interacting with the user. But bzr use some
    # shortcuts to optimize against roundtrips which can leads to
    # write requests being issued before read requests of
    # containing dirs can be redirected. So we redirect write
    # requests in the same way which seems to respect the spirit
    # of the RFC if not its letter.

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """See urllib2.HTTPRedirectHandler.redirect_request"""
        # We would have preferred to update the request instead
        # of creating a new one, but the urllib2.Request object
        # has a too complicated creation process to provide a
        # simple enough equivalent update process. Instead, when
        # redirecting, we only update the following request in
        # the redirect chain with a reference to the parent
        # request .

        # Some codes make no sense in our context and are treated
        # as errors:

        # 300: Multiple choices for different representations of
        #      the URI. Using that mechanisn with bzr will violate the
        #      protocol neutrality of Transport.

        # 304: Not modified (SHOULD only occurs with conditional
        #      GETs which are not used by our implementation)

        # 305: Use proxy. I can't imagine this one occurring in
        #      our context-- vila/20060909

        # 306: Unused (if the RFC says so...)

        # If the code is 302 and the request is HEAD, some may
        # think that it is a sufficent hint that the file exists
        # and that we MAY avoid following the redirections. But
        # if we want to be sure, we MUST follow them.

        if code in (301, 302, 303, 307):
            return Request(req.get_method(),newurl,
                           headers = req.headers,
                           origin_req_host = req.get_origin_req_host(),
                           unverifiable = True,
                           # TODO: It will be nice to be able to
                           # detect virtual hosts sharing the same
                           # IP address, that will allow us to
                           # share the same connection...
                           connection = None,
                           parent = req,
                           )
        else:
            raise urllib2.HTTPError(req.get_full_url(), code, msg, headers, fp)

    def http_error_302(self, req, fp, code, msg, headers):
        """Requests the redirected to URI.

        Copied from urllib2 to be able to clean the pipe of the associated
        connection, *before* issuing the redirected request but *after* having
        eventually raised an error.
        """
        # Some servers (incorrectly) return multiple Location headers
        # (so probably same goes for URI).  Use first header.

        # TODO: Once we get rid of addinfourl objects, the
        # following will need to be updated to use correct case
        # for headers.
        if 'location' in headers:
            newurl = headers.getheaders('location')[0]
        elif 'uri' in headers:
            newurl = headers.getheaders('uri')[0]
        else:
            return
        if self._debuglevel > 0:
            print 'Redirected to: %s (followed: %r)' % (newurl,
                                                        req.follow_redirections)
        if req.follow_redirections is False:
            req.redirected_to = newurl
            return fp

        newurl = urlparse.urljoin(req.get_full_url(), newurl)

        # This call succeeds or raise an error. urllib2 returns
        # if redirect_request returns None, but our
        # redirect_request never returns None.
        redirected_req = self.redirect_request(req, fp, code, msg, headers,
                                               newurl)

        # loop detection
        # .redirect_dict has a key url if url was previously visited.
        if hasattr(req, 'redirect_dict'):
            visited = redirected_req.redirect_dict = req.redirect_dict
            if (visited.get(newurl, 0) >= self.max_repeats or
                len(visited) >= self.max_redirections):
                raise urllib2.HTTPError(req.get_full_url(), code,
                                        self.inf_msg + msg, headers, fp)
        else:
            visited = redirected_req.redirect_dict = req.redirect_dict = {}
        visited[newurl] = visited.get(newurl, 0) + 1

        # We can close the fp now that we are sure that we won't
        # use it with HTTPError.
        fp.close()
        # We have all we need already in the response
        req.connection.cleanup_pipe()

        return self.parent.open(redirected_req)

    http_error_301 = http_error_303 = http_error_307 = http_error_302


class ProxyHandler(urllib2.ProxyHandler):
    """Handles proxy setting.

    Copied and modified from urllib2 to be able to modify the request during
    the request pre-processing instead of modifying it at _open time. As we
    capture (or create) the connection object during request processing, _open
    time was too late.

    The main task is to modify the request so that the connection is done to
    the proxy while the request still refers to the destination host.

    Note: the proxy handling *may* modify the protocol used; the request may be
    against an https server proxied through an http proxy. So, https_request
    will be called, but later it's really http_open that will be called. This
    explains why we don't have to call self.parent.open as the urllib2 did.
    """

    # Proxies must be in front
    handler_order = 100
    _debuglevel = DEBUG

    def __init__(self, proxies=None):
        urllib2.ProxyHandler.__init__(self, proxies)
        # First, let's get rid of urllib2 implementation
        for type, proxy in self.proxies.items():
            if self._debuglevel > 0:
                print 'Will unbind %s_open for %r' % (type, proxy)
            delattr(self, '%s_open' % type)

        # We are interested only by the http[s] proxies
        http_proxy = self.get_proxy_env_var('http')
        https_proxy = self.get_proxy_env_var('https')

        if http_proxy is not None:
            if self._debuglevel > 0:
                print 'Will bind http_request for %r' % http_proxy
            setattr(self, 'http_request',
                    lambda request: self.set_proxy(request, 'http'))

        if https_proxy is not None:
            if self._debuglevel > 0:
                print 'Will bind http_request for %r' % https_proxy
            setattr(self, 'https_request',
                    lambda request: self.set_proxy(request, 'https'))

    def get_proxy_env_var(self, name, default_to='all'):
        """Get a proxy env var.

        Note that we indirectly rely on
        urllib.getproxies_environment taking into account the
        uppercased values for proxy variables.
        """
        try:
            return self.proxies[name.lower()]
        except KeyError:
            if default_to is not None:
                # Try to get the alternate environment variable
                try:
                    return self.proxies[default_to]
                except KeyError:
                    pass
        return None

    def proxy_bypass(self, host):
        """Check if host should be proxied or not"""
        no_proxy = self.get_proxy_env_var('no', default_to=None)
        if no_proxy is None:
            return False
        hhost, hport = urllib.splitport(host)
        # Does host match any of the domains mentioned in
        # no_proxy ? The rules about what is authorized in no_proxy
        # are fuzzy (to say the least). We try to allow most
        # commonly seen values.
        for domain in no_proxy.split(','):
            dhost, dport = urllib.splitport(domain)
            if hport == dport or dport is None:
                # Protect glob chars
                dhost = dhost.replace(".", r"\.")
                dhost = dhost.replace("*", r".*")
                dhost = dhost.replace("?", r".")
                if re.match(dhost, hhost, re.IGNORECASE):
                    return True
        # Nevertheless, there are platform-specific ways to
        # ignore proxies...
        return urllib.proxy_bypass(host)

    def set_proxy(self, request, type):
        if self.proxy_bypass(request.get_host()):
            return request

        proxy = self.get_proxy_env_var(type)
        if self._debuglevel > 0:
            print 'set_proxy %s_request for %r' % (type, proxy)
        # FIXME: python 2.5 urlparse provides a better _parse_proxy which can
        # grok user:password@host:port as well as
        # http://user:password@host:port

        (scheme, user, password,
         host, port, path) = transport.ConnectedTransport._split_url(proxy)

        if request.proxy_auth == {}:
            # No proxy auth parameter are available, we are handling the first
            # proxied request, intialize.  scheme (the authentication scheme)
            # and realm will be set by the AuthHandler
            request.proxy_auth = {
                                  'host': host, 'port': port,
                                  'user': user, 'password': password,
                                  'protocol': scheme,
                                   # We ignore path since we connect to a proxy
                                  'path': None}
        if port is None:
            phost = host
        else:
            phost = host + ':%d' % port
        request.set_proxy(phost, type)
        if self._debuglevel > 0:
            print 'set_proxy: proxy set to %s://%s' % (type, phost)
        return request


class AbstractAuthHandler(urllib2.BaseHandler):
    """A custom abstract authentication handler for all http authentications.

    Provides the meat to handle authentication errors and
    preventively set authentication headers after the first
    successful authentication.

    This can be used for http and proxy, as well as for basic and
    digest authentications.

    This provides an unified interface for all authentication handlers
    (urllib2 provides far too many with different policies).

    The interaction between this handler and the urllib2
    framework is not obvious, it works as follow:

    opener.open(request) is called:

    - that may trigger http_request which will add an authentication header
      (self.build_header) if enough info is available.

    - the request is sent to the server,

    - if an authentication error is received self.auth_required is called,
      we acquire the authentication info in the error headers and call
      self.auth_match to check that we are able to try the
      authentication and complete the authentication parameters,

    - we call parent.open(request), that may trigger http_request
      and will add a header (self.build_header), but here we have
      all the required info (keep in mind that the request and
      authentication used in the recursive calls are really (and must be)
      the *same* objects).

    - if the call returns a response, the authentication have been
      successful and the request authentication parameters have been updated.
    """

    _max_retry = 3
    """We don't want to retry authenticating endlessly"""

    # The following attributes should be defined by daughter
    # classes:
    # - auth_required_header:  the header received from the server
    # - auth_header: the header sent in the request

    def __init__(self):
        # We want to know when we enter into an try/fail cycle of
        # authentications so we initialize to None to indicate that we aren't
        # in such a cycle by default.
        self._retry_count = None

    def update_auth(self, auth, key, value):
        """Update a value in auth marking the auth as modified if needed"""
        old_value = auth.get(key, None)
        if old_value != value:
            auth[key] = value
            auth['modified'] = True

    def auth_required(self, request, headers):
        """Retry the request if the auth scheme is ours.

        :param request: The request needing authentication.
        :param headers: The headers for the authentication error response.
        :return: None or the response for the authenticated request.
        """
        # Don't try  to authenticate endlessly
        if self._retry_count is None:
            # The retry being recusrsive calls, None identify the first retry
            self._retry_count = 1
        else:
            self._retry_count += 1
            if self._retry_count > self._max_retry:
                # Let's be ready for next round
                self._retry_count = None
                return None
        server_header = headers.get(self.auth_required_header, None)
        if server_header is None:
            # The http error MUST have the associated
            # header. This must never happen in production code.
            raise KeyError('%s not found' % self.auth_required_header)

        auth = self.get_auth(request)
        if auth.get('user', None) is None:
            # Without a known user, we can't authenticate
            return None

        auth['modified'] = False
        if self.auth_match(server_header, auth):
            # auth_match may have modified auth (by adding the
            # password or changing the realm, for example)
            if (request.get_header(self.auth_header, None) is not None
                and not auth['modified']):
                # We already tried that, give up
                return None

            response = self.parent.open(request)
            if response:
                self.auth_successful(request, response)
            return response
        # We are not qualified to handle the authentication.
        # Note: the authentication error handling will try all
        # available handlers. If one of them authenticates
        # successfully, a response will be returned. If none of
        # them succeeds, None will be returned and the error
        # handler will raise the 401 'Unauthorized' or the 407
        # 'Proxy Authentication Required' error.
        return None

    def add_auth_header(self, request, header):
        """Add the authentication header to the request"""
        request.add_unredirected_header(self.auth_header, header)

    def auth_match(self, header, auth):
        """Check that we are able to handle that authentication scheme.

        The request authentication parameters may need to be
        updated with info from the server. Some of these
        parameters, when combined, are considered to be the
        authentication key, if one of them change the
        authentication result may change. 'user' and 'password'
        are exampls, but some auth schemes may have others
        (digest's nonce is an example, digest's nonce_count is a
        *counter-example*). Such parameters must be updated by
        using the update_auth() method.
        
        :param header: The authentication header sent by the server.
        :param auth: The auth parameters already known. They may be
             updated.
        :returns: True if we can try to handle the authentication.
        """
        raise NotImplementedError(self.auth_match)

    def build_auth_header(self, auth, request):
        """Build the value of the header used to authenticate.

        :param auth: The auth parameters needed to build the header.
        :param request: The request needing authentication.

        :return: None or header.
        """
        raise NotImplementedError(self.build_auth_header)

    def auth_successful(self, request, response):
        """The authentification was successful for the request.

        Additional infos may be available in the response.

        :param request: The succesfully authenticated request.
        :param response: The server response (may contain auth info).
        """
        # It may happen that we need to reconnect later, let's be ready
        self._retry_count = None

    def get_user_password(self, auth):
        """Ask user for a password if none is already available."""
        auth_conf = config.AuthenticationConfig()
        user = auth['user']
        password = auth['password']
        realm = auth['realm']

        if user is None:
            user = auth.get_user(auth['protocol'], auth['host'],
                                 port=auth['port'], path=auth['path'],
                                 realm=realm)
            if user is None:
                # Default to local user
                user = getpass.getuser()

        if password is None:
            password = auth_conf.get_password(
                auth['protocol'], auth['host'], user, port=auth['port'],
                path=auth['path'], realm=realm,
                prompt=self.build_password_prompt(auth))

        return user, password

    def _build_password_prompt(self, auth):
        """Build a prompt taking the protocol used into account.

        The AuthHandler is used by http and https, we want that information in
        the prompt, so we build the prompt from the authentication dict which
        contains all the needed parts.

        Also, hhtp and proxy AuthHandlers present different prompts to the
        user. The daughter classes hosuld implements a public
        build_password_prompt using this method.
        """
        prompt = '%s' % auth['protocol'].upper() + ' %(user)s@%(host)s'
        realm = auth['realm']
        if realm is not None:
            prompt += ", Realm: '%s'" % realm
        prompt += ' password'
        return prompt

    def http_request(self, request):
        """Insert an authentication header if information is available"""
        auth = self.get_auth(request)
        if self.auth_params_reusable(auth):
            self.add_auth_header(request, self.build_auth_header(auth, request))
        return request

    https_request = http_request # FIXME: Need test


class BasicAuthHandler(AbstractAuthHandler):
    """A custom basic authentication handler."""

    handler_order = 500

    auth_regexp = re.compile('realm="([^"]*)"', re.I)

    def build_auth_header(self, auth, request):
        raw = '%s:%s' % (auth['user'], auth['password'])
        auth_header = 'Basic ' + raw.encode('base64').strip()
        return auth_header

    def auth_match(self, header, auth):
        scheme, raw_auth = header.split(None, 1)
        scheme = scheme.lower()
        if scheme != 'basic':
            return False

        match = self.auth_regexp.search(raw_auth)
        if match:
            realm = match.groups()
            if scheme != 'basic':
                return False

            # Put useful info into auth
            self.update_auth(auth, 'scheme', scheme)
            self.update_auth(auth, 'realm', realm)
            if auth['user'] is None or auth['password'] is None:
                user, password = self.get_user_password(auth)
                self.update_auth(auth, 'user', user)
                self.update_auth(auth, 'password', password)
        return match is not None

    def auth_params_reusable(self, auth):
        # If the auth scheme is known, it means a previous
        # authentication was successful, all information is
        # available, no further checks are needed.
        return auth.get('scheme', None) == 'basic'


def get_digest_algorithm_impls(algorithm):
    H = None
    KD = None
    if algorithm == 'MD5':
        H = lambda x: osutils.md5(x).hexdigest()
    elif algorithm == 'SHA':
        H = lambda x: osutils.sha(x).hexdigest()
    if H is not None:
        KD = lambda secret, data: H("%s:%s" % (secret, data))
    return H, KD


def get_new_cnonce(nonce, nonce_count):
    raw = '%s:%d:%s:%s' % (nonce, nonce_count, time.ctime(),
                           urllib2.randombytes(8))
    return osutils.sha(raw).hexdigest()[:16]


class DigestAuthHandler(AbstractAuthHandler):
    """A custom digest authentication handler."""

    # Before basic as digest is a bit more secure
    handler_order = 490

    def auth_params_reusable(self, auth):
        # If the auth scheme is known, it means a previous
        # authentication was successful, all information is
        # available, no further checks are needed.
        return auth.get('scheme', None) == 'digest'

    def auth_match(self, header, auth):
        scheme, raw_auth = header.split(None, 1)
        scheme = scheme.lower()
        if scheme != 'digest':
            return False

        # Put the requested authentication info into a dict
        req_auth = urllib2.parse_keqv_list(urllib2.parse_http_list(raw_auth))

        # Check that we can handle that authentication
        qop = req_auth.get('qop', None)
        if qop != 'auth': # No auth-int so far
            return False

        H, KD = get_digest_algorithm_impls(req_auth.get('algorithm', 'MD5'))
        if H is None:
            return False

        realm = req_auth.get('realm', None)
        # Put useful info into auth
        self.update_auth(auth, 'scheme', scheme)
        self.update_auth(auth, 'realm', realm)
        if auth['user'] is None or auth['password'] is None:
            user, password = self.get_user_password(auth)
            self.update_auth(auth, 'user', user)
            self.update_auth(auth, 'password', password)

        try:
            if req_auth.get('algorithm', None) is not None:
                self.update_auth(auth, 'algorithm', req_auth.get('algorithm'))
            nonce = req_auth['nonce']
            if auth.get('nonce', None) != nonce:
                # A new nonce, never used
                self.update_auth(auth, 'nonce_count', 0)
            self.update_auth(auth, 'nonce', nonce)
            self.update_auth(auth, 'qop', qop)
            auth['opaque'] = req_auth.get('opaque', None)
        except KeyError:
            # Some required field is not there
            return False

        return True

    def build_auth_header(self, auth, request):
        url_scheme, url_selector = urllib.splittype(request.get_selector())
        sel_host, uri = urllib.splithost(url_selector)

        A1 = '%s:%s:%s' % (auth['user'], auth['realm'], auth['password'])
        A2 = '%s:%s' % (request.get_method(), uri)

        nonce = auth['nonce']
        qop = auth['qop']

        nonce_count = auth['nonce_count'] + 1
        ncvalue = '%08x' % nonce_count
        cnonce = get_new_cnonce(nonce, nonce_count)

        H, KD = get_digest_algorithm_impls(auth.get('algorithm', 'MD5'))
        nonce_data = '%s:%s:%s:%s:%s' % (nonce, ncvalue, cnonce, qop, H(A2))
        request_digest = KD(H(A1), nonce_data)

        header = 'Digest '
        header += 'username="%s", realm="%s", nonce="%s"' % (auth['user'],
                                                             auth['realm'],
                                                             nonce)
        header += ', uri="%s"' % uri
        header += ', cnonce="%s", nc=%s' % (cnonce, ncvalue)
        header += ', qop="%s"' % qop
        header += ', response="%s"' % request_digest
        # Append the optional fields
        opaque = auth.get('opaque', None)
        if opaque:
            header += ', opaque="%s"' % opaque
        if auth.get('algorithm', None):
            header += ', algorithm="%s"' % auth.get('algorithm')

        # We have used the nonce once more, update the count
        auth['nonce_count'] = nonce_count

        return header


class HTTPAuthHandler(AbstractAuthHandler):
    """Custom http authentication handler.

    Send the authentication preventively to avoid the roundtrip
    associated with the 401 error and keep the revelant info in
    the auth request attribute.
    """

    auth_required_header = 'www-authenticate'
    auth_header = 'Authorization'

    def get_auth(self, request):
        """Get the auth params from the request"""
        return request.auth

    def set_auth(self, request, auth):
        """Set the auth params for the request"""
        request.auth = auth

    def build_password_prompt(self, auth):
        return self._build_password_prompt(auth)

    def http_error_401(self, req, fp, code, msg, headers):
        return self.auth_required(req, headers)


class ProxyAuthHandler(AbstractAuthHandler):
    """Custom proxy authentication handler.

    Send the authentication preventively to avoid the roundtrip
    associated with the 407 error and keep the revelant info in
    the proxy_auth request attribute..
    """

    auth_required_header = 'proxy-authenticate'
    # FIXME: the correct capitalization is Proxy-Authorization,
    # but python-2.4 urllib2.Request insist on using capitalize()
    # instead of title().
    auth_header = 'Proxy-authorization'

    def get_auth(self, request):
        """Get the auth params from the request"""
        return request.proxy_auth

    def set_auth(self, request, auth):
        """Set the auth params for the request"""
        request.proxy_auth = auth

    def build_password_prompt(self, auth):
        prompt = self._build_password_prompt(auth)
        prompt = 'Proxy ' + prompt
        return prompt

    def http_error_407(self, req, fp, code, msg, headers):
        return self.auth_required(req, headers)


class HTTPBasicAuthHandler(BasicAuthHandler, HTTPAuthHandler):
    """Custom http basic authentication handler"""


class ProxyBasicAuthHandler(BasicAuthHandler, ProxyAuthHandler):
    """Custom proxy basic authentication handler"""


class HTTPDigestAuthHandler(DigestAuthHandler, HTTPAuthHandler):
    """Custom http basic authentication handler"""


class ProxyDigestAuthHandler(DigestAuthHandler, ProxyAuthHandler):
    """Custom proxy basic authentication handler"""


class HTTPErrorProcessor(urllib2.HTTPErrorProcessor):
    """Process HTTP error responses.

    We don't really process the errors, quite the contrary
    instead, we leave our Transport handle them.
    """

    accepted_errors = [200, # Ok
                       206, # Partial content
                       404, # Not found
                       ]
    """The error codes the caller will handle.

    This can be specialized in the request on a case-by case basis, but the
    common cases are covered here.
    """

    def http_response(self, request, response):
        code, msg, hdrs = response.code, response.msg, response.info()

        accepted_errors = request.accepted_errors
        if accepted_errors is None:
            accepted_errors = self.accepted_errors

        if code not in accepted_errors:
            response = self.parent.error('http', request, response,
                                         code, msg, hdrs)
        return response

    https_response = http_response


class HTTPDefaultErrorHandler(urllib2.HTTPDefaultErrorHandler):
    """Translate common errors into bzr Exceptions"""

    def http_error_default(self, req, fp, code, msg, hdrs):
        if code == 403:
            raise errors.TransportError('Server refuses to fullfil the request')
        else:
            raise errors.InvalidHttpResponse(req.get_full_url(),
                                             'Unable to handle http code %d: %s'
                                             % (code, msg))


class Opener(object):
    """A wrapper around urllib2.build_opener

    Daughter classes can override to build their own specific opener
    """
    # TODO: Provides hooks for daughter classes.

    def __init__(self,
                 connection=ConnectionHandler,
                 redirect=HTTPRedirectHandler,
                 error=HTTPErrorProcessor,):
        self._opener = urllib2.build_opener( \
            connection, redirect, error,
            ProxyHandler(),
            HTTPBasicAuthHandler(),
            HTTPDigestAuthHandler(),
            ProxyBasicAuthHandler(),
            ProxyDigestAuthHandler(),
            HTTPHandler,
            HTTPSHandler,
            HTTPDefaultErrorHandler,
            )

        self.open = self._opener.open
        if DEBUG >= 3:
            # When dealing with handler order, it's easy to mess
            # things up, the following will help understand which
            # handler is used, when and for what.
            import pprint
            pprint.pprint(self._opener.__dict__)
