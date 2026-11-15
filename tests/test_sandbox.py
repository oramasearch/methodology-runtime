# SPDX-License-Identifier: MIT

import socket
import threading
import unittest
from unittest import mock

from orama_methodology import sandbox
from orama_methodology.sandbox import check_credentials, check_network, credential_env_names


class CredentialTests(unittest.TestCase):
    def test_names_that_look_like_credentials_are_found_and_values_never_reported(self):
        environment = {
            "LAKE_URL": "http://lake",
            "ZETTA_BASE_URL": "http://zetta",
            "GITHUB_TOKEN": "t",
            "db_password": "p",
            "AWS_ACCESS_KEY_ID": "a",
            "AZURE_CLIENT_SECRET": "s",
            "MY_CREDENTIALS": "c",
            "KUBERNETES_SERVICE_HOST": "10.0.0.1",
            "KUBERNETES_PORT_443_TCP": "tcp://10.0.0.1:443",
            "PATH": "/usr/bin",
            "PYTHON_SHA256": "abc",
            "HOME": "/tmp",
        }
        names = credential_env_names(environment)
        self.assertEqual(
            names,
            sorted(["LAKE_URL", "ZETTA_BASE_URL", "GITHUB_TOKEN", "db_password", "AWS_ACCESS_KEY_ID",
                    "AZURE_CLIENT_SECRET", "MY_CREDENTIALS"]),
        )
        finding = check_credentials(environment, exists=lambda _: False)
        self.assertFalse(finding.ok)
        self.assertNotIn("10.0.0.1", finding.message)
        self.assertNotIn("abc", finding.message)

    def test_a_clean_container_passes(self):
        finding = check_credentials({"PATH": "/usr/bin", "KUBERNETES_SERVICE_HOST": "10.0.0.1"}, exists=lambda _: False)
        self.assertTrue(finding.ok)

    def test_the_orama_credential_file_fails(self):
        finding = check_credentials({}, exists=lambda path: path == "/var/run/orama/credential")
        self.assertFalse(finding.ok)


class NetworkTests(unittest.TestCase):
    def test_a_probe_that_reaches_something_fails(self):
        self.assertFalse(check_network(False, lambda: ["DNS resolved pypi.org"]).ok)

    def test_a_probe_that_reaches_nothing_passes(self):
        self.assertTrue(check_network(False, lambda: []).ok)

    def test_the_default_probe_reports_refused_connections_as_unreachable(self):
        def refuse(*_args, **_kwargs):
            raise OSError("Network is unreachable")

        with mock.patch.object(socket, "getaddrinfo", side_effect=socket.gaierror("no DNS")), \
                mock.patch.object(socket, "create_connection", side_effect=refuse):
            self.assertEqual(sandbox.default_probe(), [])

    def test_the_default_probe_reports_what_answered(self):
        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with mock.patch.object(socket, "getaddrinfo", return_value=[()]), \
                mock.patch.object(socket, "create_connection", return_value=Connection()):
            reached = sandbox.default_probe()
        self.assertIn("DNS resolved pypi.org", reached)
        self.assertIn("TCP 1.1.1.1:443", reached)
        self.assertIn("TCP 169.254.169.254:80", reached)

    def test_a_probe_that_hangs_counts_as_unreachable(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def hang(*_args, **_kwargs):
            release.wait(30)
            raise OSError("late")

        with mock.patch.object(sandbox, "PROBE_TIMEOUT_SECONDS", 0.05), \
                mock.patch.object(socket, "getaddrinfo", side_effect=hang), \
                mock.patch.object(socket, "create_connection", side_effect=hang):
            self.assertEqual(sandbox.default_probe(), [])


if __name__ == "__main__":
    unittest.main()
