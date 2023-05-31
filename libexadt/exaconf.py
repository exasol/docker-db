import os, sys, argparse, time, getpass, random
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from libconfd.common import util
    from libconfd.common.database import db_reorder_affinities
    from libconfd.EXAConf import EXAConfError
    import exacos
else:
    try:
        from libexadt.EXAConf import EXAConfError
        from libexadt import util
        assert EXAConfError, util #silence pyflakes
    except ImportError:
        from libconfd.common import util
        from libconfd.common.database import db_reorder_affinities
        from libconfd.EXAConf import EXAConfError
        import exacos

import exacos_constants

my_version = exacos_constants.full_version()

# {{{ Setup logging
class log:
    try:
        if not os.path.exists('/var/run/ecos_unix_auth'):
            raise RuntimeError('No COS used.')
        import exacos
        _logger = exacos.logger("ConfD")
    except:
        _logger = None

    @staticmethod
    def _log_stderr_internal(log_type: str, msg: str) -> None:
        msgtemp = f'[{{}}] {log_type}: {{}}\n'
        if log_type != "Info":
            sys.stderr.write(msgtemp.format(time.strftime('%Y-%m-%d %H:%M:%S'), msg))

    @classmethod
    def _log_internal(cls, log_type: str, msg: str) -> None:
        if cls._logger:
            exalog = getattr(cls._logger, f'log{log_type}')
            exalog(f"EXACONF: {msg}")
        cls._log_stderr_internal(log_type, msg)

    @classmethod
    def error(cls, msg: str):
        cls._log_internal("Error", msg)

    @classmethod
    def info(cls, msg: str):
        cls._log_internal("Info", msg)# }}}
# {{{ str2bool
def str2bool(v):
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')
# }}}
# {{{ Print version
def print_version(cmd):
    print(my_version)
# }}}
# {{{ Read EXAConf
def read_exaconf(filename, ro = False, initialized = False):
    try: exaconf = util.read_exaconf(filename, ro, initialized)
    except EXAConfError as e:
        log.error(str(e).replace('ERROR::EXAConf: ', ''))
        sys.exit(1)
    return exaconf
# }}}
# {{{ Add node
def add_node(cmd):
    """
    Adds a new node to the given EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.add_node(nid = cmd.node_id, priv_net=cmd.priv_net, pub_net=cmd.pub_net)
# }}}
# {{{ Modify node
def modify_node(cmd):
    """
    Modify an existing node.
    """
    exaconf = read_exaconf(cmd.exaconf)
    if cmd.node_id != "_all" and not exaconf.node_exists(cmd.node_id):
        print("Node '%s' does not exist in EXAConf '%s'!" % (cmd.node_id, cmd.exaconf))
        return 1
    nodes = exaconf.get_nodes()
    node_conf = nodes[cmd.node_id]
    # modify supported parameters
    if (cmd.pub_net and cmd.pub_ip) or (cmd.priv_net and cmd.priv_ip):
        print("You can either change the IP or the network. Not both!")
        return 1
    if cmd.priv_net:
        node_conf.private_net = cmd.priv_net
        if 'private_ip' in node_conf:
            del node_conf.private_ip
    if cmd.pub_net:
        node_conf.public_net = cmd.pub_net
        if 'public_ip' in node_conf:
            del node_conf.public_ip
    if cmd.priv_ip:
        node_conf.private_ip = cmd.priv_ip
        if 'private_net' in node_conf:
            del node_conf.private_net
    if cmd.pub_ip:
        node_conf.public_ip = cmd.pub_ip
        if 'public_net' in node_conf:
            del node_conf.public_net

    exaconf.set_node_conf(node_conf, cmd.node_id)
# }}}
# {{{ Remove node
def remove_node(cmd):
    """
    Removes a node from the given EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.remove_node(nid = cmd.node_id, force=cmd.force)
# }}}
# {{{ Add node disk
def add_node_disk(cmd):
    """
    Adds an empty storage disk to the given node in EXAConf.
    """

    exaconf = read_exaconf(cmd.exaconf)
    devices = [ d.strip() for d in cmd.devices.split(",") if d.strip() != "" ] if cmd.devices else None
    drives = [ d.strip() for d in cmd.drives.split(",") if d.strip() != "" ] if cmd.drives else None
    exaconf.add_node_disk(cmd.node, cmd.disk, component = cmd.component, devices = devices, drives = drives, overwrite_existing = cmd.overwrite_existing)
# }}}
# {{{ Remove node disk
def remove_node_disk(cmd):
    """
    Removes the given storage disk (or all disks) from the given node in EXAConf.
    """

    exaconf = read_exaconf(cmd.exaconf)
    exaconf.remove_node_disk(cmd.node, cmd.disk)
# }}}
# {{{ Add node device
def add_node_device(cmd):
    """
    Adds a device to an existing node in the given EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.add_node_device(cmd.node_id, cmd.disk, cmd.device, cmd.path)
# }}}
# {{{ Remove node device
def remove_node_device(cmd):
    """
    Removes a device from an existing node in the given EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.remove_node_device(cmd.node_id, cmd.disk, cmd.device)
