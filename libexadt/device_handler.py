#! /usr/bin/env python3
# NOTE: These are only here until the code is fixed and should be removed.
# Do not copy them to new files
# mypy: no_check_untyped_defs

import os
from . import EXAConf
from .util import bytes2units
from collections import OrderedDict as odict

#{{{ Class DeviceError
class DeviceError(Exception):
    def __init__(self, msg):
        self.msg = "ERROR::DeviceHandler: " + msg
    def __str__(self):
        return repr(self.msg)
#}}}
 
class device_handler(object):
    """ 
    Implements all operations related to file- and block-device handling.
    """

#{{{ Init
    def __init__(self, ec):
        self.exaconf = ec
        self.min_auto_free_space = (1024 * 1024 * 1024 * 10)
        self.max_auto_used_space = self.min_auto_free_space * 5
        self.max_auto_internal_used_space = (1024 * 1024 * 1024 * 6)
        # we reserve 2GiB per container, because docker may need this space 
        # if the containers are created on the same partition
        # -> we also need some space for the integrated BucketFS files
        self.auto_reserved_size  = self.exaconf.get_num_nodes() * (1024 * 1024 * 1024 * 3) 
        # a ltitle buffer for container-internal device creation (i. e. "self contained" images)
        self.auto_internal_reserved_size  = (1024 * 1024 * 1024 * 1) 
        self.def_disk_name   = "disk1"
        self.auto_min_vol_size = (4 * 1024 * 1024 * 1024)
        self.vol_resize_step = (4 * 1024 * 1024 * 1024)
#}}}

#{{{ Get mount point
    def get_mount_point(self, path):
        """
        Returns the mount-point of the filesystem the given device belongs to.
        """
        path = os.path.realpath(os.path.abspath(path))
        while path != os.path.sep:
            if os.path.ismount(path):
                return path
            # go up one dir
            path = os.path.abspath(os.path.join(path, os.pardir))
        return path
#}}}

#{{{ Get free space
    def get_free_space(self, mount_point):
        """
        Returns the free space (in bytes) of the given filesystem.
        """
            
        stat = os.statvfs(mount_point)
        return stat.f_bfree * stat.f_bsize
#}}}

#{{{ Is mapped device
    def is_mapped_device(self, dev, disk_conf):
        """
        Checks if the given device is a mapped device.
        """

        dev = os.path.basename(dev)
        # strip the suffix if one exists 
        if dev.endswith(self.exaconf.data_dev_suffix):
            dev = dev[:-len(self.exaconf.data_dev_suffix)]
        elif dev.endswith(self.exaconf.meta_dev_suffix):
            dev = dev[:-len(self.exaconf.meta_dev_suffix)]
        # search the device_mapping for the given device
        # --> device-name in mapping may also have suffix!
        if "mapping" in disk_conf:
            for mdev,path in disk_conf.mapping:
                if mdev.endswith(self.exaconf.data_dev_suffix):
                    mdev = mdev[:-len(self.exaconf.data_dev_suffix)]
                elif mdev.endswith(self.exaconf.meta_dev_suffix):
                    mdev = mdev[:-len(self.exaconf.meta_dev_suffix)]
                if dev == mdev:
                    return True
        return False
#}}}

