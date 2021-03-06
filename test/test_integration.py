# -*- coding: utf-8 -*-
"""
test/integration
~~~~~~~~~~~~~~~~

This file defines integration-type tests for hyper. These are still not fully
hitting the network, so that's alright.
"""
import requests
import ssl
import threading
import hyper
from hyper import HTTP20Connection
from hyper.contrib import HTTP20Adapter
from hyper.http20.frame import (
    Frame, SettingsFrame, WindowUpdateFrame, DataFrame, HeadersFrame
)
from hyper.http20.hpack import Encoder
from hyper.http20.huffman import HuffmanEncoder
from hyper.http20.huffman_constants import (
    RESPONSE_CODES, RESPONSE_CODES_LENGTH
)
from server import SocketLevelTest

# Turn off certificate verification for the tests.
hyper.http20.tls._context = hyper.http20.tls._init_context()
hyper.http20.tls._context.verify_mode = ssl.CERT_NONE

def decode_frame(frame_data):
    f, length = Frame.parse_frame_header(frame_data[:8])
    f.parse_body(frame_data[8:8 + length])
    assert 8 + length == len(frame_data)
    return f


def build_headers_frame(headers):
    f = HeadersFrame(1)
    e = Encoder()
    e.huffman_coder = HuffmanEncoder(RESPONSE_CODES, RESPONSE_CODES_LENGTH)
    f.data = e.encode(headers)
    f.flags.add('END_HEADERS')
    return f


def receive_preamble(sock):
    # Receive the HTTP/2.0 'preamble'.
    sock.recv(65535)
    sock.recv(65535)
    sock.send(SettingsFrame(0).serialize())
    sock.recv(65535)
    return


class TestHyperIntegration(SocketLevelTest):
    def test_connection_string(self):
        self.set_up()

        # Confirm that we send the connection upgrade string and the initial
        # SettingsFrame.
        data = []
        send_event = threading.Event()

        def socket_handler(listener):
            sock = listener.accept()[0]

            # We should get two packets: one connection header string, one
            # SettingsFrame.
            first = sock.recv(65535)
            second = sock.recv(65535)
            data.append(first)
            data.append(second)

            # We need to send back a SettingsFrame.
            f = SettingsFrame(0)
            sock.send(f.serialize())

            send_event.set()
            sock.close()

        self._start_server(socket_handler)
        conn = HTTP20Connection(self.host, self.port)
        conn.connect()
        send_event.wait()

        assert data[0] == b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n'

        self.tear_down()

    def test_initial_settings(self):
        self.set_up()

        # Confirm that we send the connection upgrade string and the initial
        # SettingsFrame.
        data = []
        send_event = threading.Event()

        def socket_handler(listener):
            sock = listener.accept()[0]

            # We should get two packets: one connection header string, one
            # SettingsFrame.
            first = sock.recv(65535)
            second = sock.recv(65535)
            data.append(first)
            data.append(second)

            # We need to send back a SettingsFrame.
            f = SettingsFrame(0)
            sock.send(f.serialize())

            send_event.set()
            sock.close()

        self._start_server(socket_handler)
        conn = HTTP20Connection(self.host, self.port)
        conn.connect()
        send_event.wait()

        # Get the second chunk of data and decode it into a frame.
        data = data[1]
        f = decode_frame(data)

        assert isinstance(f, SettingsFrame)
        assert f.stream_id == 0
        assert f.settings == {SettingsFrame.ENABLE_PUSH: 0}

        self.tear_down()

    def test_stream_level_window_management(self):
        self.set_up()
        data = []
        send_event = threading.Event()

        def socket_handler(listener):
            sock = listener.accept()[0]

            # Dispose of the first two packets.
            sock.recv(65535)
            sock.recv(65535)

            # Send a Settings frame that reduces the flow-control window to
            # 64 bytes.
            f = SettingsFrame(0)
            f.settings[SettingsFrame.INITIAL_WINDOW_SIZE] = 64
            sock.send(f.serialize())

            # Grab three frames, the settings ACK, the initial headers frame,
            # and the first data frame.
            for x in range(0, 3):
                data.append(sock.recv(65535))

            # Send a WindowUpdate giving more window room to the stream.
            f = WindowUpdateFrame(1)
            f.window_increment = 64
            sock.send(f.serialize())

            # Reeive the remaining frame.
            data.append(sock.recv(65535))
            send_event.set()

            # We're done.
            sock.close()

        self._start_server(socket_handler)
        conn = HTTP20Connection(self.host, self.port)

        conn.putrequest('GET', '/')
        conn.endheaders()

        # Send the first data chunk. This is 32 bytes.
        sd = b'a' * 32
        conn.send(sd)

        # Send the second one. This should block until the WindowUpdate comes
        # in.
        sd = sd * 2
        conn.send(sd, final=True)
        assert send_event.wait(0.3)

        # Decode the frames.
        frames = [decode_frame(d) for d in data]

        # We care about the last two. The first should be a data frame
        # containing 32 bytes.
        assert (isinstance(frames[-2], DataFrame) and
                not isinstance(frames[-2], HeadersFrame))
        assert len(frames[-2].data) == 32

        # The second should be a data frame containing 64 bytes.
        assert isinstance(frames[-1], DataFrame)
        assert len(frames[-1].data) == 64

        self.tear_down()

    def test_connection_context_manager(self):
        self.set_up()

        data = []
        send_event = threading.Event()

        def socket_handler(listener):
            sock = listener.accept()[0]

            # We should get two packets: one connection header string, one
            # SettingsFrame.
            first = sock.recv(65535)
            second = sock.recv(65535)
            data.append(first)
            data.append(second)

            # We need to send back a SettingsFrame.
            f = SettingsFrame(0)
            sock.send(f.serialize())

            send_event.set()
            sock.close()

        self._start_server(socket_handler)
        with HTTP20Connection(self.host, self.port) as conn:
            conn.connect()
            send_event.wait()

        # Check that we closed the connection.
        assert conn._sock == None

        self.tear_down()

    def test_closed_responses_remove_their_streams_from_conn(self):
        self.set_up()

        recv_event = threading.Event()

        def socket_handler(listener):
            sock = listener.accept()[0]

            # We're going to get the two messages for the connection open, then
            # a headers frame.
            receive_preamble(sock)

            # Now, send the headers for the response.
            f = build_headers_frame([(':status', '200')])
            f.stream_id = 1
            sock.send(f.serialize())

            # Wait for the message from the main thread.
            recv_event.wait()
            sock.close()

        self._start_server(socket_handler)
        conn = HTTP20Connection(self.host, self.port)
        conn.request('GET', '/')
        resp = conn.getresponse()

        # Close the response.
        resp.close()

        recv_event.set()

        assert not conn.streams

        self.tear_down()

    def test_receiving_responses_with_no_body(self):
        self.set_up()

        recv_event = threading.Event()

        def socket_handler(listener):
            sock = listener.accept()[0]

            # We get two messages for the connection open and then a HEADERS
            # frame.
            receive_preamble(sock)

            # Now, send the headers for the response. This response has no body.
            f = build_headers_frame([(':status', '204'), ('Content-Length', '0')])
            f.flags.add('END_STREAM')
            f.stream_id = 1
            sock.send(f.serialize())

            # Wait for the message from the main thread.
            recv_event.wait()
            sock.close()

        self._start_server(socket_handler)
        conn = HTTP20Connection(self.host, self.port)
        conn.request('GET', '/')
        resp = conn.getresponse()

        # Confirm the status code.
        assert resp.status == 204

        # Confirm that we can read this, but it has no body.
        assert resp.read() == b''

        # Awesome, we're done now.
        recv_event.set()

        self.tear_down()