# }}}
# {{{ Add volume
def add_volume(cmd):
    """
    Adds a new volume to the given EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    vol_nodes = [ n.strip() for n in cmd.nodes.split(",") if n.strip() != "" ]
    try: vol_owner = tuple( int(o.strip()) for o in cmd.owner.split(":") if o.strip() != "" )
    except: vol_owner = None
    if vol_owner is None or len(vol_owner) != 2:
        print("Please specify user and group ID for owner (e.g. 1000:1000)")
        return 1
    exaconf.add_volume(
        name = cmd.name,
        vol_type = cmd.type,
        size = cmd.size,
        disk = cmd.disk,
        redundancy = cmd.redundancy,
        nodes = vol_nodes,
        owner = vol_owner,
        num_master_nodes = cmd.num_master_nodes,
        perm = cmd.perm,
        labels = cmd.labels,
        block_size = cmd.block_size,
        stripe_size = cmd.stripe_size
    )
# }}}
# {{{ Modify volume
def modify_volume(cmd):
    """
    Modify an existing volume.
    """
    exaconf = read_exaconf(cmd.exaconf)
    volumes = exaconf.get_volumes()
    if cmd.name != "_all" and cmd.name not in volumes:
        print("Volume '%s' does not exist in EXAConf '%s'!" % (cmd.name, cmd.exaconf))
        return 1
    vol_conf = volumes[cmd.name]
    # change supported options
    if cmd.owner:
        vol_owner = tuple( int(o.strip()) for o in cmd.owner.split(":") if o.strip() != "" )
        vol_conf.owner = vol_owner
    if cmd.size:
        vol_conf.size = cmd.size
    if cmd.disk:
        vol_conf.disk = cmd.disk
    if cmd.nodes:
        vol_conf.nodes = [ n.strip() for n in cmd.nodes.split(',') if n.strip() != '' ]
        #adapt to new nr. of nodes, but may be overwritten below
        vol_conf.num_master_nodes = len(vol_conf.nodes)
    if cmd.num_master_nodes:
        vol_conf.num_master_nodes = cmd.num_master_nodes
    if cmd.labels:
        vol_conf.labels = cmd.labels
    if cmd.redundancy:
        vol_conf.redundancy = cmd.redundancy
    exaconf.set_volume_conf(vol_conf, cmd.name)
# }}}
# {{{ Add remote volume
def add_remote_volume(cmd):
    """
    Adds a remote volume to the given EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    try: vol_owner = tuple( int(o.strip()) for o in cmd.owner.split(":") if o.strip() != "" )
    except: vol_owner = None
    if vol_owner is None or len(vol_owner) != 2:
        print("Please specify user and group ID for owner (e.g. 1000:1000)")
        return 1
    exaconf.add_remote_volume(cmd.type, cmd.url, vol_owner,
                              remote_volume_name = cmd.name,
                              remote_volume_id = cmd.id,
                              labels = cmd.labels,
                              username = cmd.username,
                              password = cmd.passwd,
                              options = cmd.options)
# }}}
# {{{ Remove volume
def remove_volume(cmd):
    """
    Removes the given volume from the given EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.remove_volume(cmd.name, cmd.force)
# }}}
# {{{ Remove remote volume
def remove_remote_volume(cmd):
    """
    Removes the given remote volume from the given EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.remove_remote_volume(remote_volume_name = cmd.name, remote_volume_id = None, force=cmd.force)
# }}}
# {{{ Set storage conf
def set_storage_conf(cmd):
    """
    Applies the given EXAStorage to the given EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    st_conf = exaconf.get_storage_conf()
    if cmd.bg_rec_enabled is not None:
        st_conf.bg_rec_enabled = cmd.bg_rec_enabled
    if cmd.bg_rec_limit is not None:
        st_conf.bg_rec_limit = cmd.bg_rec_limit
    if cmd.space_warn_threshold is not None:
        st_conf.space_warn_threshold = cmd.space_warn_threshold
    exaconf.set_storage_conf(st_conf)
# }}}
# {{{ Encode shadow passwd
def encode_shadow_passwd(cmd):
    """
    Encodes given password and into an /etc/shadow compatible SHA512 hash. Prompts for password if none is given.
    """
    passwd = cmd.passwd
    if passwd is None:
        passwd = getpass.getpass()
    print(util.encode_shadow_passwd(passwd))
# }}}
# {{{ Encode db passwd
def encode_db_passwd(cmd):
    """
    Encodes given password and into an DB compatible tigerhash hash. Prompts for password if none is given.
    """
    passwd = cmd.passwd
    if passwd is None:
        passwd = getpass.getpass()
    print(exacos.get_db_passwd_hash(passwd, random.randint(1, 2**32)))
# }}}
# {{{ Reorder database afinities
def reorder_database_affinities(cmd):
    """
    Reorder database nodes to match the volume nodes. Only usable in a running cluster.
    """
    st = exacos.storage()
    db = exacos.exa_db(cmd.db_name)
    try: db_reorder_affinities(db, st, log = lambda X: sys.stdout.write('%s\n' % X))
    except Exception as err:
        print('ERROR: %s' % str(err))
        return 1
# }}}
# {{{ List groups
def list_groups(cmd):
    """
    Lists all groups in EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    for g in exaconf.get_groups().items():
        print("%s : %i" % (g[0], g[1].id))
# }}}
# {{{ Add group
def add_group(cmd):
    """
    Add a group to EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.add_group(groupname=cmd.name, groupid=cmd.id)
# }}}
# {{{ Remove group
def remove_group(cmd):
    """
    Remove a group from EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.remove_group(cmd.name)
# }}}
# {{{ List users
def list_users(cmd):
    """
    Lists all users in EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    for u in exaconf.get_users().items():
        print("= %s (%i) =" % (u[0], u[1].id))
        print("  group : %s" % u[1].group)
        print("  login : %s" % ("enabled" if u[1].login_enabled else "disabled"))
        if "passwd" in u[1]:
            print("  passwd : %s" % u[1].passwd)
        if "additional_groups" in u[1]:
            print("  groups : %s" % ", ".join(u[1].additional_groups))
        if "authorized_keys" in u[1]:
            print("  auth-keys : %s" % "\n              ".join(u[1].authorized_keys))
# }}}
# {{{ Add user
def add_user(cmd):
    """
    Add a user to EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    passwd = cmd.passwd
    encode_passwd = False if cmd.encode_passwd is None else True
    add_groups = [ g.strip() for g in cmd.groups.split(",") if g.strip() != "" ] if cmd.groups else None
    auth_keys = None
    if cmd.auth_keys:
        auth_keys = [ k.strip() for k in cmd.auth_keys.split(",") if k.strip() != "" ]
    elif cmd.auth_keys_file:
        with open(cmd.auth_keys_file) as f:
            auth_keys = [ l.strip() for l in f ]
    # prompt for password if not given and always encode it
    if passwd is None and cmd.prompt_passwd:
        passwd = getpass.getpass()
        encode_passwd = True
    exaconf.add_user(username = cmd.name, userid = cmd.id,
                     group = cmd.group, login_enabled = cmd.login_enabled,
                     password = passwd,
                     encode_passwd = encode_passwd,
                     additional_groups = add_groups,
                     authorized_keys = auth_keys)
