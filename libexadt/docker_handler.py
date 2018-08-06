import os,docker,pprint,shutil,device_handler
from docker.utils import kwargs_from_env
import EXAConf
from utils import rotate_file
from EXAConf import config

ip_types = { 4: 'ipv4_address', 6: 'ipv6_address' }
 
#{{{ Class DockerError
class DockerError(Exception):
    def __init__(self, msg):
        self.msg = "ERROR::DockerHandler: " + msg
    def __str__(self):
        return repr(self.msg)
#}}}
 
class docker_handler:
    """ Implements all docker commands. Depends on the 'docker' python module (https://github.com/docker/docker-py). """

#{{{ Init
    def __init__(self, verbose=False, quiet=False):
        """
        Creates a new docker_handler and a docker.APIClient (used for communication with the docker service).
        """
        try:
            self.client = docker.APIClient(timeout=120, **kwargs_from_env())
        except AttributeError:
            self.client = docker.Client(timeout=120, **kwargs_from_env())

        self.verbose = verbose
        self.quiet = quiet
        if self.quiet:
            self.verbose = False
        self.def_container_cmd = None 
#}}}

#{{{ log
    def log(self, msg, no_nl=False):
        if not self.quiet:
            if no_nl:
                print msg,
            else:
                print msg
#}}}

#{{{ Set EXAConf object for this instace of docker-handler
    def set_exaconf(self, exaconf):
        """
        Set EXAConf instance for this docker_handler instance.
        """
        self.exaconf = exaconf
        self.cluster_name = self.exaconf.get_cluster_name()
        self.image = self.exaconf.get_docker_image()
        self.def_container_cmd = os.path.join(self.exaconf.os_dir, "libexec/exainit.py")
#}}}

#{{{ Docker version (debug)
    def version(self):
        """
        Returns information about the docker version on the current node.
        """
        try:
            res = self.client.version()
        except docker.errors.APIError as e:
            raise DockerError("Failed to query docker version: %s" % e)
        return res
#}}}

#{{{ Inspect image
    def inspect_image(self, image):
        """
        Returns the raw output of 'inspect_image()'.
        """
        try:
            res = self.client.inspect_image(image)
        except docker.errors.APIError as e:
            raise DockerError("Failed to inspect image '%s': %s" % (image, e))
        return res
#}}}

#{{{ Inspect containers
    def inspect_containers(self):
        """
        Returns the concatenated raw output of 'inspect_container()".
        """

        res = {}
        containers = self.get_containers(all=True)
        for container in containers:
            try:
                ci = self.client.inspect_container(container)
            except docker.errors.APIError as e:
                raise DockerError("Failed to inspect container '%s' : '%s'" % (self.container_name(container), e))
            res[self.container_name(container)] = ci
        return res
#}}}

#{{{ Create networks
    def create_networks(self):
        """ 
        Creates private and public docker networks.
        """

        created_networks = []
        # private network
        have_priv_net = self.exaconf.has_priv_net()
        if have_priv_net:
            priv_net_name = self.exaconf.get_priv_net_name()
            try:
                priv_net = self.exaconf.get_priv_net()
            except EXAConf.EXAConfError as e:
                raise DockerError("Failed to read EXAConf: %s" % e)
            # create private network
            self.log("Creating private network %s ('%s')..." % (priv_net, priv_net_name), no_nl=True)
            ipam_pool = docker.types.IPAMPool(subnet = priv_net)
            ipam_config = docker.types.IPAMConfig(pool_configs = [ipam_pool])
            try:
                net = self.client.create_network(priv_net_name, driver="bridge", ipam=ipam_config,
                                                 labels={"ClusterName":self.cluster_name,"Scope":"private"})
                # add name and type (for usage by create_containers())
                net['MyName'] = priv_net_name
                net['MyScope'] = 'private'
                created_networks.append(net)
                self.log("successful")
                if self.verbose:
                    print "Created the following network:"
                    pprint.pprint(net)
            except docker.errors.APIError as e:
                raise DockerError("Failed to create network: %s." % e)
        else:
            self.log("No private network specified")
   
        # public network
        have_pub_net = self.exaconf.has_pub_net()
        if have_pub_net:
            pub_net_name = self.exaconf.get_pub_net_name()
            try:
                pub_net = self.exaconf.get_pub_net()
            except EXAConf.EXAConfError as e:
                raise DockerError("Failed to read EXAConf: %s" % e)
            # create public network (if any)
            self.log("Creating public network %s ('%s')..." % (pub_net, pub_net_name), no_nl=True)
            ipam_pool = docker.types.IPAMPool(subnet = pub_net)
            ipam_config = docker.types.IPAMConfig(pool_configs = [ipam_pool])
            try:
                net = self.client.create_network(pub_net_name, driver="bridge", ipam=ipam_config,
                                                 labels={"ClusterName":self.cluster_name, "Scope":"public"})
                # add name and type (for usage by create_containers())
                net['MyName'] = pub_net_name
                net['MyScope'] = 'public'
                created_networks.append(net)
                self.log("successful")
                if self.verbose:
                    print "Created the following network:"
                    pprint.pprint(net)
            except docker.errors.APIError as e:
                raise DockerError("Failed to create network: %s." % e)
        else:
            self.log("No public network specified.")

        return created_networks
