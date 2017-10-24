#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
#  Tests for hammer-vlsi
#  
#  Copyright 2017 Edward Wang <edward.c.wang@compdigitec.com>

import hammer_vlsi

import tempfile
import unittest

class HammerVLSILoggingTest(unittest.TestCase):
    def test_colours(self):
        """
        Test that we can log with and without colour.
        """
        msg = "This is a test message" # type: str

        log = hammer_vlsi.HammerVLSILogging.context("test")

        hammer_vlsi.HammerVLSILogging.enable_buffering = True # we need this for test
        hammer_vlsi.HammerVLSILogging.clear_callbacks()
        hammer_vlsi.HammerVLSILogging.add_callback(hammer_vlsi.HammerVLSILogging.callback_buffering)

        hammer_vlsi.HammerVLSILogging.enable_colour = True
        log.info(msg)
        self.assertEqual(hammer_vlsi.HammerVLSILogging.get_colour_escape(hammer_vlsi.Level.INFO) + "[test] " + msg + hammer_vlsi.HammerVLSILogging.COLOUR_CLEAR, hammer_vlsi.HammerVLSILogging.get_buffer()[0])

        hammer_vlsi.HammerVLSILogging.enable_colour = False
        log.info(msg)
        self.assertEqual("[test] " + msg, hammer_vlsi.HammerVLSILogging.get_buffer()[0])

    def test_file_logging(self):
        import os
        fd, path = tempfile.mkstemp(".log")
        os.close(fd) # Don't leak file descriptors

        filelogger = hammer_vlsi.HammerVLSIFileLogger(path)

        hammer_vlsi.HammerVLSILogging.add_callback(filelogger.callback)
        log = hammer_vlsi.HammerVLSILogging.context()
        log.info("Hello world")
        log.info("Eternal voyage to the edge of the universe")
        filelogger.close()

        with open(path, 'r') as f:
            self.assertEqual(f.read().strip(), """
[<global>] Level.INFO: Hello world
[<global>] Level.INFO: Eternal voyage to the edge of the universe
""".strip())

if __name__ == '__main__':
    unittest.main()