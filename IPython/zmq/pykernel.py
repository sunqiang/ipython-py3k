#!/usr/bin/env python
"""A simple interactive kernel that talks to a frontend over 0MQ.

Things to do:

* Implement `set_parent` logic. Right before doing exec, the Kernel should
  call set_parent on all the PUB objects with the message about to be executed.
* Implement random port and security key logic.
* Implement control messages.
* Implement event loop and poll version.
"""

#-----------------------------------------------------------------------------
# Imports
#-----------------------------------------------------------------------------

# Standard library imports.
import builtins
from code import CommandCompiler
import sys
import time
import traceback

# System library imports.
import zmq

# Local imports.
from IPython.utils.traitlets import HasTraits, Instance
from .completer import KernelCompleter
from .entry_point import base_launch_kernel, make_default_main
from .session import Session, Message

#-----------------------------------------------------------------------------
# Main kernel class
#-----------------------------------------------------------------------------

class Kernel(HasTraits):

    # Private interface

    # This is a dict of port number that the kernel is listening on. It is set
    # by record_ports and used by connect_request.
    _recorded_ports = None

    #---------------------------------------------------------------------------
    # Kernel interface
    #---------------------------------------------------------------------------

    session = Instance(Session)
    reply_socket = Instance('zmq.Socket')
    pub_socket = Instance('zmq.Socket')
    req_socket = Instance('zmq.Socket')

    def __init__(self, **kwargs):
        super(Kernel, self).__init__(**kwargs)
        self.user_ns = {}
        self.history = []
        self.compiler = CommandCompiler()
        self.completer = KernelCompleter(self.user_ns)

        # Build dict of handlers for message types
        msg_types = [ 'execute_request', 'complete_request', 
                      'object_info_request', 'shutdown_request' ]
        self.handlers = {}
        for msg_type in msg_types:
            self.handlers[msg_type] = getattr(self, msg_type)

    def start(self):
        """ Start the kernel main loop.
        """
        while True:
            ident,msg = self.session.recv(self.reply_socket,0)
            assert ident is not None, "Missing message part."
            omsg = Message(msg)
            print(file=sys.__stdout__)
            print(omsg, file=sys.__stdout__)
            handler = self.handlers.get(omsg.msg_type, None)
            if handler is None:
                print("UNKNOWN MESSAGE TYPE:", omsg, file=sys.__stderr__)
            else:
                handler(ident, omsg)

    def record_ports(self, xrep_port, pub_port, req_port, hb_port):
        """Record the ports that this kernel is using.

        The creator of the Kernel instance must call this methods if they
        want the :meth:`connect_request` method to return the port numbers.
        """
        self._recorded_ports = {
            'xrep_port' : xrep_port,
            'pub_port' : pub_port,
            'req_port' : req_port,
            'hb_port' : hb_port
        }

    #---------------------------------------------------------------------------
    # Kernel request handlers
    #---------------------------------------------------------------------------

    def execute_request(self, ident, parent):
        try:
            code = parent['content']['code']
        except:
            print("Got bad msg: ", file=sys.__stderr__)
            print(Message(parent), file=sys.__stderr__)
            return
        pyin_msg = self.session.send(self.pub_socket, 'pyin',{'code':code}, parent=parent)

        try:
            comp_code = self.compiler(code, '<zmq-kernel>')

            # Replace raw_input. Note that is not sufficient to replace 
            # raw_input in the user namespace.
            raw_input = lambda prompt='': self._raw_input(prompt, ident, parent)
            builtins.raw_input = raw_input

            # Set the parent message of the display hook and out streams.
            sys.displayhook.set_parent(parent)
            sys.stdout.set_parent(parent)
            sys.stderr.set_parent(parent)

            exec(comp_code, self.user_ns, self.user_ns)
        except:
            etype, evalue, tb = sys.exc_info()
            tb = traceback.format_exception(etype, evalue, tb)
            exc_content = {
                'status' : 'error',
                'traceback' : tb,
                'ename' : str(etype.__name__),
                'evalue' : str(evalue)
            }
            exc_msg = self.session.send(self.pub_socket, 'pyerr', exc_content, parent)
            reply_content = exc_content
        else:
            reply_content = { 'status' : 'ok', 'payload' : {} }
            
        # Flush output before sending the reply.
        sys.stderr.flush()
        sys.stdout.flush()

        # Send the reply.
        reply_msg = self.session.send(self.reply_socket, 'execute_reply', reply_content, parent, ident=ident)
        print(Message(reply_msg), file=sys.__stdout__)
        if reply_msg['content']['status'] == 'error':
            self._abort_queue()

    def complete_request(self, ident, parent):
        matches = {'matches' : self._complete(parent),
                   'status' : 'ok'}
        completion_msg = self.session.send(self.reply_socket, 'complete_reply',
                                           matches, parent, ident)
        print(completion_msg, file=sys.__stdout__)

    def object_info_request(self, ident, parent):
        context = parent['content']['oname'].split('.')
        object_info = self._object_info(context)
        msg = self.session.send(self.reply_socket, 'object_info_reply',
                                object_info, parent, ident)
        print(msg, file=sys.__stdout__)

    def shutdown_request(self, ident, parent):
        content = dict(parent['content'])
        msg = self.session.send(self.reply_socket, 'shutdown_reply',
                                content, parent, ident)
        msg = self.session.send(self.pub_socket, 'shutdown_reply',
                                content, parent, ident)
        print(msg, file=sys.__stdout__)
        time.sleep(0.1)
        sys.exit(0)

    #---------------------------------------------------------------------------
    # Protected interface
    #---------------------------------------------------------------------------

    def _abort_queue(self):
        while True:
            try:
                ident,msg = self.session.recv(self.reply_socket, zmq.NOBLOCK)
            except zmq.ZMQError as e:
                if e.errno == zmq.EAGAIN:
                    break
            else:
                assert ident is not None, "Missing message part."
            print("Aborting:", file=sys.__stdout__)
            print(Message(msg), file=sys.__stdout__)
            msg_type = msg['msg_type']
            reply_type = msg_type.split('_')[0] + '_reply'
            reply_msg = self.session.send(self.reply_socket, reply_type, {'status':'aborted'}, msg, ident=ident)
            print(Message(reply_msg), file=sys.__stdout__)
            # We need to wait a bit for requests to come in. This can probably
            # be set shorter for true asynchronous clients.
            time.sleep(0.1)

    def _raw_input(self, prompt, ident, parent):
        # Flush output before making the request.
        sys.stderr.flush()
        sys.stdout.flush()

        # Send the input request.
        content = dict(prompt=prompt)
        msg = self.session.send(self.req_socket, 'input_request', content, parent)

        # Await a response.
        ident,reply = self.session.recv(self.req_socket, 0)
        try:
            value = reply['content']['value']
        except:
            print("Got bad raw_input reply: ", file=sys.__stderr__)
            print(Message(parent), file=sys.__stderr__)
            value = ''
        return value

    def _complete(self, msg):
        return self.completer.complete(msg.content.line, msg.content.text)

    def _object_info(self, context):
        symbol, leftover = self._symbol_from_context(context)
        if symbol is not None and not leftover:
            doc = getattr(symbol, '__doc__', '')
        else:
            doc = ''
        object_info = dict(docstring = doc)
        return object_info

    def _symbol_from_context(self, context):
        if not context:
            return None, context

        base_symbol_string = context[0]
        symbol = self.user_ns.get(base_symbol_string, None)
        if symbol is None:
            symbol = builtins.__dict__.get(base_symbol_string, None)
        if symbol is None:
            return None, context

        context = context[1:]
        for i, name in enumerate(context):
            new_symbol = getattr(symbol, name, None)
            if new_symbol is None:
                return symbol, context[i:]
            else:
                symbol = new_symbol

        return symbol, []