#}}}

#{{{ Delete networks            
    def delete_networks(self):
        """  
        Deletes private and public docker networks.
        """

        networks = []
        # get network names
        priv_net_name = self.exaconf.get_priv_net_name()
        pub_net_name = self.exaconf.get_pub_net_name()

        if pub_net_name and pub_net_name != "":
            networks.append(pub_net_name)
        if priv_net_name and priv_net_name != "":
            networks.append(priv_net_name)

        try:
            nets = self.client.networks(names=networks)
        except docker.errors.APIError as e:
            raise DockerError("Failed to query networks: %s." % e)
        if len(nets) == 0:
            self.log("No networks found for cluster '%s'." % self.cluster_name)
        else:
            err = False
            for net in nets:
                if self.verbose:
                    print "Going to remove the following network:"
                    pprint.pprint(net)
                try:
                    self.log("Removing network '%s'..." % net['Name'], no_nl=True)
                    self.client.remove_network(net['Id'])
                    self.log("successful")
                except docker.errors.APIError as e:
                    print "Failed to remove network '%s' : %s" % (net['Name'], e)
                    err = True
            if err:
                raise DockerError("Failed to remove all networks!")
#}}}
 
#{{{ Container name
    def container_name(self, container):
        """
        Extracts the name of a container.
        """
        return container['Names'][0].lstrip("/")
#}}}
  
#{{{ Container path
    def container_path(self, container):
        """
        Extracts the path of the container on the host (i. e. the directory that is mounted to '/exa').
        """
        host_path = None
        for mount in container['Mounts']:
            if mount['Destination'] == self.exaconf.container_root:
                host_path = mount['Source']
        return host_path
#}}}
 
#{{{ Get containers
    def get_containers(self, all=True):
        """ 
        Returns a list of all containers of the current cluster (only running ones if all==False). 
        They are identified using the 'ClusterName' label.
        """

        try:
            my_containers = self.client.containers(all=all, filters = {'label':'ClusterName=' + self.cluster_name})
        except docker.errors.APIError as e:
            raise DockerError("Failed to query containers for cluster '%s': %s" % (self.cluster_name, e))
        return my_containers
#}}}
 
#{{{ Get image conf
    def get_image_conf(self, image_name):
        """
        Returns a config containing information about the given image (e. g. all labels).
        """

        image_conf = config()
        try:
            image = self.client.inspect_image(image_name)
        except docker.errors.APIError as e:
            raise DockerError("Failed to query information about image '%s': %s" % (image_name, e))
        # add labels
        image_conf['labels'] =  config()
        for item in image['ContainerConfig']['Labels'].iteritems():
            image_conf['labels'][item[0]] = item[1]
        return image_conf
#}}}