class TestRequestsAdapter(SocketLevelTest):
    def test_adapter_received_values(self):
        self.set_up()

        data = []
        send_event = threading.Event()

        def socket_handler(listener):
            sock = listener.accept()[0]

            # Do the handshake: conn header, settings, send settings, recv ack.
            receive_preamble(sock)

            # Now expect some data. One headers frame.
            data.append(sock.recv(65535))

            # Respond!
            h = HeadersFrame(1)
            h.data = self.get_encoder().encode({':status': 200, 'Content-Type': 'not/real', 'Content-Length': 20})
            h.flags.add('END_HEADERS')
            sock.send(h.serialize())
            d = DataFrame(1)
            d.data = b'1234567890' * 2
            d.flags.add('END_STREAM')
            sock.send(d.serialize())

            send_event.set()
            sock.close()

        self._start_server(socket_handler)

        s = requests.Session()
        s.mount('https://%s' % self.host, HTTP20Adapter())
        r = s.get('https://%s:%s/some/path' % (self.host, self.port))

        # Assert about the received values.
        assert r.status_code == 200
        assert r.headers['Content-Type'] == 'not/real'
        assert r.content == b'1234567890' * 2

        self.tear_down()

    def test_adapter_sending_values(self):
        self.set_up()

        data = []
        send_event = threading.Event()

        def socket_handler(listener):
            sock = listener.accept()[0]

            # Do the handshake: conn header, settings, send settings, recv ack.
            receive_preamble(sock)

            # Now expect some data. One headers frame and one data frame.
            data.append(sock.recv(65535))
            data.append(sock.recv(65535))

            # Respond!
            h = HeadersFrame(1)
            h.data = self.get_encoder().encode({':status': 200, 'Content-Type': 'not/real', 'Content-Length': 20})
            h.flags.add('END_HEADERS')
            sock.send(h.serialize())
            d = DataFrame(1)
            d.data = b'1234567890' * 2
            d.flags.add('END_STREAM')
            sock.send(d.serialize())

            send_event.set()
            sock.close()

        self._start_server(socket_handler)

        s = requests.Session()
        s.mount('https://%s' % self.host, HTTP20Adapter())
        r = s.post(
            'https://%s:%s/some/path' % (self.host, self.port),
            data='hi there',
        )

        # Assert about the sent values.
        assert r.status_code == 200

        f = decode_frame(data[0])
        assert isinstance(f, HeadersFrame)

        f = decode_frame(data[1])
        assert isinstance(f, DataFrame)
        assert f.data == b'hi there'

        self.tear_down()
