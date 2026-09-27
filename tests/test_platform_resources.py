"""Resource-lifetime and local I/O regression checks for the examples."""
from contextlib import closing
from pathlib import Path
import socket
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from claimsassist.approval import ApprovalLedger
from claimsassist.baseline import BoundaryError
from claimsassist.deployment import JobLedger, ReleaseRegistry
from claimsassist.file_access import open_regular_readonly
from tests.network_support import _local_socketpair, portable_socketpairs


class PlatformResourceTests(unittest.TestCase):
    def assert_closed_after_construction(self, factory):
        connections = []
        connect = sqlite3.connect

        def record(*args, **kwargs):
            connection = connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with TemporaryDirectory() as directory:
            path = Path(directory) / "resource.sqlite"
            with patch.object(sqlite3, "connect", side_effect=record):
                factory(path)
            self.assertTrue(connections)
            try:
                for connection in connections:
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute("SELECT 1")
            finally:
                for connection in connections:
                    connection.close()

    def test_approval_constructor_closes_connection(self):
        self.assert_closed_after_construction(ApprovalLedger)

    def test_job_constructor_closes_connection(self):
        self.assert_closed_after_construction(JobLedger)

    def test_registry_constructor_closes_connection(self):
        self.assert_closed_after_construction(ReleaseRegistry)

    def test_portable_socket_pair_exchanges_bytes_with_connect_blocked(self):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Blocked")), \
             patch.object(socket.socket, "connect_ex", side_effect=AssertionError("Blocked")):
            reader, writer = _local_socketpair()
            with closing(reader), closing(writer):
                writer.sendall(b"wake")
                self.assertEqual(reader.recv(4), b"wake")
                self.assertFalse(reader.get_inheritable())
                self.assertFalse(writer.get_inheritable())

    def test_portable_socket_pair_does_not_unblock_other_connections(self):
        with patch("tests.network_support._IS_WINDOWS", True), portable_socketpairs(), \
             patch.object(socket.socket, "connect", side_effect=AssertionError("Blocked")), \
             patch.object(socket.socket, "connect_ex", side_effect=AssertionError("Blocked")):
            reader, writer = socket.socketpair()
            with closing(reader), closing(writer), closing(socket.socket()) as probe:
                with self.assertRaises(AssertionError):
                    probe.connect(("127.0.0.1", 1))
                with self.assertRaises(AssertionError):
                    probe.connect_ex(("192.0.2.1", 443))

    def test_portable_socket_pair_rejects_datagram_request(self):
        with self.assertRaises(ValueError):
            _local_socketpair(type=socket.SOCK_DGRAM)

    def test_regular_file_descriptor_is_readable_and_closes(self):
        import os
        with TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_bytes(b'{"value":1}')
            fd = open_regular_readonly(path)
            with os.fdopen(fd, "rb") as handle:
                self.assertEqual(handle.read(), b'{"value":1}')
            with self.assertRaises(OSError):
                os.fstat(fd)
            path.unlink()

    def test_regular_file_reader_rejects_directory(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(BoundaryError):
                open_regular_readonly(Path(directory))

    def test_regular_file_reader_rejects_symbolic_link(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.json"
            target.write_bytes(b'{}')
            link = root / "link.json"
            link.symlink_to(target)
            with self.assertRaises(BoundaryError):
                open_regular_readonly(link)

    def test_regular_file_reader_rejects_linked_parent(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "real"
            target.mkdir()
            (target / "data.json").write_bytes(b'{}')
            link = root / "linked"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(BoundaryError):
                open_regular_readonly(link / "data.json")