#{{{ Check free space
    def check_free_space(self):
        """
        Checks if the sum of the size of all sparse files is bigger
        than the free space on the associated device.
        """

        devices = set()
        mount_devices = odict()
        try:
            nodes_conf = self.exaconf.get_nodes()
            docker_conf = self.exaconf.get_docker_conf()
        except EXAConf.EXAConfError as e:
            raise DeviceError("Unable to read EXAConf: %s" % e)

        if docker_conf.device_type != "file":
            raise DeviceError("Space-check is only supported for file-devices!")

        # extract all file-devices from the given EXAConf
        for node_id in nodes_conf:
            my_conf = nodes_conf[node_id]
            # add mapped devices (they have absolute paths)
            for disk in my_conf.disks.values():
                if "mapped_devices" in disk:
                    for host_path, c in disk.mapped_devices:
                        # backwards compatibility: get filename tuple with extensions if device has meta file!
                        compat_files = self.exaconf.check_fix_local_dev_path(host_path)
                        for f in compat_files:
                            devices.add(f)
            # add "normal" file-devices
            for disk in my_conf.disks.values():
                for dev in disk.devices:
                    if not self.is_mapped_device(dev, disk):
                        dev_file = os.path.join(os.path.join(my_conf.docker_volume, self.exaconf.storage_dir), dev)
                        # backwards compatibility: get filename tuple with extensions if device has meta file!
                        compat_files = self.exaconf.check_fix_local_dev_path(dev_file)
                        for f in compat_files:
                            devices.add(f)

        # organize devices according to their filesystem
        for dev in devices:
            mount_point = self.get_mount_point(dev)
            if mount_point not in mount_devices:
                mount_devices[mount_point] = []
            mount_devices[mount_point].append(dev)
        # compute free space for each mountpoint
        sufficient_free_space = True
        for mount_point in mount_devices:
            part_free = self.get_free_space(mount_point)
            files_virt_size = sum([os.path.getsize(os.path.realpath(dev)) for dev in mount_devices[mount_point]])
            files_phys_size = sum([os.stat(os.path.realpath(dev)).st_blocks*512 for dev in mount_devices[mount_point]])
            if part_free < (files_virt_size - files_phys_size):
                print("Free space on '%s' is only %s, but accumulated size of (sparse) file-devices is %s!" % (mount_point, bytes2units(part_free), bytes2units(files_virt_size)))
                sufficient_free_space = False
        return sufficient_free_space
#}}}

#{{{ Is device file
    def is_device_file(self, name):
        """ Checks if the given filename indicates a storage device file. """
        return self.exaconf.dev_prefix in name
#}}}

 #{{{ Is data device file
    def is_data_device_file(self, name):
        """ Checks if filename indicates a storage DATA device file. """
        return self.exaconf.dev_prefix in name and self.exaconf.data_dev_suffix in name
#}}}

 #{{{ Is meta device file
    def is_meta_device_file(self, name):
        """ 
        Checks if filename indicates a storage META device file.

        (deprecated for superblock devices, but kept for backwards compatibility)
        """

        return self.exaconf.dev_prefix in name and self.exaconf.meta_dev_suffix in name
#}}}

#{{{ Get file name
    def get_file_name(self, storage_dir, node_conf, check_foreign):
        """
        Returns a valid device file name for the given directory.
        The name is created based on the entries in EXAConf, because there
        may be multiple directories containing device files (if mapping is used).
        """

        #check for foreign files if requested
        if check_foreign:
            try:
                files = os.listdir(storage_dir)
            except OSError as e:
                raise DeviceError("Error reading directory '%s': %s" % (storage_dir, e))
            # check for other files in that directory
            foreign_files = [f for f in files if not self.is_device_file(f)]
            if len(foreign_files) > 0:
                raise DeviceError("Found foreign files in '%s' that need to be removed first: %s" % (storage_dir, foreign_files))

        # build a list of all node devices 
        devices = []
        for disk in node_conf.disks.values():
            for dev in disk.devices:
                devices.append(dev)
        # find the highest number
        curr_num = 1
        if len(devices) > 0:
            curr_num = max([int(dev.split(".")[1]) for dev in devices]) + 1
        dev_file = os.path.join(storage_dir, self.exaconf.dev_prefix + str(curr_num))
        return dev_file
#}}}

#{{{ Get short  name
    def get_short_name(self, dev_name):
        """
        Returns the short name (i. e. without the suffix) of the given device.

        (deprecated for superblock devices, but kept for backwards compatibility)
        """

        short_name = dev_name
        if dev_name.endswith(self.exaconf.data_dev_suffix):
            short_name = dev_name[:-len(self.exaconf.data_dev_suffix)]
        elif dev_name.endswith(self.exaconf.meta_dev_suffix):
            short_name = dev_name[:-len(self.exaconf.meta_dev_suffix)]
        return short_name
