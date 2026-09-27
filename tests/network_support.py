"""Keep test connections blocked while supporting asyncio's private socket pair."""
from contextlib import contextmanager
import os
import socket
from unittest.mock import patch

# Capture before any test installs socket mocks. This method is used ONLY to
# connect a newly created socket to the listener created in the same function.
_CONNECT = socket.socket.connect
_IS_WINDOWS = os.name == "nt"


def _local_socketpair(family=None, type=socket.SOCK_STREAM, proto=0):
    """Make an internal loopback pair, not an exception for arbitrary connections."""
    if family is None:
        family = socket.AF_INET
    if family not in (socket.AF_INET, socket.AF_INET6):
        raise ValueError("The test socket pair requires an IP loopback family")
    if type != socket.SOCK_STREAM or proto != 0:
        raise ValueError("The test socket pair requires a default TCP stream")
    host = "127.0.0.1" if family == socket.AF_INET else "::1"
    listener = socket.socket(family, type, proto)
    client = accepted = None
    try:
        listener.settimeout(5)
        listener.bind((host, 0))
        listener.listen(1)
        client = socket.socket(family, type, proto)
        client.settimeout(5)
        # Not client.connect(): that remains blocked by the surrounding test.
        _CONNECT(client, listener.getsockname())
        accepted, peer = listener.accept()
        if peer[:2] != client.getsockname()[:2]:
            raise RuntimeError("An unexpected peer reached the private test socket pair")
        accepted.setblocking(True)
        client.setblocking(True)
        accepted.set_inheritable(False)
        client.set_inheritable(False)
        result = (accepted, client)
        accepted = client = None
        return result
    finally:
        listener.close()
        if accepted is not None:
            accepted.close()
        if client is not None:
            client.close()


@contextmanager
def portable_socketpairs():
    """Allow the Windows event loop's private pair; do not unblock connect()."""
    if _IS_WINDOWS:
        with patch.object(socket, "socketpair", new=_local_socketpair):
            yield
    else:
        yield