# }}}
# {{{ Modify user
def modify_user(cmd):
    """
    Modify an existing user. Everything except ID and password.
    """
    exaconf = read_exaconf(cmd.exaconf)
    users = exaconf.get_users()
    if cmd.name != "_all" and cmd.name not in users:
        print("User '%s' does not exist in EXAConf '%s'!" % (cmd.name, cmd.exaconf))
        return 1
    user_conf = users[cmd.name]
    # change supported options
    if cmd.group:
        user_conf.group = cmd.group
    if cmd.login_enabled:
        user_conf.login_enabled = cmd.login_enabled
    if cmd.groups:
        user_conf.additional_groups = [ g.strip() for g in cmd.groups.split(",") if g.strip() != "" ]
    if cmd.auth_keys:
        user_conf.authorized_keys =  [ k.strip() for k in cmd.auth_keys.split(",") if k.strip() != "" ]
    exaconf.set_user_conf(user_conf, cmd.name, extend_groups=cmd.extend_groups, extend_keys=cmd.extend_keys)
# }}}
# {{{ Passwd user
def passwd_user(cmd):
    """
    Change user password.
    """
    exaconf = read_exaconf(cmd.exaconf)
    users = exaconf.get_users()
    if not cmd.name in users:
        print("User '%s' does not exist in EXAConf '%s'!" % (cmd.name, cmd.exaconf))
        return 1
    user_conf = users[cmd.name]

    user_conf.passwd = cmd.passwd
    encode_passwd = False if cmd.encode_passwd is None else True
    # prompt for password if not given and always encode it
    if user_conf.passwd is None:
        user_conf.passwd = getpass.getpass()
        encode_passwd = True
    exaconf.set_user_conf(user_conf, cmd.name, encode_passwd=encode_passwd)
# }}}
# {{{ Remove user
def remove_user(cmd):
    """
    Remove a user from EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.remove_user(cmd.name)
# }}}
# {{{ Add BucketFS
def add_bucketfs(cmd):
    """
    Add a BucketFS to EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    bfs_owner = tuple( int(o.strip()) for o in cmd.owner.split(":") if o.strip() != "" )
    exaconf.add_bucketfs(bucketfs_name = cmd.name, owner = bfs_owner,
                         http_port = cmd.http_port, https_port = cmd.https_port,
                         mode = cmd.mode,
                         sync_key = cmd.sync_key,
                         sync_period = cmd.sync_period,
                         bucketvolume = cmd.bucketvolume,
                         path = cmd.path)
# }}}
# {{{ Modify BucketFS
def modify_bucketfs(cmd):
    """
    Modify an existing BucketFS. Everything except sync_period and path.
    """
    exaconf = read_exaconf(cmd.exaconf)
    bucketfs = exaconf.get_bucketfs()
    if cmd.name != "_all" and cmd.name not in bucketfs:
        print("BucketFS '%s' does not exist in EXAConf '%s'!" % (cmd.name, cmd.exaconf))
        return 1
    bfs_conf = bucketfs[cmd.name]
    # change supported options
    if cmd.owner:
        bfs_owner = tuple( int(o.strip()) for o in cmd.owner.split(":") if o.strip() != "" )
        bfs_conf.owner = bfs_owner
    if cmd.http_port:
        bfs_conf.http_port = cmd.http_port
    if cmd.https_port:
        bfs_conf.https_port = cmd.https_port
    if cmd.sync_period:
        bfs_conf.sync_period = cmd.sync_period
    exaconf.set_bucketfs_conf(bfs_conf, cmd.name)
# }}}
# {{{ Remove BucketFS
def remove_bucketfs(cmd):
    """
    Remove a BucketFS from EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.remove_bucketfs(cmd.name)
# }}}
# {{{ Add Bucket
def add_bucket(cmd):
    """
    Add a Bucket to EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    additional_files = None
    if cmd.additional_files:
        additional_files = [ f.strip() for f in cmd.additional_files.split(",") if f.strip() != "" ]
    exaconf.add_bucket(bucket_name = cmd.name, bucketfs_name = cmd.bfs_name,
                       public = cmd.public,
                       read_password = cmd.read_passwd,
                       write_password = cmd.write_passwd,
                       additional_files = additional_files)
# }}}
# {{{ Modify Bucket
def modify_bucket(cmd):
    """
    Modify an existing Bucket in EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    additional_files = None
    if cmd.additional_files:
        additional_files = [ f.strip() for f in cmd.additional_files.split(",") if f.strip() != "" ]
    bucketfs = exaconf.get_bucketfs()
    if cmd.bfs_name not in bucketfs:
        print("BucketFS '%s' does not exist in EXAConf '%s'!" % (cmd.bfs_name, cmd.exaconf))
        return 1
    bfs_conf = bucketfs[cmd.bfs_name]
    if cmd.name != "_all" and cmd.name not in bfs_conf.buckets:
        print("Bucket '%s' does not exist in BucketFS '%s' in EXAConf '%s'!" % (cmd.bfs_name, cmd.name, cmd.exaconf))
        return 1
    b_conf = bfs_conf.buckets[cmd.name]
    # change supported options
    b_conf.public = cmd.public
    if cmd.read_passwd:
        b_conf.read_passwd = cmd.read_passwd
    if cmd.write_passwd:
        b_conf.write_passwd = cmd.write_passwd
    if additional_files:
        b_conf.additional_files = additional_files
    exaconf.set_bucket_conf(b_conf, cmd.name, cmd.bfs_name)
# }}}
# {{{ Remove Bucket
def remove_bucket(cmd):
    """
    Remove a Bucket from EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.remove_bucket(cmd.name, cmd.bfs_name)
# }}}
# {{{ Add Logging
def add_Logging(cmd):
    """
    Add a Logging to EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.add_Logging(LogRotationTypes = cmd.LogRotationTypes,
                        RemoteLogRotationVolume = cmd.RemoteLogRotationVolume,
                        RemoteLogRotationPrefix = cmd.RemoteLogRotationPrefix)
# }}}
# {{{ Add backup schedule
def add_backup_schedule(cmd):
    """
    Add a backup schedule to an existing database in EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.add_backup_schedule(db_name = cmd.db_name, backup_name = cmd.backup_name,
                          volume = cmd.volume, level = cmd.level, minute = cmd.minute,
                          hour = cmd.hour, day = cmd.day, month = cmd.month,
                          weekday = cmd.weekday, expire = cmd.expire, enabled = not cmd.disabled)
