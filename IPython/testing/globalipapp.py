"""Global IPython app to support test running.

We must start our own ipython object and heavily muck with it so that all the
modifications IPython makes to system behavior don't send the doctest machinery
into a fit.  This code should be considered a gross hack, but it gets the job
done.
"""



#-----------------------------------------------------------------------------
#  Copyright (C) 2009-2010  The IPython Development Team
#
#  Distributed under the terms of the BSD License.  The full license is in
#  the file COPYING, distributed as part of this software.
#-----------------------------------------------------------------------------

#-----------------------------------------------------------------------------
# Imports
#-----------------------------------------------------------------------------

# stdlib
import builtins
import os
import sys
from types import MethodType

# our own
from . import tools

from IPython.frontend.terminal.interactiveshell import TerminalInteractiveShell

#-----------------------------------------------------------------------------
# Functions
#-----------------------------------------------------------------------------

# Hack to modify the %run command so we can sync the user's namespace with the
# test globals.  Once we move over to a clean magic system, this will be done
# with much less ugliness.

class py_file_finder(object):
    def __init__(self,test_filename):
        self.test_filename = test_filename
        
    def __call__(self,name):
        from IPython.utils.path import get_py_filename
        try:
            return get_py_filename(name)
        except IOError:
            test_dir = os.path.dirname(self.test_filename)
            new_path = os.path.join(test_dir,name)
            return get_py_filename(new_path)
    

def _run_ns_sync(self,arg_s,runner=None):
    """Modified version of %run that syncs testing namespaces.

    This is strictly needed for running doctests that call %run.
    """
    #print('in run_ns_sync', arg_s, file=sys.stderr)  # dbg
    finder = py_file_finder(arg_s)
    return get_ipython().magic_run_ori(arg_s, runner, finder)


class ipnsdict(dict):
    """A special subclass of dict for use as an IPython namespace in doctests.

    This subclass adds a simple checkpointing capability so that when testing
    machinery clears it (we use it as the test execution context), it doesn't
    get completely destroyed.
    """
    
    def __init__(self,*a):
        dict.__init__(self,*a)
        self._savedict = {}
        
    def clear(self):
        dict.clear(self)
        self.update(self._savedict)
        
    def _checkpoint(self):
        self._savedict.clear()
        self._savedict.update(self)

    def update(self,other):
        self._checkpoint()
        dict.update(self,other)

        # If '_' is in the namespace, python won't set it when executing code,
        # and we have examples that test it.  So we ensure that the namespace
        # is always 'clean' of it before it's used for test code execution.
        self.pop('_',None)

        # The builtins namespace must *always* be the real builtins module,
        # else weird stuff happens.  The main ipython code does have provisions
        # to ensure this after %run, but since in this class we do some
        # aggressive low-level cleaning of the execution namespace, we need to
        # correct for that ourselves, to ensure consitency with the 'real'
        # ipython.
        self['__builtins__'] = builtins


def get_ipython():
    # This will get replaced by the real thing once we start IPython below
    return start_ipython()


# A couple of methods to override those in the running IPython to interact
# better with doctest (doctest captures on raw stdout, so we need to direct
# various types of output there otherwise it will miss them).

def xsys(self, cmd):
    """Replace the default system call with a capturing one for doctest.
    """
    # We use getoutput, but we need to strip it because pexpect captures
    # the trailing newline differently from commands.getoutput
    print(self.getoutput(cmd, split=False).rstrip(), end='', file=sys.stdout)
    sys.stdout.flush()


def _showtraceback(self, etype, evalue, stb):
    """Print the traceback purely on stdout for doctest to capture it.
    """
    print(self.InteractiveTB.stb2text(stb), file=sys.stdout)


def start_ipython():
    """Start a global IPython shell, which we need for IPython-specific syntax.
    """
    global get_ipython

    # This function should only ever run once!
    if hasattr(start_ipython, 'already_called'):
        return
    start_ipython.already_called = True
    
    # Store certain global objects that IPython modifies
    _displayhook = sys.displayhook
    _excepthook = sys.excepthook
    _main = sys.modules.get('__main__')

    # Create custom argv and namespaces for our IPython to be test-friendly
    config = tools.default_config()

    # Create and initialize our test-friendly IPython instance.
    shell = TerminalInteractiveShell.instance(config=config, 
                                              user_ns=ipnsdict(),
                                              user_global_ns={}
                                              )

    # A few more tweaks needed for playing nicely with doctests...
    
    # These traps are normally only active for interactive use, set them
    # permanently since we'll be mocking interactive sessions.
    shell.builtin_trap.activate()

    # Modify the IPython system call with one that uses getoutput, so that we
    # can capture subcommands and print them to Python's stdout, otherwise the
    # doctest machinery would miss them.
    shell.system = MethodType(xsys, shell)
                       

    shell._showtraceback = MethodType(_showtraceback, shell)

    # IPython is ready, now clean up some global state...
    
    # Deactivate the various python system hooks added by ipython for
    # interactive convenience so we don't confuse the doctest system
    sys.modules['__main__'] = _main
    sys.displayhook = _displayhook
    sys.excepthook = _excepthook

    # So that ipython magics and aliases can be doctested (they work by making
    # a call into a global _ip object).  Also make the top-level get_ipython
    # now return this without recursively calling here again.
    _ip = shell
    get_ipython = _ip.get_ipython
    builtins._ip = _ip
    builtins.get_ipython = get_ipython

    return _ip
