"""Monkeypatch nose to accept any callable as a method.

By default, nose's ismethod() fails for static methods.
Once this is fixed in upstream nose we can disable it.

Note: merely importing this module causes the monkeypatch to be applied."""

#-----------------------------------------------------------------------------
#  Copyright (C) 2009  The IPython Development Team
#
#  Distributed under the terms of the BSD License.  The full license is in
#  the file COPYING, distributed as part of this software.
#-----------------------------------------------------------------------------

#-----------------------------------------------------------------------------
# Imports
#-----------------------------------------------------------------------------

import unittest
import nose.loader
from inspect import ismethod, isfunction

#-----------------------------------------------------------------------------
# Classes and functions
#-----------------------------------------------------------------------------

def getTestCaseNames(self, testCaseClass):
    """Override to select with selector, unless
    config.getTestCaseNamesCompat is True
    """
    if self.config.getTestCaseNamesCompat:
        return unittest.TestLoader.getTestCaseNames(self, testCaseClass)

    def wanted(attr, cls=testCaseClass, sel=self.selector):
        item = getattr(cls, attr, None)
        # MONKEYPATCH: replace this:
        #if not ismethod(item):
        #    return False
        # return sel.wantMethod(item)
        # With:
        if ismethod(item):
            return sel.wantMethod(item)
        # static method or something. If this is a static method, we
        # can't get the class information, and we have to treat it
        # as a function.  Thus, we will miss things like class
        # attributes for test selection
        if isfunction(item):
            return sel.wantFunction(item)
        return False
        # END MONKEYPATCH
    
    cases = list(filter(wanted, dir(testCaseClass)))
    for base in testCaseClass.__bases__:
        for case in self.getTestCaseNames(base):
            if case not in cases:
                cases.append(case)
    # add runTest if nothing else picked
    if not cases and hasattr(testCaseClass, 'runTest'):
        cases = ['runTest']
    if self.sortTestMethodsUsing:
        try:
            cases.sort(key=self.sortTestMethodsUsing)
        except TypeError: # Takes care of things trying to use old cmp functions.
            cases.sort(key=unittest.CmpToKey(self.sortTestMethodsUsing))
    return cases


##########################################################################
# Apply monkeypatch here
nose.loader.TestLoader.getTestCaseNames = getTestCaseNames
##########################################################################
