#{{{ Class RPCError
class RPCError(Exception):
    def __init__(self, msg):
        self.msg = "ERROR::RPCHandler: " + msg
    def __str__(self):
        return repr(self.msg)
#}}}
 
class rpc_handler(object):
    """
    Implements all RPC code (i. e. executes commands in the (virtual) cluster using RPCs).
    """
#{{{ Init
    def __init__(self, exaconf, url, quiet=False):
        """
        Creates a new rpc_handler that connects to the given URL.
        """
        self.server = url
        self.exaconf = exaconf
        self.quiet = quiet
#}}}
 
#{{{ log
    def log(self, msg, no_nl=False):
        if not self.quiet:
            if no_nl:
                print(msg, end=' ')
            else:
                print(msg)
#}}}
 
#{{{ Stop database
    def stop_database(self, name="all"):
        """
        Stops all databases (or the given one) in the current cluster.
        """

        #TODO
#}}}
 
#{{{ Start database
    def start_database(self, name="all"):
        """
        Starts all databases (or the given one) in the current cluster.
        """

        # TODO
#}}}
 