#{{{ Create containers
    def create_containers(self, networks=None, cmd=None, auto_remove=False):
        """ 
        Creates one container per node. Takes care of volumes, block-devices, environment, labels
        and additional container configuration.
        
        Returns a list of created containers.
        """

        created_containers = []
        try:
            nodes_conf = self.exaconf.get_nodes_conf()
            if self.verbose:
                print "Found the following node configurations:"
                pprint.pprint(nodes_conf)
            docker_conf = self.exaconf.get_docker_conf()
            if self.verbose:
                print "Found the following docker config:"
                pprint.pprint(docker_conf)
            bucketfs_conf = self.exaconf.get_bucketfs_conf()
            if self.verbose:
                print "Found the following BucketFS config:"
                pprint.pprint(bucketfs_conf)
        except EXAConf.EXAConfError as e:
            raise DockerError("Failed to read EXAConf: %s" % e)

        if self.verbose:
            print "Using image '%s' and cluster name '%s'." % (self.image, self.cluster_name)

        if cmd and cmd != "":
            self.log("Using custom command '%s'." % cmd)
        else:
            cmd = self.def_container_cmd
                
        # separate  the first network from the remaing ones, so it can be given to
        # the container at creation time (necessary to avoid that the container 
        # is attached to the default network)
        first_net = None
        net_conf = None
        if networks and len(networks) > 0:
            first_net = networks.pop(0)
        # create containers for all nodes
        for node_id in nodes_conf.keys():
            container_name = self.cluster_name + "_" + str(node_id)
            my_conf = nodes_conf[node_id]
            devices = []

            # 1.) configure devices, volumes, first network and host-options
            # a. default node volumes
            binds = [my_conf.docker_volume + ":" + self.exaconf.container_root + ":rw"]
            volumes = [self.exaconf.container_root]
            # b. mapped file-devices
            # --> each file-device needs to be mounted into the storage-directory within the container
            if docker_conf.device_type == 'file':
                for disk in my_conf.disks.itervalues():
                    if disk.has_key("mapped_devices"):
                        for dev_host, dev_container in disk.mapped_devices:
                            binds.append(dev_host + ":" + dev_container + ":rw")
                            volumes.append(dev_container)
            # c. block devices
            # --> only takes effect if "privileged == False" (otherwise all devices are accessible anyway)
            elif docker_conf.device_type == 'block':
                for disk in my_conf.disks.itervalues():
                    for dev, meta in disk.devices:
                        # FIXME : use mapping if given
                        dev_host = os.path.join(os.path.join(my_conf.docker_volume, self.exaconf.storage_dir), dev)
                        dev_container = os.path.join(os.path.join(self.exaconf.container_root, self.exaconf.storage_dir), dev)
                        meta_host = os.path.join(os.path.join(my_conf.docker_volume, self.exaconf.storage_dir), meta)
                        meta_container = os.path.join(os.path.join(self.exaconf.container_root, self.exaconf.storage_dir), meta)
                        devices.append(dev_host+":"+dev_container+":rwm")
                        devices.append(meta_host+":"+meta_container+":rwm")
            # d. BucketFS volumes
            for bfs_name in bucketfs_conf.fs.keys():
                bfs_conf = bucketfs_conf.fs[bfs_name]
                if bfs_conf.has_key("path") and bfs_conf.path != "":
                    bfs_host = os.path.join(bfs_conf.path, my_conf.name, bfs_name)
                    bfs_container = os.path.join(self.exaconf.container_root, self.exaconf.bucketfs_dir, bfs_name)
                    binds.append(bfs_host + ":" + bfs_container + ":rw")
                    volumes.append(bfs_container)
            # e. Additional volumes
            for v in docker_conf.additional_volumes:
                binds.append(v)
                volumes.append(v.split(":")[1].strip())

            # port bindings
            port_binds = {}
            if my_conf.has_key("exposed_ports"):
                port_binds = dict(my_conf.exposed_ports)
            # create host config
            hc = self.client.create_host_config(privileged = docker_conf.privileged,
                                                cap_add = docker_conf.cap_add,
                                                cap_drop = docker_conf.cap_drop,
                                                network_mode = docker_conf.network_mode,
                                                auto_remove = auto_remove,
                                                binds = binds,
                                                devices = devices,
                                                port_bindings = port_binds)
            # create config for the first network (see above)
            if first_net:
                ip = ""
                if first_net['MyScope'] == 'private':
                    ip = my_conf.private_ip
                elif first_net['MyScope'] == 'public':
                    ip = my_conf.public_ip
                ep_conf = self.client.create_endpoint_config(**{ip_types[self.exaconf.ip_type(ip)]: ip}) 
                net_conf = self.client.create_networking_config({first_net['MyName']: ep_conf})

            # 2.) create container
            self.log("Creating container '%s'..." % container_name, no_nl=True)
            try:
                container = self.client.create_container(self.image,
                                                         hostname = my_conf.name,
                                                         detach = True,
                                                         stdin_open = True,
                                                         tty = True,
                                                         name = container_name,
                                                         labels = {'ClusterName' : self.cluster_name,
                                                                   'NodeID' : node_id,
                                                                   'Name' : my_conf.name},
                                                         environment = {'EXA_NODE_ID' : node_id},
                                                         stop_timeout = 60,
                                                         volumes = volumes,
                                                         host_config = hc,
                                                         networking_config = net_conf,
                                                         ports = port_binds.keys(),
                                                         entrypoint = cmd)
                created_containers.append(container)
                # add name (not part of the returned dict)
                container['MyName'] = container_name
                self.log("successful")
                if self.verbose:
                    print "Created the following container:"
                    pprint.pprint(container)
            except docker.errors.ImageNotFound as e:
                raise DockerError("Image '%s' not found: %s" % (self.image, e))
            except docker.errors.APIError as e:
                raise DockerError("Failed to create container: %s" % e)

            # 3.) attach container to the remaining network(s)
            if networks:
                for net in networks:
                    ip = ""
                    if net['MyScope'] == 'private':
                        ip = my_conf.private_ip
                    elif net['MyScope'] == 'public':
                        ip = my_conf.public_ip
                    self.log("Connecting container '%s' to network '%s' with IP '%s'..." % (container['MyName'], net['MyName'], ip), no_nl=True)
                    try:
                        self.client.connect_container_to_network(container = container['Id'],
                                                                 net_id = net['Id'],
                                                                 **{ip_types[self.exaconf.ip_type(ip)]: ip})
                        self.log("successful")
                    except docker.errors.APIError as e:
                        raise DockerError("Failed to connect network: %s" % e)

        return created_containers  