#}}}

#{{{ Remove file devices
    def remove_file_devices(self, docker_root, node_conf):
        """
        Deletes all existing file-devices (incl. mapped devices) and removes them from EXAConf.
        Keeps the existing directories.
        """

        # make list of all directories (default storage_dir + mapped directories)
        directories = []
        for disk in node_conf.disks.values():
            if "mapping" in disk:
                for dev,path in disk.mapping:
                    # use parent directory in case of mapped files!
                    path = os.path.realpath(path)
                    if os.path.isfile(path):
                        path = os.path.dirname(path)
                    directories.append(path)
        directories.append(os.path.join(os.path.join(docker_root, node_conf.docker_volume), self.exaconf.storage_dir))
        deleted_devices = set()

        # we remove ALL files (no sub-folders) because we don't know if there are "old style" devices (with meta files)
        for d in directories:
            for f in os.listdir(d):
                fpath = os.path.join(d, f)
                try:
                    if os.path.isfile(fpath):
                        os.unlink(fpath)
                        deleted_devices.add(self.get_short_name(fpath))
                except Exception as e:
                    print("Failed to delete device file '%s': " % (fpath, e))

        self.exaconf.remove_node_disk(node_conf.id, disk="_all")
        return deleted_devices
#}}}

#{{{ Create node file devices
    def create_node_file_devices(self, node_id, disk, num, size, path, replace, no_odirect = False):
        """
        Creates $num data (sparese) files of size $size for a single node and adds them to EXAConf.
        If 'path' is not empty, the devices are created there and corresponding mapping entries
        are added to EXAConf.

        Returns a tuple of two dicts: created and deleted devices.
        """

        try:
            docker_conf = self.exaconf.get_docker_conf()
            nodes_conf = self.exaconf.get_nodes()
        except EXAConf.EXAConfError as e:
            raise DeviceError("Failed to read EXAConf: %s" % e)

        # sanity checks
        if docker_conf.device_type != 'file':
            raise DeviceError("Cluster has wrong DeviceType '%s'! Data files can only be used for clusters with DeviceType 'file'!" % docker_conf.device_type)

        disk = disk.strip()
        dest_dir = ""
        created_node_devices = []
        deleted_node_devices = []
        docker_root = docker_conf.root_dir
        my_conf = nodes_conf[node_id]
        if path and path.strip() != "":
            dest_dir = path.strip()
        else:
            dest_dir = os.path.join(os.path.join(docker_root, my_conf.docker_volume), self.exaconf.storage_dir)
        # remove existing devices if requested
        if replace:
            deleted_node_devices = self.remove_file_devices(docker_root, my_conf)
            # refresh config after deletion
            nodes_conf = self.exaconf.get_nodes()
            my_conf = nodes_conf[node_id]
        # create disk if it doesn't exist
        if "disks" not in my_conf or disk not in my_conf.disks:
            self.exaconf.add_node_disk(node_id, disk, no_odirect = no_odirect, component='exastorage')
        for i in range(num):
            dev_file = self.get_file_name(dest_dir, my_conf, False)
            # check if file already exists
            # --> can happen easily in case of external mappings
            if os.path.exists(dev_file) and not replace:
                raise DeviceError("File '%s' already exists! Please remove it." % dev_file)
            # create sparse file
            with open(dev_file, "wb") as d:
                d.truncate(size)
            # add device to EXAConf
            try:
                self.exaconf.add_node_device(node_id, disk,
                                             os.path.basename(dev_file),
                                             path if path and path != "" else None)
                # we need to refresh the node configuration for each device, 
                # otherwise the names would be wrong when adding multiple devices at once!
                nodes_conf = self.exaconf.get_nodes()
                my_conf = nodes_conf[node_id]
            except EXAConf.EXAConfError as e:
                raise DeviceError("Failed to read EXAConf: %s" % e)
            # store only short-name
            created_node_devices.append(dev_file)

        return (created_node_devices, deleted_node_devices)
