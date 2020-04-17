# -*- coding: utf-8 -*-
"""OpenBMP mrt2bmp

  Copyright (c) 2013-2015 Cisco Systems, Inc. and others.  All rights reserved.
  This program and the accompanying materials are made available under the
  terms of the Eclipse Public License v1.0 which accompanies this distribution,
  and is available at http://www.eclipse.org/legal/epl-v10.html

  .. moduleauthor:: Tim Evens <tievens@cisco.com>
"""
import socket
import multiprocessing
import time
import queue

from time import sleep
from mrt2bmp.logger import init_mp_logger

class BMPWriter(multiprocessing.Process):
    """ BMP Writer

        Pops messages from forwarder queue and transmits them to remote bmp collector.
    """

    def __init__(self, cfg, forward_queue, log_queue):
        """ Constructor

            :param cfg:             Configuration dictionary
            :param forward_queue:   Output for BMP raw message forwarding
            :param log_queue:       Logging queue - sync logging
        """
        multiprocessing.Process.__init__(self)
        self._stop = multiprocessing.Event()

        self.init_message = None
        self.term_message = None
        self.peer_up_messages = None

        self._cfg = cfg
        self._fwd_queue = forward_queue
        self._log_queue = log_queue
        self.LOG = None
        self._isConnected = False
        self._delay_after_peer_ups = self._cfg['collector']['delay_after_init_and_peer_ups']

        self._sock = None

    def run(self):
        """ Override """
        self.LOG = init_mp_logger("bmp_writer", self._log_queue)

        self.LOG.info("Running bmp_writer")

        self.connect()

        try:
            # Read queue
            while not self.stopped():

                # Do not pop any message unless connected
                if self._isConnected:

                    qm = self._fwd_queue.get()

                    sent = False
                    while not sent:
                        sent = self.send(qm)

                else:
                    self.LOG.info("Not connected, attempting to reconnect")
                    sleep(1)
                    self.connect()

        except (KeyboardInterrupt, IOError, EOFError):
            pass

        self.LOG.info("rewrite stopped")

    def connect(self):
        """ Connect to remote collector

        :return: True if connected, False otherwise/error
        """
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.connect((self._cfg['collector']['host'], self._cfg['collector']['port']))
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self._isConnected = True
            self.LOG.info("Connected to remote collector: %s:%d", self._cfg['collector']['host'],
                          self._cfg['collector']['port'])

            # Send INIT message.
            sent = False
            while not sent:
                sent = self.send(self.init_message)

            # Send PEER UP messages.
            for m in self.peer_up_messages:
                sent = False
                while not sent:
                    sent = self.send(m)

            # Waits for specified time in config after INIT and PEER UP messages.
            time.sleep(self._delay_after_peer_ups)

        except socket.error as msg:
            self.LOG.error("Failed to connect to remote collector: %r", msg)
            self._isConnected = False

        except KeyboardInterrupt:
            pass

    def send(self, msg):
        """ Send BMP message to socket.

            :param msg:     Message to send/write

            :return: True if sent, False if not sent
        """
        sent = False

        try:
            self._sock.sendall(msg)
            sent = True

        except socket.error as msg:
            self.LOG.error("Failed to send message to collector: %r", msg)
            self.disconnect()
            sleep(1)
            self.connect()

        finally:
            return sent

    def disconnect(self):
        """ Disconnect from remote collector
        """

        # Send TERM message to the collector.
        #self.send(self.TERM_MESSAGE)

        if self._sock:
            self._sock.close()
            self._sock = None
            self.LOG.info("Connection is disconnected to remote collector: %s:%d", self._cfg['collector']['host'],
                          self._cfg['collector']['port'])

        self._isConnected = False

    def setInitialMessages(self, init_message, peer_up_message_list, term_message):
        self.init_message = init_message
        self.peer_up_messages = peer_up_message_list
        self.term_message = term_message

    def isConnected(self):
        return self._isConnected

    def stop(self):

        self._stop.set()
        self._fwd_queue.put(self.term_message)


    def stopped(self):
        return self._stop.is_set()
