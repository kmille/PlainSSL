#!/usr/bin/env python
r'''
Extract SSL/DTLS keys from programs that use OpenSSL.

Example usage, attach to an existing process, put keys in premaster.txt:

  PYTHONPATH=. \
  gdb -q -ex  'py import sslkeylog as skl; skl.start("premaster.txt")' \
    -p `pidof curl`

Run a new program while outputting keys to the default stderr
(you can set envvar SSLKEYLOGFILE to override this):

  PYTHONPATH=. gdb -q -ex 'py import sslkeylog as skl; skl.start()' \
      -ex r --args curl https://example.com


Recommended configuration: copy this file to ~/.gdb/sslkeylog.py and put
the following in your ~/.gdbinit:

    python
    import sys, os.path
    #sys.dont_write_bytecode = True # avoid *.pyc clutter
    sys.path.insert(0, os.path.expanduser('~/.gdb'))
    import sslkeylog as skl
    # Override default keylog (SSLKEYLOGFILE env or stderr)
    #skl.keylog_filename = '/tmp/premaster.txt'
    end

    define skl-batch
    dont-repeat
    handle all noprint pass
    handle SIGINT noprint pass
    py skl.start()
    end

Then you can simply execute:

    gdb -q -ex 'py skl.start()' -p `pidof curl`

To stop capturing keys, detach GDB or invoke 'skl.stop()'

If you are not interested in debugging the program, and only want to
extract keys, use the skl-batch command defined in gdbinit:

    SSLKEYLOGFILE=premaster.txt gdb -batch -ex skl-batch -p `pidof curl`

To stop capturing keys early, send SIGTERM to gdb. (Note that SIGTRAP is
used internally for breakpoints and should not be ignored.)
'''

import gdb
import errno
from os import getenv

# Default filename for new Keylog instances.
#keylog_filename = getenv('SSLKEYLOGFILE', '/dev/stderr')
keylog_filename = getenv('SSLKEYLOGFILE', 'keys.log')
_SSL_KEYLOG_HEADER = '# Automatically generated by sslkeylog.py\n'

def _read_as_hex(value, size):
    addr = value.address
    data = gdb.selected_inferior().read_memory(addr, size)
    return ''.join('%02X' % ord(x) for x in data)

def _ssl_get_master_key(ssl_ptr):
    session = ssl_ptr['session']
    if session != 0 and session['master_key_length'] > 0:
        return _read_as_hex(session['master_key'], 48)
    return None

def get_keylog_line(ssl_ptr):
    '''
    Returns (client_random, master_key) for the current SSL session.
    '''
    mk = _ssl_get_master_key(ssl_ptr)
    s3 = ssl_ptr['s3']
    if s3 == 0 or mk is None:
        return

    cr = _read_as_hex(s3['client_random'], 32)
    # Maybe optimize storage by using Session ID if available?
    #sid = _read_as_hex(self.ssl_ptr['session']['session_id'], 32)
    return (cr, mk)

class SKLFinishBreakpoint(gdb.FinishBreakpoint):
    '''Breaks on points where new key material is possibly available.'''
    def __init__(self, ssl_ptr, key_listener):
        # Mark as internal, it is expected to be gone as soon as this quits.
        gdb.FinishBreakpoint.__init__(self, internal=True)
        self.ssl_ptr = ssl_ptr
        self.key_listener = key_listener

    def stop(self):
        # Attempt to recover key material.
        info = get_keylog_line(self.ssl_ptr)
        if info:
            # Line consists of a cache key and actual key log line
            self.key_listener.notify(*info)
        return False # Continue execution

class SKLBreakpoint(gdb.Breakpoint):
    '''Breaks at function entrance and registers a finish breakpoint.'''
    def __init__(self, spec, key_listener):
        gdb.Breakpoint.__init__(self, spec)
        self.key_listener = key_listener

    def stop(self):
        # Retrieve SSL* parameter.
        ssl_ptr = gdb.selected_frame().read_var('s')
        #ssl_ptr = gdb.selected_frame().read_var('ssl')

        # Proceed with handshakes (finish function) before checking for keys.
        SKLFinishBreakpoint(ssl_ptr, self.key_listener)
        
        # Increase hit count for debugging (info breakpoints)
        # This number will be decremented when execution continues.
        self.ignore_count += 1
        
        return False # Continue execution

class Keylog(object):
    '''Listens for new key material and writes them to a file.'''
    def __init__(self, keylog_file):
        self.keylog_file = keylog_file
        # Remember written lines to avoid printing duplicates.
        self.written_items = set()

    def notify(self, client_random, master_key):
        '''Puts a new entry in the key log if not already known.'''
        if client_random not in self.written_items:
            line = 'CLIENT_RANDOM %s %s\n' % (client_random, master_key)
            self.keylog_file.write(line.encode('ascii'))

            # Assume client random is random enough as cache key.
            cache_key = client_random
            self.written_items.add(cache_key)

    def close():
        self.keylog_file.close()

    @classmethod
    def create(cls, filename):
        def needs_header(f):
            try:
                # Might fail for pipes (such as stdin).
                return f.tell() == 0
            except:
                return False

        # Byte output is needed for unbuffered I/O
        try:
            f = open(filename, 'ab', 0)
        except OSError as e:
            # Older gdb try to seek when append is requested. If seeking is not
            # possible (for stderr or pipes), use plain write mode.
            if e.errno == errno.ESPIPE:
                f = open(filename, 'wb', 0)
            else:
                raise
        if needs_header(f):
            f.write(_SSL_KEYLOG_HEADER.encode('ascii'))
        return cls(f)

# A shared Keylog instance.
_keylog_file = None

def start(sslkeylogfile=None, cont=True):
    '''
    :param sslkeylogfile: optional SSL keylog file name (overrides
    SSLKEYLOGFILE environment variable and its fallback value).
    :param cont: True to continue this process when paused.
    '''
    global keylog_filename
    if sslkeylogfile:
        keylog_filename = sslkeylogfile
    enable()

    # Continue the process when it was already started before.
    if cont and gdb.selected_thread():
        gdb.execute('continue')

def stop():
    '''Remove all breakpoints and close the key logfile.'''
    global _keylog_file
    if not _keylog_file:
        print('No active keylog session')
        return
    disable()
    _keylog_file.close()
    print('Logged %d entries in total' % _keylog_file.written_items)
    _keylog_file = None


# Remember enabled breakpoints
_locations = { name: None for name in (
    'SSL_connect',
    'SSL_do_handshake',
    'SSL_accept',
    'SSL_read',
    'SSL_write',
)}

def enable():
    '''Enable all SSL-related breakpoints.'''
    global _keylog_file
    if not _keylog_file:
        _keylog_file = Keylog.create(keylog_filename)
        print('Started logging SSL keys to %s' % keylog_filename)
    for name, breakpoint in _locations.items():
        if breakpoint:
            print('Breakpoint for %s is already active, ignoring' % name)
            continue
        _locations[name] = SKLBreakpoint(name, _keylog_file)

def disable():
    '''Disable all SSL-related breakpoints.'''
    for name, breakpoint in _locations.items():
        if breakpoint:
            msg = 'Deleting breakpoint %d' % breakpoint.number
            msg += ' (%s)' % breakpoint.location
            if breakpoint.hit_count > 0:
                msg += ' (called %d times)' % breakpoint.hit_count
            print(msg)
            breakpoint.delete()
            _locations[name] = None
