# Copyright (C) 2011 Canonical Ltd
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

"""Tools for dealing with the Launchpad API without using launchpadlib.
"""

import socket

from bzrlib import tests
from bzrlib.plugins.launchpad import lp_api_lite

_example_response = r"""
{
    "total_size": 2,
    "start": 0,
    "next_collection_link": "https://api.launchpad.net/1.0/ubuntu/+archive/primary?distro_series=%2Fubuntu%2Flucid&exact_match=true&source_name=%22bzr%22&status=Published&ws.op=getPublishedSources&ws.start=1&ws.size=1",
    "entries": [
        {
            "package_creator_link": "https://api.launchpad.net/1.0/~maxb",
            "package_signer_link": "https://api.launchpad.net/1.0/~jelmer",
            "source_package_name": "bzr",
            "removal_comment": null,
            "display_name": "bzr 2.1.4-0ubuntu1 in lucid",
            "date_made_pending": null,
            "source_package_version": "2.1.4-0ubuntu1",
            "date_superseded": null,
            "http_etag": "\"9ba966152dec474dc0fe1629d0bbce2452efaf3b-5f4c3fbb3eaf26d502db4089777a9b6a0537ffab\"",
            "self_link": "https://api.launchpad.net/1.0/ubuntu/+archive/primary/+sourcepub/1750327",
            "distro_series_link": "https://api.launchpad.net/1.0/ubuntu/lucid",
            "component_name": "main",
            "status": "Published",
            "date_removed": null,
            "pocket": "Updates",
            "date_published": "2011-05-30T06:09:58.653984+00:00",
            "removed_by_link": null,
            "section_name": "devel",
            "resource_type_link": "https://api.launchpad.net/1.0/#source_package_publishing_history",
            "archive_link": "https://api.launchpad.net/1.0/ubuntu/+archive/primary",
            "package_maintainer_link": "https://api.launchpad.net/1.0/~ubuntu-devel-discuss-lists",
            "date_created": "2011-05-30T05:19:12.233621+00:00",
            "scheduled_deletion_date": null
        }
    ]
}"""

class TestLatestPublication(tests.TestCase):

    def make_latest_publication(self, archive='ubuntu', series='natty',
                                project='bzr'):
        return lp_api_lite.LatestPublication(archive, series, project)

    def test_init(self):
        latest_pub = self.make_latest_publication()
        self.assertEqual('ubuntu', latest_pub._archive)
        self.assertEqual('natty', latest_pub._series)
        self.assertEqual('bzr', latest_pub._project)
        self.assertEqual(None, latest_pub._pocket)

    def test__archive_URL(self):
        latest_pub = self.make_latest_publication()
        self.assertEqual(
            'https://api.launchpad.net/1.0/ubuntu/+archive/primary',
            latest_pub._archive_URL())

    def test__publication_status_for_ubuntu(self):
        latest_pub = self.make_latest_publication()
        self.assertEqual('Published', latest_pub._publication_status())

    def test__publication_status_for_debian(self):
        latest_pub = self.make_latest_publication(archive='debian')
        self.assertEqual('Pending', latest_pub._publication_status())

    def test_pocket(self):
        latest_pub = self.make_latest_publication(series='natty-proposed')
        self.assertEqual('natty', latest_pub._series)
        self.assertEqual('Proposed', latest_pub._pocket)

    def test_series_None(self):
        latest_pub = self.make_latest_publication(series=None)
        self.assertEqual('ubuntu', latest_pub._archive)
        self.assertEqual(None, latest_pub._series)
        self.assertEqual('bzr', latest_pub._project)
        self.assertEqual(None, latest_pub._pocket)

    def test__query_params(self):
        latest_pub = self.make_latest_publication()
        self.assertEqual({'ws.op': 'getPublishedSources',
                          'exact_match': 'true',
                          'source_name': '"bzr"',
                          'status': 'Published',
                          'ws.size': '1',
                          'distro_series': '/ubuntu/natty',
                         }, latest_pub._query_params())

    def test__query_params_no_series(self):
        latest_pub = self.make_latest_publication(series=None)
        self.assertEqual({'ws.op': 'getPublishedSources',
                          'exact_match': 'true',
                          'source_name': '"bzr"',
                          'status': 'Published',
                          'ws.size': '1',
                         }, latest_pub._query_params())

    def test__query_params_pocket(self):
        latest_pub = self.make_latest_publication(series='natty-proposed')
        self.assertEqual({'ws.op': 'getPublishedSources',
                          'exact_match': 'true',
                          'source_name': '"bzr"',
                          'status': 'Published',
                          'ws.size': '1',
                          'distro_series': '/ubuntu/natty',
                          'pocket': 'Proposed',
                         }, latest_pub._query_params())

    def test__query_URL(self):
        latest_pub = self.make_latest_publication()
        # we explicitly sort params, so we can be sure this URL matches exactly
        self.assertEqual(
            'https://api.launchpad.net/1.0/ubuntu/+archive/primary'
            '?distro_series=%2Fubuntu%2Fnatty&exact_match=true'
            '&source_name=%22bzr%22&status=Published'
            '&ws.op=getPublishedSources&ws.size=1',
            latest_pub._query_URL())

    def DONT_test__gracefully_handle_failed_rpc_connection(self):
        # TODO: This test kind of sucks. We intentionally create an arbitrary
        #       port and don't listen to it, because we want the request to fail.
        #       However, it seems to take 1s for it to timeout. Is there a way
        #       to make it fail faster?
        latest_pub = self.make_latest_publication()
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        addr, port = s.getsockname()
        latest_pub.LP_API_ROOT = 'http://%s:%s/' % (addr, port)
        s.close()
        self.assertIs(None, latest_pub._get_lp_info())

    def test__query_launchpad(self):
        # TODO: This is a test that we are making a valid request against
        #       launchpad. This seems important, but it is slow, requires net
        #       access, and requires launchpad to be up and running. So for
        #       now, it is commented out for production tests.
        latest_pub = self.make_latest_publication()
        json_txt = latest_pub._get_lp_info()
        self.assertIsNot(None, json_txt)
        if lp_api_lite.json is None:
            # We don't have a way to parse the text
            return
        # The content should be a valid json result
        content = lp_api_lite.json.loads(json_txt)
        entries = content['entries'] # It should have an 'entries' field.
        # ws.size should mean we get 0 or 1, and there should be something
        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual('bzr', entry['source_package_name'])
        version = entry['source_package_version']
        self.assertIsNot(None, version)
