# -*- coding: utf-8 -*-
"""Display formatters.


Authors:

* Robert Kern
* Brian Granger
"""
#-----------------------------------------------------------------------------
# Copyright (c) 2010, IPython Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------

#-----------------------------------------------------------------------------
# Imports
#-----------------------------------------------------------------------------

# Stdlib imports
import abc
# We must use StringIO, as cStringIO doesn't handle unicode properly.
from io import StringIO

# Our own imports
from IPython.config.configurable import Configurable
from IPython.external import pretty
from IPython.utils.traitlets import Bool, Dict, Int, Str


#-----------------------------------------------------------------------------
# The main DisplayFormatter class
#-----------------------------------------------------------------------------


class DisplayFormatter(Configurable):

    # When set to true only the default plain text formatter will be used.
    plain_text_only = Bool(False, config=True)

    # A dict of formatter whose keys are format types (MIME types) and whose
    # values are subclasses of BaseFormatter.
    formatters = Dict(config=True)
    def _formatters_default(self):
        """Activate the default formatters."""
        formatter_classes = [
            PlainTextFormatter,
            HTMLFormatter,
            SVGFormatter,
            PNGFormatter,
            LatexFormatter,
            JSONFormatter
        ]
        d = {}
        for cls in formatter_classes:
            f = cls(config=self.config)
            d[f.format_type] = f
        return d

    def format(self, obj, include=None, exclude=None):
        """Return a format data dict for an object.

        By default all format types will be computed.

        The following MIME types are currently implemented:

        * text/plain
        * text/html
        * text/latex
        * application/json
        * image/png
        * immage/svg+xml

        Parameters
        ----------
        obj : object
            The Python object whose format data will be computed.
        include : list or tuple, optional
            A list of format type strings (MIME types) to include in the
            format data dict. If this is set *only* the format types included
            in this list will be computed.
        exclude : list or tuple, optional
            A list of format type string (MIME types) to exclue in the format
            data dict. If this is set all format types will be computed,
            except for those included in this argument.

        Returns
        -------
        format_dict : dict
            A dictionary of key/value pairs, one or each format that was
            generated for the object. The keys are the format types, which
            will usually be MIME type strings and the values and JSON'able
            data structure containing the raw data for the representation in
            that format.
        """
        format_dict = {}

        # If plain text only is active
        if self.plain_text_only:
            formatter = self.formatters['text/plain']
            try:
                data = formatter(obj)
            except:
                # FIXME: log the exception
                raise
            if data is not None:
                format_dict['text/plain'] = data
            return format_dict

        for format_type, formatter in list(self.formatters.items()):
            if include is not None:
                if format_type not in include:
                    continue
            if exclude is not None:
                if format_type in exclude:
                    continue
            try:
                data = formatter(obj)
            except:
                # FIXME: log the exception
                raise
            if data is not None:
                format_dict[format_type] = data
        return format_dict

    @property
    def format_types(self):
        """Return the format types (MIME types) of the active formatters."""
        return list(self.formatters.keys())


#-----------------------------------------------------------------------------
# Formatters for specific format types (text, html, svg, etc.)
#-----------------------------------------------------------------------------


class FormatterABC(object, metaclass=abc.ABCMeta):
    """ Abstract base class for Formatters.

    A formatter is a callable class that is responsible for computing the
    raw format data for a particular format type (MIME type). For example,
    an HTML formatter would have a format type of `text/html` and would return
    the HTML representation of the object when called.
    """

    # The format type of the data returned, usually a MIME type.
    format_type = 'text/plain'

    # Is the formatter enabled...
    enabled = True

    @abc.abstractmethod
    def __call__(self, obj):
        """Return a JSON'able representation of the object.

        If the object cannot be formatted by this formatter, then return None
        """
        try:
            return repr(obj)
        except TypeError:
            return None


