import os, ConfigParser
from .EXAConf import config
 
#{{{ Class ConfError
class ConfError(Exception):
    """
    The exadt configuration exception class.
    """
    def __init__(self, msg):
        self.msg = "Conf: " + msg
    def __str__(self):
        return repr(self.msg)
#}}}
 
class exadt_conf:
    """ Read and write exadt's own config file."""

#{{{ Init
    def __init__(self):
        """
        Creates a new exadt_conf containing the content of all available exadt configuration files
        (i. e. $HOME/.exadt.conf and /etc/exadt.conf)
        """
        self.config = ConfigParser.SafeConfigParser()
        # make it case-sensitive
        self.config.optionxform = str
        # try all valid configuration files
        self.conf_paths = self.config.read(['/etc/exadt.conf', os.path.expanduser('~/.exadt.conf')])
        # if none exists: create the personal one
        if len(self.conf_paths) == 0:
            self.conf_paths.append(os.path.expanduser('~/.exadt.conf'))
#}}}
    
#{{{ Extract name 
    def extract_name(self, section):
        """ Extracts cluster name from given section name. """
        return section.split(":")[1].strip()
#}}}

#{{{ Root exists
    def root_exists(self, root):
        """ 
        Checks if the root-directory is already in use by a cluster.
        """
        for section in self.config.sections():
            if self.config.get(section, "root") == root:
                print "Root directory '%s' is already used by cluster '%s'." % (root, self.extract_name(section))
                return True
        return False
#}}}

#{{{ Cluster exists
    def cluster_exists(self, name):
        for section in self.config.sections():
            if self.extract_name(section) == name:
                return True
        return False
#}}}

#{{{ Get root 
    def get_root(self, cluster):
        """ Returns the root directory of the given cluster (or "") """
        if self.cluster_exists(cluster):
            return self.config.get("Cluster : %s" % cluster, "root")
        else:
            raise ConfError("Cluster '%s' does not exist in %s!" % (cluster, self.conf_paths))
            
#}}}

#{{{ Get conf paths
    def get_conf_paths(self):
        """
        Returns all possible paths for exadt configuration files.
        """
        return self.conf_paths
#}}}

#{{{ Create cluster
    def create_cluster(self, name, root):
        """ 
        Creates a new cluster entry and stores it in all configuration files
        (i. e. in $HOME/.exadt.conf and /etc/exadt.conf)
        """
        cluster_section = "Cluster : %s" % name
        if self.config.has_section(cluster_section):
            raise ConfError("Cluster '%s' already exists (root = '%s')." % (name, self.config.get(cluster_section, "root")))
        if not self.root_exists(root):
            self.config.add_section(cluster_section)
            self.config.set(cluster_section, "root", root)
            # write to all config files that have been read
            for cp in self.conf_paths:
                with open(cp, 'w') as conf_file:
                    self.config.write(conf_file)
            print "Successfully created cluster '%s' with root directory '%s'." % (name, root)
#}}}
 
#{{{ Delete cluster
    def delete_cluster(self, name):
        """ 
        Deletes the given cluster from all configuration files. 
        """
        if self.cluster_exists(name):
            self.config.remove_section("Cluster : %s" % name)
            # write to all config files that have been read
            for cp in self.conf_paths:
                with open(cp, 'w') as conf_file:
                    self.config.write(conf_file)
            print "Successfully removed cluster '%s'." % name
        else:
            raise ConfError("Cluster '%s' does not exist!")
#}}}

#{{{ Get clusters
    def get_clusters(self):
        """ 
        Returns a dict containing all clusters and their root directory. 
        """
        clusters = config()
        for section in self.config.sections():
            cluster = self.extract_name(section)
            clusters[cluster] = self.get_root(cluster)
        return clusters
#}}}