# }}}
# {{{ Remove backup schedule
def remove_backup_schedule(cmd):
    """
    Remove an existing backup schedule from a database in EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    exaconf.remove_backup_schedule(db_name = cmd.db_name, backup_name = cmd.backup_name)
# }}}
# {{{ Modify backup schedule
def modify_backup_schedule(cmd):
    """
    Modify an existing backup schedule in EXAConf.
    """
    exaconf = read_exaconf(cmd.exaconf)
    databases = exaconf.get_databases()
    if cmd.db_name not in databases:
        print("Database '%s' does not exist in EXAConf '%s'!" % (cmd.db_name, cmd.exaconf))
        return 1
    db_conf = databases[cmd.db_name]
    if 'backups' not in db_conf or (cmd.backup_name != "_all" and cmd.backup_name not in db_conf.backups):
        print("Backup schedule '%s' of DB '%s' does not exist in EXAConf '%s'!" % (cmd.backup_name, cmd.db_name, cmd.exaconf))
        return 1
    ba_conf = db_conf.backups[cmd.backup_name]
    # change supported options
    if cmd.minute:
        ba_conf.minute = cmd.minute
    if cmd.hour:
        ba_conf.hour = cmd.hour
    if cmd.day:
        ba_conf.day = cmd.day
    if cmd.month:
        ba_conf.month = cmd.month
    if cmd.weekday:
        ba_conf.weekday = cmd.weekday
    if cmd.disabled:
        ba_conf.enabled = not cmd.disabled
    exaconf.set_backup_schedule_conf(ba_conf, cmd.db_name, cmd.backup_name)
# }}}
# {{{ Commit
def commit(cmd):
    """
    Commits the current state of the local node to all online nodes in the cluster
    by copying the given EXAConf to all nodes and using 'exalocalconf' to commit
    the EXAConf on each node. This command only works within a cluster (i. e.
    it must be executed on an Exasol cluster node / container).
    """
    try:
        from pipes import quote as shquote
        from libconfd import file_sync
        import exacos
    except ImportError:
        print("Can't load required modules. This command only works within an Exasol cluster.")
        return 1

    command = 'exalocalconf --commit-local -c %s 2>&1' % shquote(cmd.exaconf)

    print("=== Step 1: synchronizing '%s' ===" % shquote(cmd.exaconf))
    exaconf = read_exaconf(cmd.exaconf)
    sync = file_sync.file_sync(log_stderr=True, verbose=cmd.verbose)
    sync.sync_file('%s' % shquote(cmd.exaconf))
    print("--> Successful!")

    print("=== Step 2: executing '%s' ===" % command)
    ph = exacos.process_handler()
    part, error = ph.startPartitionControlled({'all_nodes': True, 'params': ['/bin/sh', '-c', command]})
    if part <= 0:
        print("Failed to create 'exaconf' partition: %s. " % error)
        return 1
    ret, exit_status = ph.waitPartitionExitStatus(part)
    if ret != 0:
        print("'waitPartitionExitStatus()' returned with %i. " % ret)
        ph.release(part)
        return 1
    if exit_status == 0:
        print("--> Successful!")
    else:
        print("--> ERROR:")
        print(ph.read(part))
        ph.release(part)
        return 1
    ph.release(part)

    print("=== Step 3: creating status file ===")
    rev = str(exaconf.get_revision())
    ts = time.strftime("%d.%m.%Y - %H:%M:%S")
    sync.write_to_nodes(("'%s' : revision %s : %s" % (cmd.exaconf, rev, ts)).encode('utf-8'),
                        "/exa/etc/EXAConf.commited.manually")
    print("--> Successful!")
# }}}
# {{{ Main
def main():

    parser = argparse.ArgumentParser(
            description = 'Command line tool for modifying EXAConf.',
            prog = 'exaconf')
    cmdparser = parser.add_subparsers(
            dest = 'command',
            title = 'commands',
            description = 'supported commands')
    cmdparser.required = True


    # version command
    parser_ver = cmdparser.add_parser(
            'version',
            help='Print the exaconf CLI tool version number')
    parser_ver.set_defaults(func=print_version)

    # add node command
    parser_an = cmdparser.add_parser(
            'add-node',
            help='Add a new node to EXAConf.')
    parser_an.add_argument(
            'exaconf',
            type=str,
            metavar='EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help='The EXAConf file.')
    parser_an.add_argument(
            '--priv-net', '-p',
            type=str,
            required = True,
            help="Private network (e.g. '10.10.10.12/24'). Characters 'x' and 'X' in the IP are replaced with the node ID.")
    parser_an.add_argument(
            '--pub-net', '-P',
            type=str,
            required = False,
            help="Public network (e.g. '10.10.0.12/24'). Characters 'x' and 'X' in the IP are replaced with the node ID.")
    parser_an.add_argument(
            '--node-id', '-n',
            type = int,
            required = False,
            help='ID for the new node (automatically selected if omitted).')
    parser_an.set_defaults(func=add_node)

    # modify node command
    parser_mn = cmdparser.add_parser(
            'modify-node',
            help='Modify an existing node in EXAConf.')
    parser_mn.add_argument(
            'exaconf',
            type=str,
            metavar='EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help='The EXAConf file.')
    parser_mn.add_argument(
            '--node-id', '-n',
            type = str,
            required = True,
            help='ID of the node.')
    parser_mn.add_argument(
            '--priv-net', '-p',
            type=str,
            help="Private network (e.g. '10.10.10.12/24'). Characters 'x' and 'X' in the IP are replaced with the node ID.")
    parser_mn.add_argument(
            '--pub-net', '-P',
            type=str,
            help="Public network (e.g. '10.10.0.12/24'). Characters 'x' and 'X' in the IP are replaced with the node ID.")
    parser_mn.add_argument(
            '--priv-ip', '-i',
            type=str,
            help="Private IP address (e.g. '10.10.10.12'). The netmask is not modified.")
    parser_mn.add_argument(
            '--pub-ip', '-I',
            type=str,
            help="Public IP address (e.g. '10.10.0.12'). The netmask is not modified.")
    parser_mn.set_defaults(func=modify_node)

    # remove node command
    parser_rn = cmdparser.add_parser(
            'remove-node',
            help='Remove a node from EXAConf.')
    parser_rn.add_argument(
            'exaconf',
            type=str,
            metavar='EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help='The EXAConf file.')
    parser_rn.add_argument(
            '--node-id', '-n',
            type = str,
            required = True,
            help='ID of the node that should be removed.')
    parser_rn.add_argument(
            '--force', '-f',
            action='store_true',
            default=False,
            help="Remove node even if it's in use by a volume or database.")
    parser_rn.set_defaults(func=remove_node)

    # add node device command
    parser_and = cmdparser.add_parser(
            'add-node-device',
            help='Add a device to an existing node in EXAConf.')
    parser_and.add_argument(
            'exaconf',
            type=str,
            metavar='EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help='The EXAConf file.')
    parser_and.add_argument(
            '--node-id', '-n',
            type = int,
            required = True,
            help='Node ID')
    parser_and.add_argument(
            '--disk', '-D',
            type=str,
            required = True,
            help="The disk that should contain the given device. Created if it does not exist.")
    parser_and.add_argument(
            '--device', '-d',
            type=str,
            required = True,
            help="Device name (only the basename of the device file).")
    parser_and.add_argument(
            '--path', '-p',
            type=str,
            required = False,
            help="Absolute path to the directory that contains the device file (if it's not in '/exa/data/storage/').")
    parser_and.set_defaults(func=add_node_device)

    # remove node device command
    parser_rnd = cmdparser.add_parser(
            'remove-node-device',
            help='Remove a device from an existing node in EXAConf. The disk is also removed if it does not contain other devices.')
    parser_rnd.add_argument(
            'exaconf',
            type=str,
            metavar='EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help='The EXAConf file')
    parser_rnd.add_argument(
            '--node-id', '-n',
            type = int,
            required = True,
            help='Node ID')
    parser_rnd.add_argument(
            '--disk', '-D',
            type=str,
            required = True,
            help="The disk that contains the given device")
    parser_rnd.add_argument(
            '--device', '-d',
            type=str,
            required = True,
            help="Device name (only the basename of the device file)")
    parser_rnd.set_defaults(func=remove_node_device)

    # add node disk command
    parser_andi = cmdparser.add_parser(
            'add-node-disk',
            help="Add an disk to an existing node in EXAConf. Use 'add-node-device' to add a disk with devices.")
    parser_andi.add_argument(
            'exaconf',
            type=str,
            metavar='EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help='The EXAConf file')
    parser_andi.add_argument(
            '--node-id', '-n',
            type = int,
            required = True,
            help='Node ID')
    parser_andi.add_argument(
            '--disk', '-D',
            type=str,
            required = True,
            help="Name of the disk that should be added.")
    parser_andi.add_argument(
            '--component', '-c',
            type=str,
            required = False,
            help="The component that should use this disk (default: 'exastorage').")
    parser_andi.add_argument(
            '--devices', '-d',
            type=str,
            required = False,
            help="Comma-separated list of device names.")
    parser_andi.add_argument(
            '--drives', '-r',
            type=str,
            required = False,
            help="Comma-separated list of drive IDs.")
    parser_andi.add_argument(
            '--overwrite-existing', '-O',
            action='store_true',
            required = False,
            help="Overwrite any existing disk with the same name.")
    parser_andi.set_defaults(func=add_node_disk)

    # remove node disk command
    parser_rndi = cmdparser.add_parser(
            'remove-node-disk',
            help='Remove a disk (or all disks) from an existing node in EXAConf.')
    parser_rndi.add_argument(
            'exaconf',
            type=str,
            metavar='EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help='The EXAConf file')
    parser_rndi.add_argument(
            '--node-id', '-n',
            type = int,
            required = True,
            help='Node ID')
    parser_rndi.add_argument(
            '--disk', '-D',
            type=str,
            required = True,
            help="The disk that should be removed ('all' for all disks).")
    parser_rndi.set_defaults(func=remove_node_disk)

    # add volume command
    parser_av = cmdparser.add_parser(
            'add-volume',
            help='Add a new volume to EXAConf.')
    parser_av.add_argument(
            'exaconf',
            type=str,
            metavar='EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help='The EXAConf file')
    parser_av.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help='Name of the new volume (must be unqiue in the cluster).')
    parser_av.add_argument(
            '--type', '-t',
            type=str,
            required = True,
            help="Type of the new volume ('data', 'archive' or 'remote').")
    parser_av.add_argument(
            '--size', '-s',
            type=str,
            required = True,
            help="Size of the new volume, e.g. 1TiB, 20GiB, 20000000B, etc..")
    parser_av.add_argument(
            '--disk', '-d',
            type=str,
            required = True,
            help="Disk to be used for the new volume.")
    parser_av.add_argument(
            '--redundancy', '-r',
            type=str,
            required = True,
            help="Redundancy of the new volume.")
    parser_av.add_argument(
            '--nodes', '-N',
            type=str,
            required = True,
            help="Comma-separated list of node IDs, e.g. '11,12,13'.")
    parser_av.add_argument(
            '--num-master-nodes', '-m',
            type=int,
            help="Number of master nodes (default: same as nr. of nodes in '--nodes').")
    parser_av.add_argument(
            '--owner', '-o',
            type=str,
            required=True,
            help="User and group ID, e. g. '1000:10001'.")
    parser_av.add_argument(
            '--perm', '-p',
            type=str,
            help="Permissions for the new volume, e. g. 'rw-r--r--' (default: 'rwx------').")
    parser_av.add_argument(
            '--labels', '-l',
            type=str,
            help="Comma separated list of labels, e. g. 'best,volume,ever' (default: None).")
    parser_av.add_argument(
            '--block-size', '-B',
            type=int,
            help="Block-size in bytes (default: 4096).")
    parser_av.add_argument(
            '--stripe-size', '-S',
            type=int,
            help="Stripe-size in bytes (default: 262144).")
    parser_av.set_defaults(func=add_volume)

    # modify volume command
    parser_mv = cmdparser.add_parser(
            'modify-volume',
            help='Modify an existing volume in EXAConf.')
    parser_mv.add_argument(
            'exaconf',
            type=str,
            metavar='EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help='The EXAConf file')
    parser_mv.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help='Name of the volume.')
    parser_mv.add_argument(
            '--owner', '-o',
            type = str,
            required = False,
        help="User and group ID of the volume (e. g. '1000:1001').")
    parser_mv.add_argument(
            '--size', '-s',
            type=str,
            help="Size of the volume, e.g. 1TiB, 20GiB, 20000000B, etc..")
    parser_mv.add_argument(
            '--disk', '-d',
            type=str,
            help="Disk to be used for the volume.")
    parser_mv.add_argument(
            '--nodes', '-N',
            type=str,
            help="Comma-separated list of node IDs, e.g. '11,12,13'.")
    parser_mv.add_argument(
            '--num-master-nodes', '-m',
            type=int,
            help="Number of master nodes (default: same as nr. of nodes in '--nodes').")
    parser_mv.add_argument(
            '--redundancy', '-r',
            type=int,
            help='Redundancy value.')
    parser_mv.add_argument(
            '--labels', '-l',
            type=str,
            help="Comma separated list of labels, e. g. 'best,volume,ever' (default: None).")
    parser_mv.set_defaults(func=modify_volume)

    # add remote volume command
    parser_arv = cmdparser.add_parser(
            'add-remote-volume',
            help='Add a new volume to EXAConf.')
    parser_arv.add_argument(
            'exaconf',
            type=str,
            metavar='EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help='The EXAConf file')
    parser_arv.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help='Name of the remote volume (must be unqiue in the cluster, generated autom. if omitted)')
    parser_arv.add_argument(
            '--type', '-t',
            type=str,
            required = True,
            help="Type of the volume ('smb', 'ftp' or 's3').")
    parser_arv.add_argument(
            '--owner', '-o',
            type = str,
            required = False,
        help="User and group ID of the remote volume (e. g. '1000:1001').")
    parser_arv.add_argument(
            '--id', '-i',
            type=int,
            help="ID of the volume (automatically generated if omitted).")
    parser_arv.add_argument(
            '--url', '-u',
            type=str,
            required = True,
            help="URL for the remote volume, e.g. 'smb://my.remote.server/share (default: None).")
    parser_arv.add_argument(
            '--username', '-U',
            type=str,
            help="Username for accessing the remote volume' (default: None).")
    parser_arv.add_argument(
            '--passwd', '-P',
            type=str,
            help="Password for accessing the remote volume' (default: None).")
    parser_arv.add_argument(
            '--options', '-f',
            type=str,
            help="Additional options for the remote volume' (default: None).")
    parser_arv.add_argument(
            '--labels', '-l',
            type=str,
            help="Comma separated list of labels, e. g. 'best,volume,ever' (default: None).")
    parser_arv.set_defaults(func=add_remote_volume)

    # remove volume command
    parser_rv = cmdparser.add_parser(
            'remove-volume',
            help = 'Remove a volume from EXAConf.')
    parser_rv.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_rv.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help='Name of the volume to be removed')
    parser_rv.add_argument(
            '--force', '-f',
            action='store_true',
            default=False,
            help="remove volume even if it's in use by a database")
    parser_rv.set_defaults(func=remove_volume)

    # remove remote volume command
    parser_rrv = cmdparser.add_parser(
            'remove-remote-volume',
            help = 'Remove a remote volume from EXAConf.')
    parser_rrv.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_rrv.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help='Name of the remote volume to be removed')
    parser_rrv.add_argument(
            '--force', '-f',
            action='store_true',
            default=False,
            help="remove remote volume even if it's in use by a database")
    parser_rrv.set_defaults(func=remove_remote_volume)

    # set storage conf command
    parser_ssc = cmdparser.add_parser(
            'set-storage-conf',
            help = 'Set configurable EXAStorage parameters.')
    parser_ssc.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_ssc.add_argument(
            '--bg-rec-enabled', '-r',
            type = str2bool,
            help='Enable or disable background recovery / data restoration (does not affect on-demand recovery)')
    parser_ssc.add_argument(
            '--bg-rec-limit', '-l',
            type = int,
            help="Max. throughput for the background recovery / data restoration (in MiB/s)")
    parser_ssc.add_argument(
            '--space-warn-threshold', '-t',
            type = int,
            help="Space usage threshold (in percent, per node) for sending a warning")
    parser_ssc.set_defaults(func=set_storage_conf)

    # encode-shadow command
    parser_enc = cmdparser.add_parser(
            'encode-shadow',
            help = 'Encode given password into an /etc/shadow compatible SHA512 hash. Prompt for the password if none is given.')
    parser_enc.add_argument(
            '--passwd', '-p',
            type = str,
            required = False,
            help = "The password that should be encoded.")
    parser_enc.set_defaults(func=encode_shadow_passwd)

    # encode-shadow command
    parser_encdb = cmdparser.add_parser(
            'encode-db',
            help = 'Encode given password into an DB compatible hash. Prompt for the password if none is given.')
    parser_encdb.add_argument(
            '--passwd', '-p',
            type = str,
            required = False,
            help = "The password that should be encoded.")
    parser_encdb.set_defaults(func=encode_db_passwd)

    # reoder database affinities
    parser_dbra = cmdparser.add_parser(
            'db-reorder',
            help = 'Reorder database node affinities to match volume information.')
    parser_dbra.add_argument(
            '--db-name', '-n',
            type = str,
            required = True,
            help = "Name of the database.")
    parser_dbra.set_defaults(func=reorder_database_affinities)

    # list-groups command
    parser_lsg = cmdparser.add_parser(
            'list-groups',
            help = 'Print all groups in EXAConf.')
    parser_lsg.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_lsg.set_defaults(func=list_groups)

    # add-group command
    parser_adg = cmdparser.add_parser(
            'add-group',
            help = 'Add a group to EXAConf.')
    parser_adg.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_adg.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The group name.")
    parser_adg.add_argument(
            '--id', '-i',
            type = int,
            required = True,
            help = "The group ID.")
    parser_adg.set_defaults(func=add_group)

    # remove-group command
    parser_rmg = cmdparser.add_parser(
            'remove-group',
            help = 'Remove a group from EXAConf.')
    parser_rmg.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_rmg.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The group name.")
    parser_rmg.set_defaults(func=remove_group)

    # list-users command
    parser_lsu = cmdparser.add_parser(
            'list-users',
            help = 'Print all users in EXAConf.')
    parser_lsu.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_lsu.set_defaults(func=list_users)

    # add-user command
    parser_adu = cmdparser.add_parser(
            'add-user',
            help = 'Add a user to EXAConf.')
    parser_adu.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_adu.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The user name.")
    parser_adu.add_argument(
            '--id', '-i',
            type = int,
            required = True,
            help = "The user ID.")
    parser_adu.add_argument(
            '--group', '-g',
            type = str,
            required = True,
            help = "The user's main group (name or ID).")
    parser_adu.add_argument(
            '--login-enabled', '-l',
            action='store_true',
            required = False,
            help = "Enable SSH login for this user (also gives him a valid shell).")
    parser_adu.add_argument(
            '--passwd', '-p',
            type = str,
            required = False,
            help = "The user's password (encoded ot cleartext).")
    parser_adu.add_argument(
            '--prompt-passwd', '-P',
            action='store_true',
            required = False,
            help = "Prompt for user password (will always be encoded).")
    parser_adu.add_argument(
            '--encode-passwd', '-e',
            action='store_true',
            required = False,
            help = "Encode the given password as a SHA512 hash value (always True if prompted for password).\
If not specified, the password will automatically be encoded if it's not in an /etc/shadow compatible format.")
    parser_adu.add_argument(
            '--groups', '-G',
            type=str,
            required = False,
            help="Comma-separated list of additional groups (names).")
    parser_adu.add_argument(
            '--auth-keys', '-k',
            type=str,
            required = False,
            help="Comma-separated list of public SSH keys.")
    parser_adu.add_argument(
            '--auth-keys-file', '-K',
            type=str,
            required = False,
            help="File containing public SSH keys (one per line).")
    parser_adu.set_defaults(func=add_user)

    # modify-user command
    parser_mdu = cmdparser.add_parser(
            'modify-user',
        help = 'Modify an existing user in EXAConf (everything except uid and password).')
    parser_mdu.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_mdu.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The user name.")
    parser_mdu.add_argument(
            '--group', '-g',
            type = str,
            help = "The user's main group (name).")
    parser_mdu.add_argument(
            '--login-enabled', '-l',
            action='store_true',
            required = False,
            help = "Enable SSH login for this user (also gives him a valid shell).")
    parser_mdu.add_argument(
            '--groups', '-G',
            type=str,
            required = False,
            help="Comma-separated list of additional groups (names).")
    parser_mdu.add_argument(
            '--auth-keys', '-K',
            type=str,
            required = False,
            help="Comma-separated list of public SSH keys.")
    parser_mdu.add_argument(
            '--extend-groups', '-a',
            action='store_true',
            required = False,
            help = "Extend the existing groups with the given ones.")
    parser_mdu.add_argument(
            '--extend-keys', '-A',
            action='store_true',
            required = False,
            help = "Extend the existing keys with the given ones.")
    parser_mdu.set_defaults(func=modify_user)

    # passwd-user command
    parser_pwu = cmdparser.add_parser(
            'passwd-user',
        help = 'Set new password for given user.')
    parser_pwu.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_pwu.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The user name.")
    parser_pwu.add_argument(
            '--passwd', '-p',
            type = str,
            required = False,
            help = "The user's password. Will prompt for password if omitted.")
    parser_pwu.add_argument(
            '--encode-passwd', '-e',
            action='store_true',
            required = False,
            help = "Encode the given cleartext password as a SHA512 hash value (always True if prompted for password).")
    parser_pwu.set_defaults(func=passwd_user)

    # remove-user command
    parser_rmu = cmdparser.add_parser(
            'remove-user',
            help = 'Remove a user from EXAConf.')
    parser_rmu.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_rmu.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The user name.")
    parser_rmu.set_defaults(func=remove_user)

    # add-bucketfs command
    parser_abfs = cmdparser.add_parser(
            'add-bucketfs',
            help = 'Add a BucketFS to EXAConf.')
    parser_abfs.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_abfs.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The BucketFS name.")
    parser_abfs.add_argument(
            '--owner', '-o',
            type = str,
            required = True,
            help = "User and group ID of the BucketFS (e. g. '1000 :1001').")
    parser_abfs.add_argument(
            '--http-port', '-p',
            type = int,
            required = True,
            help = "The HTTP port number (0 = disabled).")
    parser_abfs.add_argument(
            '--https-port', '-P',
            type = int,
            required = True,
            help = "The HTTPS port number (0 = disabled).")
    parser_abfs.add_argument(
            '--sync-period', '-s',
            type = int,
            required = False,
            help = "The synchronization interval (default value is used if omitted).")
    parser_abfs.add_argument(
            '--sync-key', '-S',
            type = int,
            required = False,
            help = "The synchronization key (generated if omitted).")
    parser_abfs.add_argument(
            '--path', '-A',
            type = int,
            required = False,
            help = "A on-default path for this bucket.")
    parser_abfs.set_defaults(func=add_bucketfs)

    # modify-bucketfs command
    parser_mbfs = cmdparser.add_parser(
            'modify-bucketfs',
            help = 'Modify an existing BucketFS in EXAConf.')
    parser_mbfs.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_mbfs.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The BucketFS name.")
    parser_mbfs.add_argument(
            '--owner', '-o',
            type = str,
            required = False,
            help = "User and group ID of the BucketFS (e. g. '1000 :1001').")
    parser_mbfs.add_argument(
            '--http-port', '-p',
            type = int,
            required = False,
            help = "The HTTP port number (0 = disabled).")
    parser_mbfs.add_argument(
            '--https-port', '-P',
            type = int,
            required = False,
            help = "The HTTPS port number (0 = disabled).")
    parser_mbfs.add_argument(
            '--sync-period', '-s',
            type = int,
            required = False,
            help = "The synchronization interval (default value is used if omitted).")
    parser_mbfs.set_defaults(func=modify_bucketfs)

    # remove-bucketfs command
    parser_rbfs = cmdparser.add_parser(
            'remove-bucketfs',
            help = 'Remove an existing BucketFS in EXAConf.')
    parser_rbfs.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_rbfs.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The BucketFS name.")
    parser_rbfs.set_defaults(func=remove_bucketfs)

    # add-bucket command
    parser_adb = cmdparser.add_parser(
            'add-bucket',
            help = 'Add a Bucket to EXAConf.')
    parser_adb.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_adb.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The Bucket name.")
    parser_adb.add_argument(
            '--bfs-name', '-N',
            type = str,
            required = True,
            help = "The BucketFS name.")
    parser_adb.add_argument(
            '--public', '-p',
            action = "store_true",
            required = False,
            help = "Make the Bucket public (i. e. readable by everybody).")
    parser_adb.add_argument(
            '--read-passwd', '-r',
            type = str,
            required = False,
            help = "An optional password for read access (default: auto-generated).")
    parser_adb.add_argument(
            '--write-passwd', '-w',
            type = str,
            required = False,
            help = "An optional password for write access (default: auto-generated).")
    parser_adb.add_argument(
            '--additional-files', '-a',
            type = str,
            required = False,
            help = "An optional comma-separated list of additional files for this Bucket.")
    parser_adb.set_defaults(func=add_bucket)

    # modify-bucket command
    parser_mob = cmdparser.add_parser(
            'modify-bucket',
            help = 'Modify an existing Bucket in EXAConf.')
    parser_mob.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_mob.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The Bucket name.")
    parser_mob.add_argument(
            '--bfs-name', '-N',
            type = str,
            required = True,
            help = "The BucketFS name.")
    parser_mob.add_argument(
            '--public', '-p',
            action = "store_true",
            required = False,
            help = "Make the Bucket public (i. e. readable by everybody).")
    parser_mob.add_argument(
            '--read-passwd', '-r',
            type = str,
            required = False,
            help = "An optional password for read access (default: auto-generated).")
    parser_mob.add_argument(
            '--write-passwd', '-w',
            type = str,
            required = False,
            help = "An optional password for write access (default: auto-generated).")
    parser_mob.add_argument(
            '--additional-files', '-a',
            type = str,
            required = False,
            help = "An optional comma-separated list of additional files for this Bucket.")
    parser_mob.set_defaults(func=modify_bucket)

    # remove-bucket command
    parser_rmb = cmdparser.add_parser(
            'remove-bucket',
            help = 'Remove an existing Bucket from EXAConf.')
    parser_rmb.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_rmb.add_argument(
            '--name', '-n',
            type = str,
            required = True,
            help = "The Bucket name.")
    parser_rmb.add_argument(
            '--bfs-name', '-N',
            type = str,
            required = True,
            help = "The BucketFS name.")
    parser_rmb.set_defaults(func=remove_bucket)

    # add-backup-schedule command
    parser_abup = cmdparser.add_parser(
            'add-backup-schedule',
            help = 'Add a backup schedule to an existing database in EXAConf.')
    parser_abup.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_abup.add_argument(
            '--db-name', '-n',
            type = str,
            required = True,
            help = "The DB name.")
    parser_abup.add_argument(
            '--backup-name', '-N',
            type = str,
            required = True,
            help = "The backup schedule name.")
    parser_abup.add_argument(
            '--disabled', '-D',
            action = "store_true",
            required = False,
            help = "Disable the backup schedule (default: enabled).")
    parser_abup.add_argument(
            '--volume', '-v',
            type = str,
            required = True,
            help = "Name of the destination volume.")
    parser_abup.add_argument(
            '--level', '-l',
            type = int,
            required = True,
            help = "Backup level.")
    parser_abup.add_argument(
            '--expire', '-e',
            type = str,
            default = '0',
            required = False,
            help = "Expire time, e. g. '1w 2d 5m 10s' (default: never expire).")
    parser_abup.add_argument(
            '--minute', '-m',
            type = str,
            default = '*',
            help = "'Minute' value for cron job (default: '*').")
    parser_abup.add_argument(
            '--hour', '-H',
            type = str,
            default = '*',
            help = "'Hour' value for cron job (default: '*').")
    parser_abup.add_argument(
            '--day', '-d',
            type = str,
            default = '*',
            help = "'Day' value for cron job (default: '*').")
    parser_abup.add_argument(
            '--month', '-M',
            type = str,
            default = '*',
            help = "'Month' value for cron job (default: '*').")
    parser_abup.add_argument(
            '--weekday', '-w',
            type = str,
            default = '*',
            help = "'Weekday' value for cron job (default: '*').")
    parser_abup.set_defaults(func=add_backup_schedule)

    # modify-backup-schedule command
    parser_mbup = cmdparser.add_parser(
            'modify-backup-schedule',
            help = 'Modify an existing backup schedule in EXAConf.')
    parser_mbup.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_mbup.add_argument(
            '--db-name', '-n',
            type = str,
            required = True,
            help = "The DB name.")
    parser_mbup.add_argument(
            '--backup-name', '-N',
            type = str,
            required = True,
            help = "The backup schedule name.")
    parser_mbup.add_argument(
            '--disabled', '-D',
            action = "store_true",
            required = False,
            help = "Disable the backup schedule (default: enabled).")
    parser_mbup.add_argument(
            '--minute', '-m',
            type = str,
            default = '*',
            help = "'Minute' value for cron job (default: '*').")
    parser_mbup.add_argument(
            '--hour', '-H',
            type = str,
            default = '*',
            help = "'Hour' value for cron job (default: '*').")
    parser_mbup.add_argument(
            '--day', '-d',
            type = str,
            default = '*',
            help = "'Day' value for cron job (default: '*').")
    parser_mbup.add_argument(
            '--month', '-M',
            type = str,
            default = '*',
            help = "'Month' value for cron job (default: '*').")
    parser_mbup.add_argument(
            '--weekday', '-w',
            type = str,
            default = '*',
            help = "'Weekday' value for cron job (default: '*').")
    parser_mbup.set_defaults(func=modify_backup_schedule)

    # remove-backup-schedule command
    parser_rbup = cmdparser.add_parser(
            'remove-backup-schedule',
            help = 'Remove an existing backup schedule from a database in EXAConf.')
    parser_rbup.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_rbup.add_argument(
            '--db-name', '-n',
            type = str,
            required = True,
            help = "The DB name.")
    parser_rbup.add_argument(
            '--backup-name', '-N',
            type = str,
            required = True,
            help = "The backup schedule name.")
    parser_rbup.set_defaults(func=remove_backup_schedule)

    # commit command
    parser_c = cmdparser.add_parser(
            'commit',
            help = """Commit local '/exa/etc/EXAConf' to all online cluster nodes,
by changing local configurations according to EXAConf. This works for the following components:
        - users and groups
        - remote volumes
        - bucketfs and buckets
        - name resolution
        - cron jobs
        - syslog
        """)
    parser_c.add_argument(
            'exaconf',
            type = str,
            metavar = 'EXACONF',
            default = '/exa/etc/EXAConf', nargs='?',
            help = 'The EXAConf file')
    parser_c.add_argument(
            '--verbose', '-v',
            action='store_true',
            default = False,
            help = "Enable verbose output.")
    parser_c.set_defaults(func=commit)

    command = parser.parse_args()
    log_data = command.__dict__.copy()
    for key_name in ('passwd', 'read_passwd', 'write_passwd'):
        if key_name in log_data: del log_data[key_name]
    try:
        log_data['result'] = command.func(command)
    except Exception as err:
        log_data['error'] = err
    finally:
        if 'func' in log_data:
            log_data['func'] = log_data['func'].__name__
            if 'result' in log_data and log_data['result'] is None:
                del log_data['result']
            if 'error' in log_data:
                log.error(repr(log_data))
                return 1
            else:
                log.info(repr(log_data))
                return 0

# }}}

if __name__ == '__main__':
    sys.exit(main())