#}}}

#{{{ Start containers
    def start_containers(self, containers):
        """ 
        Starts all given containers.
        """

        started_containers = []

        for container in containers:
            if self.verbose:
                print "Going to start the following container:"
                pprint.pprint(container)
            try:
                self.log("Starting container '%s'..." % container['MyName'], no_nl=True)
                self.client.start(container=container['Id'])
                started_containers.append(container)
                self.log("successful")
            except docker.errors.APIError as e:
                raise DockerError("Failed to start container: %s" % e)
        return started_containers
#}}}

#{{{ Stop containers
    def stop_containers(self, timeout):
        """ 
        Stops all running containers from the current cluster. If they don't terminate within the given timeout, 
        they are killed.
        """

        containers = self.get_containers()
        if len(containers) == 0:
            self.log("No containers found for cluster '%s'." % self.cluster_name)
            return False

        # stop them
        num_running = 0
        num_stopped = 0
        err = False
        for container in containers:
            if self.verbose:
                print "Found the following container:"
                pprint.pprint(container)
            state = container['State']
            if state == 'running' or state == 'paused' or state == 'restarting':
                num_running += 1
                try:
                    self.log("Stopping container '%s'..." % self.container_name(container), no_nl=True)
                    self.client.stop(container['Id'], int(timeout))
                    num_stopped += 1
                    self.log("successful")
                except docker.errors.APIError as e:
                    print "Failed to stop container '%s': %s" % (self.container_name(container), e)
                    err = True
            else:
                num_stopped += 1

        if err:
            raise DockerError("Failed to stop all containers!")    
        if num_running == 0:
            self.log("No running containers found for cluster '%s'." % self.cluster_name)
        elif self.verbose:
            print "Successfully stopped %i containers." % num_running

        return num_stopped > 0
#}}}

#{{{ Remove containers
    def remove_containers(self):
        """ 
        Removes all exited containers (they have to be stopped before calling this function).
        """

        containers = self.get_containers()
        if len(containers) == 0:
            self.log("No containers found for cluster '%s'." % self.cluster_name)
            return False

        err = False
        for container in containers:
            state = container['State']
            if state == 'exited' or state == 'created':
                self.log("Removing container '%s'..." % self.container_name(container), no_nl=True)
                try:
                    self.client.remove_container(container['Id'])
                    self.log("successful")
                except docker.errors.APIError as e:
                    print "Failed to remove container '%s': %s" % (self.container_name(container), e)
                    err = True

        if err:
            raise DockerError("Failed to remove all containers!")

        return True
#}}}

#{{{ Cluster started
    def cluster_started(self):
        """ 
        Checks if containers of this cluster exist, i. e. the cluster has been started 
        and not stopped (there may be crashed containers, though).
        """

        containers = self.get_containers()
        if containers and len(containers) > 0:
            return True
        else:
            return False
#}}}
  