#}}}

#{{{ Create file devices
    def create_file_devices(self, disk, num, size, path, replace, no_odirect = False):
        """
        Creates $num data (sparse) files of size $size for all nodes and adds them to EXAConf.
        If 'path' is not empty, the devices are created there and corresponding mapping entries
        are added to EXAConf.

        Returns a tuple of two dicts: created and deleted devices per node.
        """

        try:
            nodes_conf = self.exaconf.get_nodes()
        except EXAConf.EXAConfError as e:
            raise DeviceError("Failed to read EXAConf: %s" % e)

        disk = disk.strip()
        created_devices = odict()
        deleted_devices = odict()
        for node_id in nodes_conf:
            # create sub-directory for current node in case a path is given 
            node_path = ""
            if path and path.strip() != "":
                path = os.path.realpath(os.path.abspath(path))
                # raise error if path does not exist
                if not os.path.exists(path):
                    raise DeviceError("'%s' does not exist!" % path)
                node_path = os.path.join(path.strip(), nodes_conf[node_id].name)
                if not os.path.exists(node_path):
                    try:
                        os.makedirs(node_path)
                    except OSError as e:
                        raise DeviceError("Failed to create directory '%s': %s" % (node_path, e))
            devices = self.create_node_file_devices(node_id, disk, num, size, node_path, replace, no_odirect = no_odirect)
            created_devices[node_id] = devices[0]
            if len(devices[1]) > 0:
                deleted_devices[node_id] = devices[1]

        return (created_devices, deleted_devices)
#}}}

#{{{ Auto create file devices
    def auto_create_file_devices(self, container_internal=False, no_odirect=False, max_space=None):
        """
        Automatically determines the available free space in the root directory of the current
        cluster and creates one file device per node for the default disk. 'container_internal'
        has to be True if this function is called from within a container (e. g. during the 
        initialization of a self-contained image).

        Throws an exception if the cluster already contains disks and devices.
        """

        if max_space is None:
            max_space = self.max_auto_internal_used_space if container_internal else self.max_auto_used_space
        # Get and check available free space in root directory
        root_usable = 0
        root_free = self.get_free_space(self.get_mount_point(self.exaconf.root))
        if container_internal == True:
            root_usable = min(root_free - self.auto_internal_reserved_size, max_space)
        else:
            if root_free < self.min_auto_free_space:
                raise DeviceError("Free space on '%s' is only '%s' but '%s' are required for automatic file-device creation!" % 
                                  (self.exaconf.root, bytes2units(root_free), bytes2units(self.min_auto_free_space)))
            root_usable = min(root_free - self.auto_reserved_size, max_space)

        try:
            nodes_conf = self.exaconf.get_nodes()
        except EXAConf.EXAConfError as e:
            raise DeviceError("Failed to read EXAConf: %s" % e)

        # check if the nodes already have disks
        for node in nodes_conf.values():
            if len(node.disks) > 0:
                raise DeviceError("Devices can't be auto-generated because this cluster alreay has disks!") 
        
        bytes_per_node = root_usable // len(nodes_conf)

        # create the device-file in the local storage directory
        if container_internal == True:
            self.create_node_file_devices("11", self.def_disk_name, 1, bytes_per_node, 
                                          os.path.join(self.exaconf.container_root, self.exaconf.storage_dir),
                                          False, no_odirect = no_odirect)
        else:
            self.create_file_devices(self.def_disk_name, 1, bytes_per_node, "", False, no_odirect = no_odirect)

        try:
            # leave some room for the temporary volume!
            self.exaconf.use_disk_for_volumes(self.def_disk_name, bytes_per_node * 0.666, 
                                              min_vol_size = self.auto_min_vol_size,
                                              vol_resize_step = self.vol_resize_step)
        except EXAConf.EXAConfError as e:
            raise DeviceError("Failed to use new disk for the existing volumes: %s" % e)

#}}}
