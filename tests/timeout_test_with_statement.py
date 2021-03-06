""" Tests with-statement behavior of Timeout class.  Don't import when
using Python 2.4. """

from __future__ import with_statement
import sys
import unittest
import weakref
import time
from eventlet import sleep
from eventlet.timeout import Timeout
from tests import LimitedTestCase
DELAY = 0.01

class Error(Exception):
    pass

class Test(LimitedTestCase):
    def test_api(self):
        # Nothing happens if with-block finishes before the timeout expires
        t = Timeout(DELAY*2)
        sleep(0)  # make it pending
        assert t.pending, repr(t)
        with t:
            assert t.pending, repr(t)
            sleep(DELAY)
        # check if timer was actually cancelled
        assert not t.pending, repr(t)
        sleep(DELAY*2)

        # An exception will be raised if it's not
        try:
            with Timeout(DELAY) as t:
                sleep(DELAY*2)
        except Timeout, ex:
            assert ex is t, (ex, t)
        else:
            raise AssertionError('must raise Timeout')

        # You can customize the exception raised:
        try:
            with Timeout(DELAY, IOError("Operation takes way too long")):
                sleep(DELAY*2)
        except IOError, ex:
            assert str(ex)=="Operation takes way too long", repr(ex)

        # Providing classes instead of values should be possible too:
        try:
            with Timeout(DELAY, ValueError):
                sleep(DELAY*2)
        except ValueError:
            pass

        try:
            1//0
        except:
            try:
                with Timeout(DELAY, sys.exc_info()[0]):
                    sleep(DELAY*2)
                    raise AssertionError('should not get there')
                raise AssertionError('should not get there')
            except ZeroDivisionError:
                pass
        else:
            raise AssertionError('should not get there')

        # It's possible to cancel the timer inside the block:
        with Timeout(DELAY) as timer:
            timer.cancel()
            sleep(DELAY*2)

        # To silent the exception before exiting the block, pass False as second parameter.
        XDELAY=0.1
        start = time.time()
        with Timeout(XDELAY, False):
            sleep(XDELAY*2)
        delta = (time.time()-start)
        assert delta<XDELAY*2, delta

        # passing None as seconds disables the timer
        with Timeout(None):
            sleep(DELAY)
        sleep(DELAY)

    def test_ref(self):
        err = Error()
        err_ref = weakref.ref(err)
        with Timeout(DELAY*2, err):
            sleep(DELAY)
        del err
        assert not err_ref(), repr(err_ref())

    def test_nested_timeout(self):
        with Timeout(DELAY, False):
            with Timeout(DELAY*2, False):
                sleep(DELAY*3)
            raise AssertionError('should not get there')

        with Timeout(DELAY) as t1:
            with Timeout(DELAY*2) as t2:
                try:
                    sleep(DELAY*3)
                except Timeout, ex:
                    assert ex is t1, (ex, t1)
                assert not t1.pending, t1
                assert t2.pending, t2
            assert not t2.pending, t2

        with Timeout(DELAY*2) as t1:
            with Timeout(DELAY) as t2:
                try:
                    sleep(DELAY*3)
                except Timeout, ex:
                    assert ex is t2, (ex, t2)
                assert t1.pending, t1
                assert not t2.pending, t2
        assert not t1.pending, t1