#{{{ Cluster online
    def cluster_online(self):
        """ 
        Checks if containers of this cluster exist AND are currently running.
        """

        containers = self.get_containers(all=False)
        if containers and len(containers) > 0:
            return True
        else:
            return False
#}}}
 
#{{{ Merge exaconf
    def merge_exaconf(self):
        """
        Merges the EXAConf copies within the containers into the 'external' EXAConf in the cluster root directory.
        """

        try:
            exaconf_list = []
            node_volumes = self.exaconf.get_docker_node_volumes()
            for n,volume in node_volumes.iteritems():
                node_etc_dir = os.path.join(volume, self.exaconf.etc_dir)
                if os.path.exists(os.path.join(node_etc_dir, "EXAConf")):
                    exaconf_list.append(EXAConf.EXAConf(node_etc_dir, True))
            if len(exaconf_list) > 0:
                self.exaconf.merge_node_uuids(exaconf_list)     
                self.log("Merged EXAConf from %i node(s)." % len(exaconf_list))
        except EXAConf.EXAConfError as e:
            self.log("Error while merging EXAConf: '%s'! Skipping merge." % e)
#}}}

#{{{ Start cluster
    def start_cluster(self, cmd=None, auto_remove=False, dummy_mode=False):
        """
        Starts the given cluster by:
            - checking the available free space (in case of file-devices)
            - copying EXAConf, license and SSL files to the docker-volume of all nodes (into 'etc/')
            - creating docker networks
            - creating docker containers for all cluster-nodes
            - starting docker containers
        If 'dummy_mode' is true, all checks and preparations are skipped. Only containers are created 
        and started. This can be used to execute arbitrary commands in the containers (if 'auto_remove'
        is also true, these containers will be removed by Docker as soon as the container process exits).
        """

        networks = None
        if not dummy_mode:
            # 0. sanity checks
            if self.cluster_started():
                raise DockerError("Cluster '%s' has already been started! Use 'stop-cluster' if you want to stop it." % self.cluster_name)
            docker_conf = self.exaconf.get_docker_conf()
            conf_img_version = self.exaconf.get_img_version()
            ic = self.get_image_conf(self.exaconf.get_docker_image())
            if ic.labels.version != conf_img_version:
                raise DockerError("EXAConf image version does not match that of the docker image ('%s' vs. '%s')! Please update the cluster before attempting to start it." % (conf_img_version, ic.labels.version))

            # 1. check free space in case of file-devices
            if self.exaconf.get_docker_device_type() == "file":
                dh = device_handler.device_handler(self.exaconf)
                if dh.check_free_space() == False:
                    raise DockerError("Check for space usage failed! Aborting startup.")
      
            # 2. merge EXAConf copies
            self.merge_exaconf()

            # 3. copy EXAConf and license to all node volumes
            conf_path = self.exaconf.get_conf_path()
            license = self.exaconf.get_license_file()
            node_volumes = self.exaconf.get_docker_node_volumes()
            self.log("Copying EXAConf and license to all node volumes.")
            for n,volume in node_volumes.iteritems():
                shutil.copy(conf_path, os.path.join(volume, self.exaconf.etc_dir))
                shutil.copy(license, os.path.join(volume, self.exaconf.etc_dir))
            # 3.1 Copy SSL files (if they exist)
            try:
                ssl_conf = self.exaconf.get_ssl_conf()
                if ssl_conf.has_key("cert") and os.path.isfile(ssl_conf.cert):
                    shutil.copy(ssl_conf.cert, os.path.join(volume, self.exaconf.ssl_dir))
                if ssl_conf.has_key("cert_key") and os.path.isfile(ssl_conf.cert_key):
                    shutil.copy(ssl_conf.cert_key, os.path.join(volume, self.exaconf.ssl_dir))
                if ssl_conf.has_key("cert_auth") and os.path.isfile(ssl_conf.cert_auth):
                    shutil.copy(ssl_conf.cert_auth, os.path.join(volume, self.exaconf.ssl_dir))
            except EXAConf.EXAConfError as e:
                print "Skipping SSL configuration (not present in EXAConf)."

            # 4. create networks (if network mode is not "host")
            if docker_conf.network_mode != "host":
                try:
                    networks = self.create_networks()
                except DockerError as e:
                    print "Error during startup! Cleaning up..."
                    self.stop_cluster(30)   
                    raise e

        # 4. create containers
        try:
            containers = self.create_containers(networks, cmd=cmd, auto_remove=auto_remove)
        except DockerError as e:
            print "Error during startup! Cleaning up..."
            self.stop_cluster(30)            
            raise e

        # 5. start containers
        try:
            self.start_containers(containers)
        except DockerError as e:
            print "Error during startup! Cleaning up..."
            self.stop_cluster(30)            
            raise e