class BaseFormatter(Configurable):
    """A base formatter class that is configurable.

    This formatter should usually be used as the base class of all formatters.
    It is a traited :class:`Configurable` class and includes an extensible
    API for users to determine how their objects are formatted. The following
    logic is used to find a function to format an given object.

    1. The object is introspected to see if it has a method with the name
       :attr:`print_method`. If is does, that object is passed to that method
       for formatting.
    2. If no print method is found, three internal dictionaries are consulted
       to find print method: :attr:`singleton_printers`, :attr:`type_printers`
       and :attr:`deferred_printers`.

    Users should use these dictionaries to register functions that will be
    used to compute the format data for their objects (if those objects don't
    have the special print methods). The easiest way of using these
    dictionaries is through the :meth:`for_type` and :meth:`for_type_by_name`
    methods.

    If no function/callable is found to compute the format data, ``None`` is
    returned and this format type is not used.
    """

    format_type = Str('text/plain')

    enabled = Bool(True, config=True)

    print_method = Str('__repr__')

    # The singleton printers.
    # Maps the IDs of the builtin singleton objects to the format functions.
    singleton_printers = Dict(config=True)
    def _singleton_printers_default(self):
        return {}

    # The type-specific printers.
    # Map type objects to the format functions.
    type_printers = Dict(config=True)
    def _type_printers_default(self):
        return {}

    # The deferred-import type-specific printers.
    # Map (modulename, classname) pairs to the format functions.
    deferred_printers = Dict(config=True)
    def _deferred_printers_default(self):
        return {}

    def __call__(self, obj):
        """Compute the format for an object."""
        if self.enabled:
            obj_id = id(obj)
            try:
                obj_class = getattr(obj, '__class__', None) or type(obj)
                if hasattr(obj_class, self.print_method):
                    printer = getattr(obj_class, self.print_method)
                    return printer(obj)
                try:
                    printer = self.singleton_printers[obj_id]
                except (TypeError, KeyError):
                    pass
                else:
                    return printer(obj)
                for cls in pretty._get_mro(obj_class):
                    if cls in self.type_printers:
                        return self.type_printers[cls](obj)
                    else:
                        printer = self._in_deferred_types(cls)
                        if printer is not None:
                            return printer(obj)
                return None
            except Exception:
                pass
        else:
            return None

    def for_type(self, typ, func):
        """Add a format function for a given type.

        Parameters
        -----------
        typ : class
            The class of the object that will be formatted using `func`.
        func : callable
            The callable that will be called to compute the format data. The
            call signature of this function is simple, it must take the
            object to be formatted and return the raw data for the given
            format. Subclasses may use a different call signature for the
            `func` argument.
        """
        oldfunc = self.type_printers.get(typ, None)
        if func is not None:
            # To support easy restoration of old printers, we need to ignore
            # Nones.
            self.type_printers[typ] = func
        return oldfunc

    def for_type_by_name(self, type_module, type_name, func):
        """Add a format function for a type specified by the full dotted
        module and name of the type, rather than the type of the object.

        Parameters
        ----------
        type_module : str
            The full dotted name of the module the type is defined in, like
            ``numpy``.
        type_name : str
            The name of the type (the class name), like ``dtype``
        func : callable
            The callable that will be called to compute the format data. The
            call signature of this function is simple, it must take the
            object to be formatted and return the raw data for the given
            format. Subclasses may use a different call signature for the
            `func` argument.
        """
        key = (type_module, type_name)
        oldfunc = self.deferred_printers.get(key, None)
        if func is not None:
            # To support easy restoration of old printers, we need to ignore
            # Nones.
            self.deferred_printers[key] = func
        return oldfunc

    def _in_deferred_types(self, cls):
        """
        Check if the given class is specified in the deferred type registry.

        Returns the printer from the registry if it exists, and None if the
        class is not in the registry. Successful matches will be moved to the
        regular type registry for future use.
        """
        mod = getattr(cls, '__module__', None)
        name = getattr(cls, '__name__', None)
        key = (mod, name)
        printer = None
        if key in self.deferred_printers:
            # Move the printer over to the regular registry.
            printer = self.deferred_printers.pop(key)
            self.type_printers[cls] = printer
        return printer


