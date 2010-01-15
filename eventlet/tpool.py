# Copyright (c) 2007-2009, Linden Research, Inc.
# Copyright (c) 2007, IBM Corp.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import threading
import sys

from Queue import Empty, Queue

from eventlet import api
from eventlet import greenio
from eventlet import greenthread

__all__ = ['execute', 'Proxy', 'killall']

QUIET=True

_rfile = _wfile = None

def _signal_t2e():
    _wfile.write(' ')
    _wfile.flush()
    
_reqq = None
_rspq = None

def tpool_trampoline():
    global _reqq, _rspq
    while(True):
        try:
            _c = _rfile.read(1)
            assert _c != ""
        except ValueError:
            break  # will be raised when pipe is closed
        while not _rspq.empty():
            try:
                (e,rv) = _rspq.get(block=False)
                e.send(rv)
                rv = None
            except Empty:
                pass    

def esend(meth,*args, **kwargs):
    global _reqq, _rspq
    e = greenthread.Event()
    _reqq.put((e,meth,args,kwargs))
    return e

SYS_EXCS = (KeyboardInterrupt, SystemExit)


def tworker():
    global _reqq, _rspq
    while(True):
        msg = _reqq.get()
        if msg is None:
            return
        (e,meth,args,kwargs) = msg
        rv = None
        try:
            rv = meth(*args,**kwargs)
        except SYS_EXCS:
            raise
        except Exception,exn:
            rv = sys.exc_info()
        _rspq.put((e,rv))
        meth = args = kwargs = e = rv = None
        _signal_t2e()


def erecv(e):
    rv = e.wait()
    if isinstance(rv,tuple) and len(rv) == 3 and isinstance(rv[1],Exception):
        import traceback
        (c,e,tb) = rv
        if not QUIET:
            traceback.print_exception(c,e,tb)
            traceback.print_stack()
        raise c,e,tb
    return rv


def execute(meth,*args, **kwargs):
    """
    Execute *meth* in a Python thread, blocking the current coroutine/
    greenthread until the method completes.

    The primary use case for this is to wrap an object or module that is not
    amenable to monkeypatching or any of the other tricks that Eventlet uses
    to achieve cooperative yielding.  With tpool, you can force such objects to
    cooperate with green threads by sticking them in native threads, at the cost
    of some overhead.
    """
    setup()
    e = esend(meth,*args,**kwargs)
    rv = erecv(e)
    return rv


def proxy_call(autowrap, f, *args, **kwargs):
    """
    Call a function *f* and returns the value.  If the type of the return value
    is in the *autowrap* collection, then it is wrapped in a :class:`Proxy`
    object before return.  
    
    Normally *f* will be called in the threadpool with :func:`execute`; if the
    keyword argument "nonblocking" is set to ``True``, it will simply be 
    executed directly.  This is useful if you have an object which has methods
    that don't need to be called in a separate thread, but which return objects
    that should be Proxy wrapped.
    """
    if kwargs.pop('nonblocking',False):
        rv = f(*args, **kwargs)
    else:
        rv = execute(f,*args,**kwargs)
    if isinstance(rv, autowrap):
        return Proxy(rv, autowrap)
    else:
        return rv

class Proxy(object):
    """
    A simple proxy-wrapper of any object, in order to forward every method
    invocation onto a thread in the native-thread pool.  A key restriction is
    that the object's methods cannot use Eventlet primitives without great care,
    since the Eventlet dispatcher runs on a different native thread.
    
    Construct the Proxy with the instance that you want proxied.  The optional 
    parameter *autowrap* is used when methods are called on the proxied object.  
    If a method on the proxied object returns something whose type is in 
    *autowrap*, then that object gets a Proxy wrapped around it, too.  An 
    example use case for this is ensuring that DB-API connection objects 
    return cursor objects that are also Proxy-wrapped.
    """
    def __init__(self, obj,autowrap=()):
        self._obj = obj
        self._autowrap = autowrap

    def __getattr__(self,attr_name):
        f = getattr(self._obj,attr_name)
        if not callable(f):
            return f
        def doit(*args, **kwargs):
            return proxy_call(self._autowrap, f, *args, **kwargs)
        return doit

    # the following are a buncha methods that the python interpeter
    # doesn't use getattr to retrieve and therefore have to be defined
    # explicitly
    def __getitem__(self, key):
        return proxy_call(self._autowrap, self._obj.__getitem__, key)
    def __setitem__(self, key, value):
        return proxy_call(self._autowrap, self._obj.__setitem__, key, value)
    def __deepcopy__(self, memo=None):
        return proxy_call(self._autowrap, self._obj.__deepcopy__, memo)
    def __copy__(self, memo=None):
        return proxy_call(self._autowrap, self._obj.__copy__, memo)
    # these don't go through a proxy call, because they're likely to
    # be called often, and are unlikely to be implemented on the
    # wrapped object in such a way that they would block
    def __eq__(self, rhs):
        return self._obj.__eq__(rhs)
    def __repr__(self):
        return self._obj.__repr__()
    def __str__(self):
        return self._obj.__str__()
    def __len__(self):
        return len(self._obj)
    def __nonzero__(self):
        return bool(self._obj)



_nthreads = int(os.environ.get('EVENTLET_THREADPOOL_SIZE', 20))
_threads = {}
_coro = None
_setup_already = False
def setup():
    global _rfile, _wfile, _threads, _coro, _setup_already, _reqq, _rspq
    if _setup_already:
        return
    else:
        _setup_already = True
    try:
        _rpipe, _wpipe = os.pipe()
        _wfile = os.fdopen(_wpipe,"w",0)
        _rfile = os.fdopen(_rpipe,"r",0)
        ## Work whether or not wrap_pipe_with_coroutine_pipe was called
        if not isinstance(_rfile, greenio.GreenPipe):
            _rfile = greenio.GreenPipe(_rfile)
    except ImportError:
        # This is Windows compatibility -- use a socket instead of a pipe because
        # pipes don't really exist on Windows.
        import socket
        from eventlet import util
        sock = util.__original_socket__(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('localhost', 0))
        sock.listen(50)
        csock = util.__original_socket__(socket.AF_INET, socket.SOCK_STREAM)
        csock.connect(('localhost', sock.getsockname()[1]))
        nsock, addr = sock.accept()
        _rfile = greenio.Green_fileobject(greenio.GreenSocket(csock))
        _wfile = nsock.makefile()

    _reqq = Queue(maxsize=-1)
    _rspq = Queue(maxsize=-1)
    for i in range(0,_nthreads):
        _threads[i] = threading.Thread(target=tworker)
        _threads[i].setDaemon(True)
        _threads[i].start()

    _coro = greenthread.spawn_n(tpool_trampoline)

def killall():
    global _setup_already, _reqq, _rspq, _rfile, _wfile
    if not _setup_already:
        return
    for i in _threads:
        _reqq.put(None)
    for thr in _threads.values():
        thr.join()
    if _coro:
        api.kill(_coro)
    greenthread.sleep(0.01)
    _rfile.close()
    _wfile.close()
    _rfile = None
    _wfile = None
    _reqq = None
    _rspq = None
    _setup_already = False