#}}}

#{{{ Stop cluster
    def stop_cluster(self, timeout):
        """
        Stops the given cluster by:
            - stopping all running docker containers
            - removing all stopped docker containers
            - removing all docker networks
        """
        ex = None
        try:
            stopped = self.stop_containers(timeout)
        except Exception as e:
            print "Error during shutdown: %s! Continueing anyway..." % e
            ex = e
        # save logs and merge EXAConf before removing containers
        self.save_logs()
        self.merge_exaconf()
        try:
            if stopped:
                self.remove_containers()
        except DockerError as e:
            print "Error during shutdown! Continueing anyway..."
            ex = e
        try:
            self.delete_networks()
        except DockerError as e:
            ex = e

        if ex:
            raise ex
#}}}
 
#{{{ Save logs
    def save_logs(self):
        """
        Stores the output of 'docker logs' within '/exa/logs/docker/'.
        """
        containers = self.get_containers(all = True)
        for container in containers:
            host_path = self.container_path(container)
            if host_path:
                try:
                    logs = self.client.logs(container, stderr=True, stdout=True, timestamps=False)
                    current_file = os.path.join(host_path, self.exaconf.docker_log_dir, self.exaconf.docker_logs_filename)
                    rotate_file(current_file, self.exaconf.docker_max_logs_copies)
                    with open(current_file, "w") as current_logs:
                        current_logs.write(logs)
                except docker.errors.APIError as e:
                    print "Failed to retrieve docker logs from container '%s' : %s" % (self.container_name(container), e)
                except IOError as e:
                    print "Failed to write docker logs to '%s': %s" % (current_file, e)
#}}}
 
#{{{ Execute container
    def execute_container(self, cmd, container, stdin=False, tty=False, quiet=False):
        """
        Executes the given command in the given container.
        """

        # Retrieve node name (Label changed in 6.0.1 from "Hostname" to "Name")
        # or use container name instead
        node_name = container['Names'][0].lstrip("/")
        if 'Hostname' in container['Labels']:
            node_name =  container['Labels']['Hostname']
        elif 'Name' in container['Labels']:
            node_name =  container['Labels']['Name']
                                                              
        # This local 'quiet' only suppresses the additional output, not the one from the command (like 'self.quiet')!
        if not quiet:
            self.log("=== Executing '%s' in container '%s' ===" % (cmd, node_name))
        try:
            exi = self.client.exec_create(container=container, cmd=cmd, stdin=stdin, tty=tty)
        except docker.errors.APIError as e:
            raise DockerError("Failed to create exec instance for command '%s': %s" % (cmd, e))
        try:
            res = self.client.exec_start(exec_id=exi, tty=False, stream=True)
        except docker.errors.APIError as e:
            raise DockerError("Failed to start exec instance for command '%s': %s" % (cmd, e))

        for val in res:
            self.log(val)
#}}}

#{{{ Execute
    def execute(self, cmd, all=False, stdin=False, tty=False, quiet=False):
        """
        Executes the given command either on a single (random) running container or all running containers (if 'all' == True).
        """
        
        success = False
        containers = self.get_containers(all=False)
        for container in containers:
            if container['State'] == 'running':
                self.execute_container(cmd, container, stdin=stdin, tty=tty, quiet=quiet)
                success = True
                if all == False:
                    break

        if not success:
            self.log("No running containers found for the given cluster.")
#}}}

#{{{ Run
    def run(self, image, cmd=None):
        """
        Creates and runs a new container with given image and command (in privileged mode). The container is immediately removed when it exits.
        """

        # create a simple client for this task
        try:
            c = docker.DockerClient()
            c.containers.run(image, command=cmd, auto_remove=True, privileged=True)
        except docker.errors.ContainerError as e:
            raise DockerError("Container with image '%s' and command '%s' exited with error: %s" % (image, cmd if cmd else "None", e))
        except docker.errors.ImageNotFound as e:
            raise DockerError("Image '%s' could not be found: %s" % (image, e))
        except docker.errors.APIError as e:
            raise DockerError("Error running a container with image '%s' and command '%s': %s" % (image, cmd if cmd else "None", e))
#}}}