class PlainTextFormatter(BaseFormatter):
    """The default pretty-printer.

    This uses :mod:`IPython.external.pretty` to compute the format data of
    the object. If the object cannot be pretty printed, :func:`repr` is used.
    See the documentation of :mod:`IPython.external.pretty` for details on
    how to write pretty printers.  Here is a simple example::

        def dtype_pprinter(obj, p, cycle):
            if cycle:
                return p.text('dtype(...)')
            if hasattr(obj, 'fields'):
                if obj.fields is None:
                    p.text(repr(obj))
                else:
                    p.begin_group(7, 'dtype([')
                    for i, field in enumerate(obj.descr):
                        if i > 0:
                            p.text(',')
                            p.breakable()
                        p.pretty(field)
                    p.end_group(7, '])')
    """

    # The format type of data returned.
    format_type = Str('text/plain')

    # This subclass ignores this attribute as it always need to return
    # something.
    enabled = Bool(True, config=False)

    # Look for a __pretty__ methods to use for pretty printing.
    print_method = Str('__pretty__')

    # Whether to pretty-print or not.
    pprint = Bool(True, config=True)

    # Whether to be verbose or not.
    verbose = Bool(False, config=True)

    # The maximum width.
    max_width = Int(79, config=True)

    # The newline character.
    newline = Str('\n', config=True)

    # Use the default pretty printers from IPython.external.pretty.
    def _singleton_printers_default(self):
        return pretty._singleton_pprinters.copy()

    def _type_printers_default(self):
        return pretty._type_pprinters.copy()

    def _deferred_printers_default(self):
        return pretty._deferred_type_pprinters.copy()

    #### FormatterABC interface ####

    def __call__(self, obj):
        """Compute the pretty representation of the object."""
        if not self.pprint:
            try:
                return repr(obj)
            except TypeError:
                return ''
        else:
            # This uses use StringIO, as cStringIO doesn't handle unicode.
            stream = StringIO()
            printer = pretty.RepresentationPrinter(stream, self.verbose,
                self.max_width, self.newline,
                singleton_pprinters=self.singleton_printers,
                type_pprinters=self.type_printers,
                deferred_pprinters=self.deferred_printers)
            printer.pretty(obj)
            printer.flush()
            return stream.getvalue()


class HTMLFormatter(BaseFormatter):
    """An HTML formatter.

    To define the callables that compute the HTML representation of your
    objects, define a :meth:`__html__` method or use the :meth:`for_type`
    or :meth:`for_type_by_name` methods to register functions that handle
    this.
    """
    format_type = Str('text/html')

    print_method = Str('__html__')


class SVGFormatter(BaseFormatter):
    """An SVG formatter.

    To define the callables that compute the SVG representation of your
    objects, define a :meth:`__svg__` method or use the :meth:`for_type`
    or :meth:`for_type_by_name` methods to register functions that handle
    this.
    """
    format_type = Str('image/svg+xml')

    print_method = Str('__svg__')


class PNGFormatter(BaseFormatter):
    """A PNG formatter.

    To define the callables that compute the PNG representation of your
    objects, define a :meth:`__png__` method or use the :meth:`for_type`
    or :meth:`for_type_by_name` methods to register functions that handle
    this. The raw data should be the base64 encoded raw png data.
    """
    format_type = Str('image/png')

    print_method = Str('__png__')


class LatexFormatter(BaseFormatter):
    """A LaTeX formatter.

    To define the callables that compute the LaTeX representation of your
    objects, define a :meth:`__latex__` method or use the :meth:`for_type`
    or :meth:`for_type_by_name` methods to register functions that handle
    this.
    """
    format_type = Str('text/latex')

    print_method = Str('__latex__')


class JSONFormatter(BaseFormatter):
    """A JSON string formatter.

    To define the callables that compute the JSON string representation of
    your objects, define a :meth:`__json__` method or use the :meth:`for_type`
    or :meth:`for_type_by_name` methods to register functions that handle
    this.
    """
    format_type = Str('application/json')

    print_method = Str('__json__')


FormatterABC.register(BaseFormatter)
FormatterABC.register(PlainTextFormatter)
FormatterABC.register(HTMLFormatter)
FormatterABC.register(SVGFormatter)
FormatterABC.register(PNGFormatter)
FormatterABC.register(LatexFormatter)
FormatterABC.register(JSONFormatter)


def format_display_data(obj, include=None, exclude=None):
    """Return a format data dict for an object.

    By default all format types will be computed.

    The following MIME types are currently implemented:

    * text/plain
    * text/html
    * text/latex
    * application/json
    * image/png
    * immage/svg+xml

    Parameters
    ----------
    obj : object
        The Python object whose format data will be computed.

    Returns
    -------
    format_dict : dict
        A dictionary of key/value pairs, one or each format that was
        generated for the object. The keys are the format types, which
        will usually be MIME type strings and the values and JSON'able
        data structure containing the raw data for the representation in
        that format.
    include : list or tuple, optional
        A list of format type strings (MIME types) to include in the
        format data dict. If this is set *only* the format types included
        in this list will be computed.
    exclude : list or tuple, optional
        A list of format type string (MIME types) to exclue in the format
        data dict. If this is set all format types will be computed,
        except for those included in this argument.
    """
    from IPython.core.interactiveshell import InteractiveShell

    InteractiveShell.instance().display_formatter.format(
        obj,
        include,
        exclude
    )
