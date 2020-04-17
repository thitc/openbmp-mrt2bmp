""" Logger class/methods

  Copyright (c) 2013-2015 Cisco Systems, Inc. and others.  All rights reserved.
  This program and the accompanying materials are made available under the
  terms of the Eclipse Public License v1.0 which accompanies this distribution,
  and is available at http://www.eclipse.org/legal/epl-v10.html

  .. moduleauthor:: Tim Evens <tievens@cisco.com>
"""
import logging
import logging.handlers
import logging.config
import threading

from queue import Empty

def init_main_logger(config):
    """ Initialize a new main logger instance

    :param config:    Configuration dictionary for logging

    :return: logger instance
    """
    root = logging.getLogger()

    for h in root.handlers:
        root.removeHandler(h)

    logging.config.dictConfig(config)

    return root


def init_mp_logger(name, queue):
    """ Initialize a new multiprocess logger instance

    :param name:            logger name
    :param queue:           multiprocessing.Queue

    :return: logger instance
    """
    handler = QueueHandler(queue)
    root = logging.getLogger()

    for h in root.handlers:
        root.removeHandler(h)

    root.addHandler(handler)
    root.setLevel(logging.INFO)

    log = logging.getLogger(name)

    return log


class QueueHandler(logging.Handler):
    """
    This is a logging handler which sends events to a multiprocessing queue.
    """

    def __init__(self, queue):
        logging.Handler.__init__(self)
        self.queue = queue

    def emit(self, record):
        self.queue.put_nowait(record)


class LoggerThread(threading.Thread):
    """ Threading class to monitor the multiprocessing logging queue which then
        synchronously writes to the log file
    """
    def __init__(self, queue, log_cfg):
        """ Constructor

            :param queue:               multiprocess.Queue
            :param log_cfg:             Logging configuration dictionary
        """
        threading.Thread.__init__(self)
        self._log_cfg = log_cfg
        self._queue = queue
        self._log = init_main_logger(self._log_cfg)
        self._stopme = threading.Event()
        #print (log_cfg['handlers']['file']['filename'])

    def run(self):
        """ Override """

        while not self.stopped():
            try:
                record = self._queue.get(True, 0.2)

                if record is None:
                    continue

                logger = logging.getLogger(record.name)
                logger.handle(record)

            except Empty:
                continue

            except KeyboardInterrupt:
                break

    def stop(self):
        self._stopme.set()

    def stopped(self):
        return self._stopme.isSet()