#-----------------------------------------------------------------------------
# Kernel main and launch functions
#-----------------------------------------------------------------------------

def launch_kernel(ip=None, xrep_port=0, pub_port=0, req_port=0, hb_port=0,
                  independent=False):
    """ Launches a localhost kernel, binding to the specified ports.

    Parameters
    ----------
    ip  : str, optional
        The ip address the kernel will bind to.
    
    xrep_port : int, optional
        The port to use for XREP channel.

    pub_port : int, optional
        The port to use for the SUB channel.

    req_port : int, optional
        The port to use for the REQ (raw input) channel.

    hb_port : int, optional
        The port to use for the hearbeat REP channel.

    independent : bool, optional (default False) 
        If set, the kernel process is guaranteed to survive if this process
        dies. If not set, an effort is made to ensure that the kernel is killed
        when this process dies. Note that in this case it is still good practice
        to kill kernels manually before exiting.

    Returns
    -------
    A tuple of form:
        (kernel_process, xrep_port, pub_port, req_port)
    where kernel_process is a Popen object and the ports are integers.
    """
    extra_arguments = []
    if ip is not None:
        extra_arguments.append('--ip')
        if isinstance(ip, str):
            extra_arguments.append(ip)
    
    return base_launch_kernel('from IPython.zmq.pykernel import main; main()',
                              xrep_port, pub_port, req_port, hb_port,
                              independent, extra_arguments=extra_arguments)

main = make_default_main(Kernel)

if __name__ == '__main__':
    main()
