from . import rpc_handler, docker_handler

class docker_rpc_handler(rpc_handler.rpc_handler):
    """
    An RPC handler class that simulates RPCs by executing commands in Docker containers.
    """

#{{{ Init
    def __init__(self, exaconf, quiet=False, dh=None):
        """
        Creates a new docker_rpc_handler (which creates a new docker_handler if not given).
        """
        rpc_handler.rpc_handler.__init__(self, exaconf, "NULL", quiet=quiet)
        self.dh = dh
        if not self.dh:
            try:
                self.dh = docker_handler.docker_handler(quiet=quiet)
                self.dh.set_exaconf(self.exaconf)
            except docker_handler.DockerError as e:
                raise e
#}}}

#{{{ Stop database
    def stop_database(self, name="all"):
        """
        Stops all databases (or the given one) in the current cluster.
        """

        filters = None
        if name != "all":
            filters = {"name": name}
        db_configs = self.exaconf.get_databases(filters=filters)

        if len(db_configs) == 0:
            if name == 'all':
                self.log("No databases found.")
            else:
                self.log("Could not find database '%s'!" % name)
            return False
        
        for db in db_configs.iterkeys():
            self.dh.execute("dwad_client stop-wait %s" % db, quiet=True)
        return True
#}}}
 
#{{{ Kill database
    def kill_database(self, name="all"):
        """
        Immediately force-stops all databases (or the given one) in the current cluster.
        """

        filters = None
        if name != "all":
            filters = {"name": name}
        db_configs = self.exaconf.get_databases(filters=filters)

        if len(db_configs) == 0:
            if name == 'all':
                self.log("No databases found.")
            else:
                self.log("Could not find database '%s'!" % name)
            return False
        
        for db in db_configs.iterkeys():
            self.dh.execute("dwad_client stop-force %s" % db, quiet=True)
        return True
#}}}
 
#{{{ Start database
    def start_database(self, name="all"):
        """
        Starts all databases (or the given one) in the current cluster.
        """

        filters = None
        if name != "all":
            filters = {"name": name}
        db_configs = self.exaconf.get_databases(filters=filters)
 
        if len(db_configs) == 0:
            if name == 'all':
                self.log("No databases found.")
            else:
                self.log("Could not find database '%s'!" % name)
            return False
        
        for db in db_configs.iterkeys():
            self.dh.execute("dwad_client start-wait %s" % db, quiet=True)
        return True
#}}}
         
#{{{ List databases
    def list_databases(self, short=False):
        """
        Lists all existing databases.
        """

        if short == True:
            self.dh.execute("dwad_client shortlist", quiet=True)
        else:
            self.dh.execute("dwad_client list", quiet=True)
        return True
#}}}

