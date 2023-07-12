# -*- mode: python -*-
# File layout controlled by Emacs folding.el available at:
# https://github.com/jaalto/project-emacs--folding-mode.

import sys, os, stat, ipaddr, configobj, hashlib, re, io, json, logging
import base64, secrets, string
import math, abc
import functools
from urllib.parse import urlparse
from collections import OrderedDict as odict
from pathlib import PurePath
from typing import Any, Dict, Pattern, Tuple, Type, TypeVar, Union, Optional, TYPE_CHECKING, List
# mypy has problems with try imports: https://github.com/python/mypy/issues/1153
if TYPE_CHECKING:
    from libconfd.common.util import atomic_file_writer, units2bytes, bytes2units, gen_bfs_passwd, encode_bfs_passwd, encode_remote_vol_passwd, decode_remote_vol_passwd, get_euid, get_egid, gen_node_uuid, encode_shadow_passwd, is_shadow_encoded, str2sec, sec2str, gen_b64_uuid, b64_to_uuid, is_valid_b64_uuid
    units2bytes, bytes2units, gen_bfs_passwd, encode_bfs_passwd, encode_remote_vol_passwd, decode_remote_vol_passwd, get_euid, get_egid, gen_node_uuid, encode_shadow_passwd, is_shadow_encoded, str2sec, sec2str, gen_b64_uuid, b64_to_uuid, is_valid_b64_uuid #silence pyflakes
else:
    try:
        from libconfd.common.util import atomic_file_writer, units2bytes, bytes2units, gen_bfs_passwd, encode_bfs_passwd, encode_remote_vol_passwd, decode_remote_vol_passwd, get_euid, get_egid, gen_node_uuid, encode_shadow_passwd, is_shadow_encoded, str2sec, sec2str, gen_b64_uuid, b64_to_uuid, is_valid_b64_uuid
        units2bytes, bytes2units, gen_bfs_passwd, encode_bfs_passwd, encode_remote_vol_passwd, decode_remote_vol_passwd, get_euid, get_egid, gen_node_uuid, encode_shadow_passwd, is_shadow_encoded, str2sec, sec2str, gen_b64_uuid, b64_to_uuid, is_valid_b64_uuid #silence pyflakes
    except ImportError:
        from libexadt.util import atomic_file_writer, units2bytes, bytes2units, gen_bfs_passwd, encode_bfs_passwd, get_euid, get_egid, gen_node_uuid, encode_shadow_passwd, is_shadow_encoded, str2sec, sec2str, gen_b64_uuid, b64_to_uuid, is_valid_b64_uuid

import exacos_constants

# {{{ Class EXAConfError

class EXAConfError(Exception):
    """
    The EXAConf exception class.
    """
    msg: str
    def __init__(self, msg) -> None:
        self.msg = f"EXAConf: {msg}"
    def __str__(self) -> str:
        return self.msg
    def __repr__(self) -> str:
        return "%s: %s" % (self.__class__.__name__, self.msg)

# }}}
# {{{ Class EXAConfIntegrityError

class EXAConfIntegrityError(EXAConfError):
    """
    Special exception raised if the integrity check fails.
    """
    def __init__(self, msg) -> None:
        self.msg = "EXAConf: " + msg
    def __str__(self) -> str:
        return repr(self.msg)

# }}}
# {{{ Class config

class config(odict):
    __enabled = False
    def __init__(self, *args, **kw) -> None:
        odict.__init__(self, *args, **kw)
        self.__enabled = True
    def __repr__(self) -> str:
        return "<%s at %s: %s>" % (self.__class__.__name__, hex(id(self)), repr(list(self.items())))
    def __getattr__(self, name):
        if not self.__enabled:
            return self.__getattribute__(name)
        return self.__getitem__(name)
    def __setattr__(self, name, value) -> None:
        if not self.__enabled:
            self.__dict__[name] = value
        else: self.__setitem__(name, value)
    def __delattr__(self, name) -> None:
        if not self.__enabled:
            del self.__dict__[name]
        else: self.__delitem__(name)

    def __getstate__(self):
        return self.__dict__.copy()

    def __setstate__(self, state) -> None:
        self.__dict__.update(state)

    def to_section(self, ignore_keys = []):
        """
        Returns a dictionary with all members converted so it can be assigned
        to a ConfigObj section.
        """
        special_keys = {'id': 'ID',
                        'uuid': 'UUID',
                        'jdbc': 'JDBC',
                        'direct_io': 'DirectIO',
                        'ssh_port': 'SSHPort',
                        'bucketfs': 'BucketFS',
                        'additional_urls': 'AdditionalURLs'}
        def to_camelcase(key):
            """
            Converts the given key to camelcase (or a special, pre-defined, case).
            """
            if key in special_keys:
                return special_keys[key]
            return ''.join(x.capitalize() or '_' for x in key.split('_'))
        def to_str(value):
            """
            Converts the given value to a string suitable for ConfigObj.
            """
            if isinstance(value, list):
                return ", ".join(to_str(e) for e in value)
            elif isinstance(value, tuple):
                return " : ".join(to_str(e) for e in value)
            else:
                return str(value)

        section = { to_camelcase(k):v for (k,v) in self.items() if not k.startswith('_') and k not in ignore_keys }
        for k,v in section.items():
            if isinstance(v, type(self)):
                section[k] = v.to_section()
            else:
                section[k] = to_str(v)
        return section

# }}}
# {{{ Class EXAVersion

@functools.total_ordering
class EXAVersion:
    """Abstracts over supported Exasol version formats and allows comparing them using '<'

    Supported versions look like:
        '(num)[-_.](suffix)(num)?(-d(num))?'

    where 'num' can be a dot separated string of numbers and suffix is one of
    'dev', 'alpha', 'beta', 'rc', '', 'p' (in ascending order).
    For historical reasons, the separator before the 'p' suffix can be omitted.

    Examples:
        6.1.1 6.2.rc1 7.1.0-beta3 8.0.0_dev 9.1.2-d5 10.0.0-alpha2-d1

    For testing purposes, any string can be appended instead of a real suffix.
    This will sort higher than any version with the same prefix.
    e.g. 6.2.999_p1 < 6.2.foobar
    """

    dotted_version = r"[0-9]+(?:\.[0-9]+)*"
    maybe_dotted_version = f"(?:{dotted_version})?"
    suffixes = ("dev", "alpha", "beta", "rc", "", "p")
    suffix_separator = r"[-_.]"
    garbage_suffix = f"{suffix_separator}.+?"
    buildid_suffix = f"-d({dotted_version})"
    # need a helper function to use generator expression at class scope
    @staticmethod
    def _setup_suffix_regex(sfxs, sfx_sep, maybe_ver) -> str:
        def _get_suffix_regex(sfx):
            if sfx == "":
                return "()()"
            elif sfx == "p":
                return f"{sfx_sep}?({sfx})({maybe_ver})"
            else:
                return f"{sfx_sep}({sfx})({maybe_ver})"

        return "|".join(_get_suffix_regex(sfx) for sfx in reversed(sfxs))

    # work around mypy not knowing that we never call this with an instance
    # also work around it not correctly infering the staticmethod
    # https://github.com/python/mypy/issues/3482
    suffix_regex = _setup_suffix_regex.__func__( # type: ignore[attr-defined]
        suffixes, suffix_separator, maybe_dotted_version
    )
    version_regex = re.compile(
        f"^({dotted_version})(?:{suffix_regex}|({garbage_suffix})?)(?:{buildid_suffix})?$"
    )

    def __init__(self, version_str: str) -> None:
        self.version_str = version_str
        self.key = self.sort_key(version_str)

    def __lt__(self, other) -> bool:
        return bool(self.key < other.key)

    def __eq__(self, other) -> bool:
        return bool(self.key == other.key)

    def without_buildid(self):
        return EXAVersion(re.sub(f"{self.buildid_suffix}$", "", self.version_str))

    def sort_key(self, version_str: str) -> Tuple[Tuple[int, ...], ...]:
        matched_version = self.version_regex.match(version_str)
        if matched_version:
            (
                version,
                *suffix_versions,
                garbage,
                docker_version,
            ) = matched_version.groups()
        else:
            raise NotImplementedError(f"Cannot parse version {version_str}")

        def split_ver(ver):
            if ver is None:
                return (-1,)
            return tuple(int(i) for i in ver.split("."))

        def suffix_ver(suffix, ver):
            if suffix is None:
                return (-1,)
            elif not ver:
                return (0,)
            else:
                return split_ver(ver)

        # Within one version prefix sort by first occuring suffix. Treat 1.0.beta as 1.0.beta0
        return (
            split_ver(version) + ((math.inf,) if garbage else (-1,)),
            *(
                suffix_ver(suffix, suffix_version)
                for suffix, suffix_version in zip(
                    suffix_versions[0::2], suffix_versions[1::2]
                )
            ),
            split_ver(docker_version),
        )

# }}}
# There should be better algorithms to define the affinity of node.
# So more derived classes will be created and used.
# {{{
class NodeAffinity(abc.ABC):
    def get_affinity(self, nid: int) -> int:
        raise NotImplementedError('Abstract class for node affinity.')

# Assumption: affinity is set:
# *exainit, automatically assign
# *add_node, on master node
# *manully in EXAConf
# It seems there is no any race condition
class IncrementalAffinity(NodeAffinity):
    min_affinity: int = 10
    def __init__(self) -> None:
        super().__init__()
        self.current_affinity = self.min_affinity

    def get_affinity(self, nid: int) -> int:
        ret_val = self.current_affinity
        self.current_affinity = self.current_affinity + 1
        return ret_val

class NIDAffinity(NodeAffinity):
    def __init__(self) -> None:
        super().__init__()
    def get_affinity(self, nid: int) -> int:
        return nid
# }}}

class EXAConf(object):
    """
    Read, write and modify the EXAConf file.

    This class depends on the 'configobj' module (https://configobj.readthedocs.io, https://github.com/DiffSK/configobj)
    """

    # {{{ Static / default values

    max_reserved_node_id = 10 # IDs 0-10 are reserved
    container_root = "/exa"
    license_filename = "license.exasol_license"
    node_root_prefix = "n"
    dev_prefix = "dev."
    data_dev_suffix  = ".data"
    meta_dev_suffix  = ".meta"
    data_dir = "data"
    storage_dir = "data/storage"
    bucketfs_dir = "data/bucketfs"
    etc_dir = "etc"
    tmp_dir = "tmp"
    support_dir = "tmp/support"
    spool_dir = "spool"
    sync_dir = "spool/sync"
    coredump_dir = "spool/coredumps"
    job_dir = "spool/jobs"
    job_queue_dir = "spool/jobs/queue"
    job_run_dir = "spool/jobs/run"
    job_finish_dir = "spool/jobs/finish"
    job_archive_dir = "spool/jobs/archive"
    job_id_file = "next.id"
    conf_ssl_dir = "etc/ssl"
    conf_dwad_dir = "etc/dwad"
    conf_remote_volumes_dir = "etc/remote_volumes/"
    md_dir = "metadata"
    md_storage_dir = "metadata/storage"
    md_dwad_dir = "metadata/dwad"
    log_dir = "logs"
    logd_dir = "logs/logd"
    cored_log_dir = "logs/cored"
    db_log_dir = "logs/db"
    docker_log_dir = "logs/docker"
    syslog_dir = "logs/syslog"
    device_pool_dir = "sys/devices"
    node_uuid = "etc/node_uuid"
    valid_platforms = ('docker', 'vm', 'aws', 'azure', )
    valid_vol_types = ('data', 'archive', 'remote', 'object', )
    valid_remote_vol_types = ('smb', 'ftp', 's3', 'file', 'gs', 'azure')
    valid_object_vol_types = ('s3', 'gs', 'azure')
    remote_vol_id_offset = 10000
    docker_logs_filename = "docker_logs"
    docker_max_logs_copies = 9
    def_cored_port = 325
    def_cored_canary_file_dirs = None
    def_cored_canary_timeout = 90
    def_cored_canary_grace_period = 300
    def_cored_canary_logging_enabled = True
    def_ssh_port = 22
    def_issue_tracker='Jira'
    def_bucketfs = "bfsdefault"
    def_bucket = "default"
    def_bucketfs_sync_period = 30000
    def_sector_size = 4096
    def_db_port = 8563
    def_conn_threads = 16
    def_max_conns = 1024
    def_meta_package = "none"
    def_license_rawmem_threshold = 80 #percent
    def_bucketfs_http_port = 2580
    def_bucketfs_https_port = 0
    def_bucketfs_mode = 'rsync'
    def_bucketfs_bucketvolume = None
    def_xmlrpc_port = 443
    def_docker_privileged = True
    def_docker_network_mode = "bridge"
    def_docker_ipc_mode = "private"
    lvm_volumes = ['/run/udev:/run/udev:rw','/run/lvm:/run/lvm:rw','/lib/modules:/lib/modules:ro']
    def_docker_volumes = lvm_volumes
    def_plugin_dir = 'plugins'
    def_jdbc_driver_dir = "drivers/jdbc"
    def_oracle_driver_dir = "drivers/oracle"
    def_vol_block_size = 4096 # block-size in bytes
    def_vol_stripe_size = 262144 # stripe-size in bytes
    def_vol_perm = "rwx"
    def_vol_prio = 10
    def_vol_shared = True
    def_arch_vol_shared = True
    def_arch_vol_block_size = 65536
    def_arch_vol_stripe_size = 65536
    def_space_warn_threshold = 90 #percent
    def_bg_rec_limit_1gb = 75 #recovery limit (1GBit)
    def_bg_rec_limit_10gb = 300 #recovery limit (10GBit)
    def_disk_component = 'exastorage'
    def_storage_request_timeout = 20000
    def_storage_connect_timeout = 20000
    def_storage_connection_threads = 16
    def_storage_max_connections = 1024
    def_storage_proxy_url = None
    def_webterm_subdir = "share/webterm/"
    def_device_type = "block"
    def_c4socket = "/var/run/c4_socket"
    def_timezone = "Europe/Berlin"
    def_legacy_platform_manager = True
    def_slc_globs = ['/opt/exasol/slc-*/ScriptLanguages*']
    def_resolv_filename = "/etc/resolv.conf"
    def_hosts_filename = "/etc/hosts"
    def_rsyslog_filename = "/exa/etc/rsyslog.conf"
    def_hugepages = 0
    def_groups_start_id = 500
    def_users_start_id = 500
    def_webui_backend_port = 4444
    def_loadbalancer_flag = False
    def_ui_domain = "exacluster.local"
    def_ui_backend_binary = "db-restapi"
    reserved_users = {'exameta': 55555, '_owner': None, '_self': None}
    reserved_groups = {'exameta': 55555, 'exahugepages': 55554}

    def_old_default_cloud_volume = 'cloud_data_remote_volume'
    def_logging_logrotation_types = ['remote']
    def_remote_logrotation_volume = 'default_logrotation_volume'
    def_remote_logrotation_prefix = "logs"
    def_remote_backup_volume = 'default_backup_volume'
    def_remote_backup_prefix = "backup"
    def_logging_backup_count = 60

    # }}}
    # {{{ Init

    def __init__(self, root = None, initialized = None, filename="EXAConf") -> None:
        """
        Creates a new EXAConf instance from the file 'EXAConf' within the given
        root directory. If 'initialized' is true, an exception is thrown if
        the file 'EXAConf' does not exist.
        """

        # Version numbers of the current cluster
        # NOTE : the version numbers are somewhat special. The COS
        # and DB version are overwritten by the ones in the EXAConf file
        # (present after initialization). The DB version may also be
        # overwritten during initialization (if provided on the CLI
        # or taken from the Docker image).
        # The 'version' parameter is static and denotes the version
        # of the EXAConf python module and EXAConf format
        self.version = exacos_constants.full_version()
        self.set_os_version(self.version)
        self.set_db_version(self.version)
        self.img_version = self.version

        # set root to container_root if omitted
        # --> only true when called from within the container
        if not root:
            self.root = os.path.join(self.container_root, self.etc_dir)
        else:
            self.root = root
        # check if root actually exists
        if not os.path.isdir(self.root):
            raise EXAConfError("root directory '%s' does not exist (or is a file)!" % self.root)
        self.conf_path = os.path.join(self.root, filename)
        # if initialized is true, the given file has to exist!
        if initialized and not os.path.exists(self.conf_path):
            raise EXAConfError("EXAConf file '%s' does not exist! Has the cluster been initialized?" % self.conf_path)
        # read / create configuration
        try:
            self.revert()
        except configobj.ConfigObjError as e:
            raise EXAConfError("Failed to read '%s': %s" % (self.conf_path, e))

        # update and validate content if EXAConf is already initialized
        # also read current version numbers from config
        if self.initialized():
            self.check_integrity()
            self.validate()
            if "OSVersion" in self.config["Global"].scalars:
                self.set_os_version(self.config["Global"]["OSVersion"])
            if "DBVersion" in self.config["Global"].scalars:
                self.set_db_version(self.config["Global"]["DBVersion"])
            # has been introduced later, i. e. may be absent
            if "ImageVersion" in self.config["Global"].scalars:
                self.img_version = self.config["Global"]["ImageVersion"]
            if "AuthenticationToken" not in self.config["Global"].scalars:
                self.config["Global"]["AuthenticationToken"] = self.generate_authentication_token()
                self.commit()

        self._affinity_ctrl: 'NIDAffinity' = NIDAffinity()
    # }}}
    # {{{ Check integrity

    def check_integrity(self) -> None:
        """
        Checks if the current checksum is valid by computing a new one and comparing them (if the checksum is not 'NONE').
        If the current checksum is 'COMMIT', all changes are commited along with the new checksum (also, the  revision
        number is increased).
        """

        curr_checksum = self.get_checksum()
        # ignore (checksum will be computed on next commit)
        if curr_checksum.upper() == "NONE":
            return
        # warn, because no checksum will ever be computed
        elif curr_checksum.upper() == "DISABLED":
            sys.stderr.write("WARNGING::EXAConf: Integrity check is disabled!\n")
            return
        # commit
        elif curr_checksum.upper() == "COMMIT":
            self.commit()
            return
        # compare
        new_checksum = self.compute_checksum()
        if curr_checksum != new_checksum:
            raise EXAConfIntegrityError("Integrity check failed! The stored checksum '%s' does not match the actual checksum '%s'. Set checksum to 'COMMIT' if you made intentional changes." % (curr_checksum, new_checksum))

    # }}}
    # {{{ Update self

    def update_self(self) -> None:
        """
        Checks if the EXAConf version stored in the file is older than the current one and
        updates options, section names and values if necessary.

        NOTE : not all newly introduced options are added to an existing EXAConf, only the
        ones that are necessary for exadt / exainit. For all other options, default values
        are used if they are not present.
        """
        conf_version = self.config["Global"]["ConfVersion"]
        if EXAVersion(self.version) == EXAVersion(conf_version):
            return
        # the EXAConf file is newer than the EXAConf module -> abort
        if EXAVersion(self.version) < EXAVersion(conf_version):
            raise EXAConfError("The version of the EXAConf file '%s' is higher (%s) than that of the EXAConf module (%s)! Please update your installation!" % (self.conf_path, conf_version, self.version))
        # the EXAConf file is older than the EXAConf module -> update
        if EXAVersion(self.version) > EXAVersion(conf_version):
            logging.info("Updating EXAConf '%s' from version '%s' to '%s'" % (self.conf_path, conf_version, self.version))
            # 6.0.1 :
            # - "Hostname" renamed to "Name"
            # - Introduced "Host"
            if EXAVersion(conf_version) < EXAVersion("6.0.1"):
                for section in self.config.sections:
                    if self.is_node(section):
                        node_sec = self.config[section]
                        node_sec["Name"] = node_sec["Hostname"]
                        del node_sec["Hostname"]
            # 6.0.4 :
            # - Revision number has been added
            if EXAVersion(conf_version) < EXAVersion("6.0.4"):
                if "Revision" not in self.config["Global"].scalars:
                    self.config["Global"]["Revision"] = 1
            # 6.0.7 :
            # - Node UUID has been added
            #   It is initialized with 'IMPORT' because the existing nodes already have a UUID
            #   (generated by 'exainit') and we don't want to change it! Instead, exainit
            #   imports the existing node UUID into EXAConf during the next boot process.
            #
            # - IMPORTANT: exainit respects 'IMPORT' only since 6.0.7 (although it has been
            #   introduced in 6.0.5), therefore we need to set the UUID to 'IMPORT', even if
            #   it has been initialized with a different value when creating a cluster with
            #   6.0.5 / 6.0.6 (it hasn't been used anyway).
            #
            # - Checksum has been added
            if EXAVersion(conf_version) < EXAVersion("6.0.7"):
                for section in self.config.sections:
                    if self.is_node(section):
                        node_sec = self.config[section]
                        node_sec["UUID"] = "IMPORT"
                # we want the modified EXAConf to be commited asap
                # (in order to have integrity protection), so we
                # set it to 'COMMIT'
                if "Checksum" not in self.config["Global"].scalars:
                    self.config["Global"]["Checksum"] = "COMMIT"
            # 6.1.1 Timezone has been added
            if EXAVersion(conf_version) < EXAVersion("6.1.1"):
                if "Timezone" not in self.config["Global"]:
                    self.config["Global"]["Timezone"] = self.def_timezone
            # 6.1.2 Add users and groups
            if EXAVersion(conf_version) < EXAVersion("6.1.2"):
                self.add_missing_users_and_groups()
            # 6.1.3 Add Runtime version and hugepages
            if EXAVersion(conf_version) < EXAVersion("6.1.3"):
                if "REVersion" not in self.config["Global"].scalars:
                    self.config["Global"]["REVersion"] = exacos_constants.re_version()
                if "Hugepages" not in self.config["Global"].scalars:
                    self.config["Global"]["Hugepages"] = self.def_hugepages
            # 6.1.4 New BucketFS ownership mechanism
            if EXAVersion(conf_version) < EXAVersion("6.1.4"):
                if "BucketFS" in self.config.sections:
                    bfs_owner = self.config["BucketFS"]["ServiceOwner"]
                    # obsolete 'service_owner' becomes new owner of every BucketFS
                    for section in self.config.sections:
                        if self.is_bucketfs(section):
                            bfs_sec = self.config[section]
                            bfs_sec["Owner"] = bfs_owner
                    # delete old global section
                    del self.config["BucketFS"]
            # 6.1.5 Rename 'NumMasterNodes' to 'NumActiveNodes' in DB section
            if EXAVersion(conf_version) < EXAVersion("6.1.5"):
                for section in self.config.sections:
                    if self.is_database(section):
                        db_sec = self.config[section]
                        db_sec["NumActiveNodes"] = db_sec["NumMasterNodes"]
                        del db_sec["NumMasterNodes"]
            # 6.2.2 Encode passwords for remote volumes in base64 (SPOT-8526),
            #       add default ssl directory in SSL section
            if EXAVersion(conf_version) < EXAVersion("6.2.2"):
                ssl_sec = self.config["SSL"]
                if ssl_sec["Cert"].strip().startswith("/path/to/"):
                    ssl_sec["Cert"] = os.path.join(self.container_root, self.conf_ssl_dir, "ssl.crt")
                if ssl_sec["CertKey"].strip().startswith("/path/to/"):
                    ssl_sec["CertKey"] = os.path.join(self.container_root, self.conf_ssl_dir, "ssl.key")
                if ssl_sec["CertAuth"].strip().startswith("/path/to/"):
                    ssl_sec["CertAuth"] = os.path.join(self.container_root, self.conf_ssl_dir, "ssl.ca")

                for section in self.config.sections:
                    if self.is_remote_volume(section):
                        vol_sec = self.config[section]
                        if "Passwd" in vol_sec.scalars:
                            # check if password is already base64 encoded
                            try: base64.b64decode(vol_sec["Passwd"].strip().encode())
                            except:
                                vol_sec["Passwd"] = base64.b64encode(vol_sec["Passwd"].strip().encode()).decode()

            if EXAVersion(conf_version) < EXAVersion("7.0.0"):
                ssl_sec = self.config["SSL"]
                ssl_sec["CertVerify"] = "none"
            # 7.1.2 Cored filter incoming packets based on port and subnet.
            if EXAVersion(conf_version) < EXAVersion("7.1.2"):
                self.set_cored_use_privileged_ports(self.get_cored_use_privileged_ports())
                self.set_cored_allow_only_same_subnet(self.get_cored_allow_only_same_subnet())
                self.set_cored_subnets(self.get_cored_subnets())

            # 7.2.0-dev2 Add uuid for db clusters to allow runtime renaming
            if EXAVersion(conf_version) < EXAVersion("7.2.0-dev2"):
                for section in self.config.sections:
                    if self.is_database(section):
                        db_sec = self.config[section]
                        if "ID" not in db_sec.scalars:
                            db_name = self.get_section_id(section)
                            if is_valid_b64_uuid(db_name):
                                # Smoothly migrate preexisting SaaS deployments that put the id in the name
                                db_sec["ID"] = db_name
                            else:
                                db_sec["ID"] = gen_b64_uuid()

            if EXAVersion(conf_version) < EXAVersion("8.13.0"):
                for section in self.config.sections:
                    if self.is_remote_volume(section):
                        vol_name = self.get_section_id(section)
                        if vol_name == self.def_old_default_cloud_volume:
                            vol_sec = self.config[section]
                            vol_owner = [ int(o.strip()) for o in vol_sec["Owner"].split(":") if o.strip() != "" ]
                            self.add_remote_volume(
                                vol_type=vol_sec["Type"],
                                url = vol_sec["URL"].rstrip("/") + f"/{self.def_remote_logrotation_prefix}",
                                owner = vol_owner,
                                remote_volume_name = self.def_remote_logrotation_volume,
                                commit=False,
                            )
                            self.add_remote_volume(
                                vol_type=vol_sec["Type"],
                                url = vol_sec["URL"].rstrip("/") + f"/{self.def_remote_backup_prefix}",
                                owner = vol_owner,
                                remote_volume_name = self.def_remote_backup_volume,
                                commit=False,
                            )
                            self.remove_remote_volume(
                                remote_volume_name=self.def_old_default_cloud_volume,
                                commit=False,
                            )
                            log_cfg = self.get_logging()
                            if ('remote' in log_cfg.get('LogRotationTypes', [])
                                    and log_cfg.get('RemoteLogRotationVolume') == self.def_old_default_cloud_volume
                                    and log_cfg.get('RemoteLogRotationPrefix') == self.def_remote_logrotation_prefix):
                                self.update_logging(
                                    logrotation_types=self.def_logging_logrotation_types,
                                    remote_logrotation_volume=self.def_remote_logrotation_volume,
                                    remote_logrotation_prefix="",
                                    commit=False,
                                )
            if EXAVersion(conf_version) < EXAVersion("8.18.0"):
                if "REVersion" in self.config["Global"].scalars:
                    del self.config["Global"]["REVersion"]

            # always increase version number
            self.config["Global"]["ConfVersion"] = self.version
            self.commit()

    # }}}
    # {{{ Check img compat

    def check_img_compat(self) -> Tuple[bool, str, str]:
        """
        Checks if the version of the current EXAConf module is identical with or higher than the image version stored in the given EXAConf.
        If this is not the case, then the given EXAConf file has been created with a newer docker image and may not be compatible
        (i. e. the exadt / EXAConf modules need an upgrade).
        NOTE: this assumes that older images can be used with more recent EXAConf / exadt versions.
        """
        img_version = self.get_img_version()
        if EXAVersion(self.version) >= EXAVersion(img_version).without_buildid():
            return (True, self.version, img_version)
        else:
            return (False, self.version, img_version)

    # }}}
    # {{{ Check update needed

    def check_update_needed(self, db_version=None, os_version=None, re_version=None, img_version=None) -> Tuple[bool, str]:
        """
        Checks if the current EXAConf file contains image, db, os or re versions that are different from the given
        ones (i. e. the installation needs to be updated). Also checks (since 6.1.3) if the EXAConf file version
        differs from the EXAConf module version.
        """
        res = ""
        need_update = False
        if img_version:
            conf_img_version = self.get_img_version()
            if EXAVersion(img_version) != EXAVersion(conf_img_version):
                res += "- image version: %s vs. %s\n" % (conf_img_version, img_version)
                need_update = True
        if db_version:
            conf_db_version = self.get_db_version()
            if EXAVersion(db_version) != EXAVersion(conf_db_version):
                res += "- db version: %s vs. %s\n" % (conf_db_version, db_version)
                need_update = True
        if os_version:
            conf_os_version = self.get_os_version()
            if EXAVersion(os_version) != EXAVersion(conf_os_version):
                res += "- os version: %s vs. %s\n" % (conf_os_version, os_version)
                need_update = True
        # always compare EXAConf module vs. file version
        conf_version = self.config["Global"]["ConfVersion"]
        if EXAVersion(conf_version) != EXAVersion(self.version):
            res += "- EXAConf version: %s vs. %s\n" % (conf_version, self.version)
            need_update = True

        return (need_update, res)

    # }}}
    # {{{ Set OS version

    def set_os_version(self, os_version) -> None:
        """
        Stores the given OS (EXAClusterOS) version and builds the path to the OS installation
        based on the OS version.
        """
        self.os_version = os_version.strip()
        self.os_dir = f"/opt/exasol/cos-{self.os_version}"

    # }}}
    # {{{ Set DB version

    def set_db_version(self, db_version) -> None:
        """
        Stores the given database (EXASolution) version and builds the path to the database installation
        based on the db version.
        """
        self.db_version = db_version.strip()
        self.db_dir = f"/opt/exasol/db-{self.db_version}"

    # }}}
    # {{{ Update db version

    def update_db_version(self, db_version) -> None:
        """
        Replaces all occurences of the database version number with the given one
        and commits the configuration (used to update the cluster).
        """

        db_version = db_version.strip()
        # get all databases with the current (old) version and replace with given one
        filters = {"Version" : self.db_version}
        db_configs = self.get_databases(filters=filters)
        for db in db_configs.items():
            db_sec = self.config[db[1]._sec_name]
            db_sec["Version"] = db_version

        path_db_curr = PurePath(self.get_db_dir(self.db_version))
        path_db_new = PurePath(self.get_db_dir(db_version))
        exasuite_db_curr = path_db_curr.name if path_db_curr.parent.name == "exasol" else path_db_curr.relative_to(path_db_curr.parent.parent)
        exasuite_db_new = path_db_new.name if path_db_new.parent.name == "exasol" else path_db_new.relative_to(path_db_new.parent.parent)

        bucketfs_conf = self.get_bucketfs()
        for bfs in bucketfs_conf.items():
            for bucket in bfs[1].buckets.items():
                bucket_sec = self.config[bfs[1]._sec_name][bucket[1]._sec_name]
                if "AdditionalFiles" in bucket_sec.scalars:
                    # Replacement in three steps to ensure both EXASuite-X → EXASuite-Y and EXASuite-Z → /opt/exasol
                    # transitions work correctly and preserve EXAClusterOS paths if needed
                    bucket_sec["AdditionalFiles"] = bucket_sec["AdditionalFiles"].replace(str(path_db_curr), str(path_db_new))
                    bucket_sec["AdditionalFiles"] = bucket_sec["AdditionalFiles"].replace(str(exasuite_db_curr), str(exasuite_db_new))
                    bucket_sec["AdditionalFiles"] = bucket_sec["AdditionalFiles"].replace(str(path_db_curr.name), str(path_db_new.name))

        # replace global version
        self.config["Global"]["DBVersion"] = db_version
        self.set_db_version(db_version)
        self.commit()

    # }}}
    # {{{ Update os version

    def update_os_version(self, os_version) -> None:
        """
        Replaces all occurences of the OS version number with the given one
        and commits the configuration (used to update the cluster).
        """

        os_version = os_version.strip()

        path_os_curr = PurePath(self.get_os_dir())
        path_os_new = PurePath(self.get_os_dir(os_version))
        exasuite_os_curr = path_os_curr.name if path_os_curr.parent.name == "exasol" else path_os_curr.relative_to(path_os_curr.parent.parent)
        exasuite_os_new = path_os_new.name if path_os_new.parent.name == "exasol" else path_os_new.relative_to(path_os_new.parent.parent)

        bucketfs_conf = self.get_bucketfs()
        for bfs_name, bfs_conf in bucketfs_conf.items():
            # in case this is a bucketfs without buckets
            try:
                for _, b_conf in bfs_conf.buckets.items():
                    bucket_sec = self.config[bfs_conf._sec_name][b_conf._sec_name]
                    if "AdditionalFiles" in bucket_sec.scalars:
                        # Replacement in three steps to ensure both EXASuite-X → EXASuite-Y and EXASuite-Z → /opt/exasol
                        # transitions work correctly and preserve EXASolution paths if needed
                        bucket_sec["AdditionalFiles"] = bucket_sec["AdditionalFiles"].replace(str(path_os_curr), str(path_os_new))
                        bucket_sec["AdditionalFiles"] = bucket_sec["AdditionalFiles"].replace(str(exasuite_os_curr), str(exasuite_os_new))
                        bucket_sec["AdditionalFiles"] = bucket_sec["AdditionalFiles"].replace(str(path_os_curr.name), str(path_os_new.name))
            except:
                pass
        # replace global version
        self.config["Global"]["OSVersion"] = os_version
        self.set_os_version(os_version)
        self.commit()

    # }}}
    # {{{ Update re version

    def update_re_version(self, re_version) -> None:
        """deprecated"""
        pass
        self.commit()

    # }}}
    # {{{ Update image version

    def update_img_version(self, img_version) -> None:
        """
        Updates the EXABase image version.
        """

        self.config["Global"]["ImageVersion"] = img_version
        self.img_version = img_version.strip()
        self.commit()

    # }}}
    # {{{ Clear configuration

    def clear_config(self) -> None:
        """
        Clears all content of the 'EXAConf' file and this EXAConf instance.
        """
        self.config.reset()
        self.__atomic_write()
        logging.info("Cleared configuration in '%s'." % self.conf_path)

    # }}}
    # {{{ Commit

    def commit(self) -> None:
        """
        Writes the configuration to disk (into '$RootDir/EXAConf')
        """

        curr_checksum = self.get_checksum()
        # special case : checksum protection is disabled
        if curr_checksum.upper() == "DISABLED":
            # don't store checksum but always increase revision
            self.config["Global"]["Revision"] = str(self.get_revision() + 1)
        else:
            new_checksum = self.compute_checksum()
            # increase revision if old and new checksum are different
            if curr_checksum != new_checksum:
                self.config["Global"]["Checksum"] = new_checksum
                self.config["Global"]["Revision"] = str(self.get_revision() + 1)
        # write config
        self.__atomic_write()
        # reload in order to force type conversion
        # --> parameters added as lists during runtime are converted back to strings (as if they have been added manually)
        self.revert()
        # modify permissions
        try:
            os.chmod(self.conf_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as e:
            raise EXAConfError("Failed to change permissions for '%s': %s" % (self.conf_path, e))

    # }}}
    # {{{ Revert

    def revert(self) -> None:
        """
        Revert all changes that have not yet been committed.
        """
        cfg = configobj.ConfigObj(
            self.conf_path,
            list_values = False,
            write_empty_values = True,
            indent_type = '    ',
        )
        # Assign separately to make this atomic and thus thread-safe
        # ConfigObj.reload() first clears the existing object, which isn't atomic
        self.config = cfg

    # }}}
    # {{{ Compute checksum

    def compute_checksum(self) -> str:
        """
        Computes the MD5 sum of this EXAConf instance (but does NOT store it).
        """

        # replace and remember current revision and checksum
        curr_revision = self.config["Global"]["Revision"]
        curr_md5 = self.config["Global"]["Checksum"]
        self.config["Global"]["Revision"] = "PLACEHOLDER"
        self.config["Global"]["Checksum"] = "PLACEHOLDER"
        # write config to string
        serialized_conf = io.BytesIO()
        self.config.write(outfile=serialized_conf)
        # compute MD5 sum
        md5 = hashlib.md5()
        md5.update(serialized_conf.getvalue())
        new_md5 = md5.hexdigest()
        # restore revision and checksum
        self.config["Global"]["Revision"] = curr_revision
        self.config["Global"]["Checksum"] = curr_md5
        return new_md5

    # }}}
    # {{{ Get checksum

    def get_checksum(self):
        """
        Returns the current checksum that is stored in this EXAConf instance (does NOT compute a new one)."
        """

        if "Checksum" in self.config["Global"].scalars:
            return self.config["Global"]["Checksum"]
        else:
            return "NONE"

    # }}}
    # {{{ Generate new authentication token

    def generate_authentication_token(self) -> str:
        token = "%s:%s" % (''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16)),
                           ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(64)))
        return base64.b64encode(token.encode()).decode()

    # }}}
    # {{{ Get authentication token

    def get_authentication_token(self, encoded = False):
        """
        Returns the authentication token for internal usage in confd.
        """
        if "AuthenticationToken" in self.config["Global"].scalars:
            if encoded:
                return self.config["Global"]["AuthenticationToken"]
            return base64.b64decode(self.config["Global"]["AuthenticationToken"].encode()).decode()
        return None

    # }}}
    # {{{ Write config atomically (use commit())

    def __atomic_write(self) -> None:
        try:
            sr = os.stat(self.conf_path)
            mode, uid, gid = sr.st_mode, sr.st_uid, sr.st_gid
        except FileNotFoundError:
            mode, uid, gid = stat.S_IRUSR | stat.S_IWUSR, None, None
        with atomic_file_writer(
            self.conf_path,
            writemode='wb',
            mode=mode,
            uid=uid,
            gid=gid,
        ) as fd:
            self.config.write(outfile=fd)
    # }}}

    # {{{ Write copy

    def write_copy(self, filename) -> None:
        """
        Writes a copy of this EXAConf instance to the given outfile.
        """
        with open(filename, 'wb') as f:
            self.config.write(outfile=f)

    # }}}
    # {{{ Platform valid

    def platform_valid(self, platform) -> bool:
        """
        Checks if the given platform is in the list of valid platforms.
        """
        return platform.lower() in self.valid_platforms

    # }}}
    # {{{ Is initialized

    def initialized(self) -> bool:
        """
        Checks if the current instance has already been initialized.
        """
        return "Global" in self.config.sections

    # }}}
    # {{{ Initialize legacy

    def initialize_legacy (self, config_obj: dict) -> None:
        kwargs: dict = {k : v for k, v in config_obj.items () if k in self.initialize.__code__.co_varnames}
        self.initialize (**kwargs)

    # }}}
    # {{{ Initialize DB IdP

    def initialize_db_idp (self, config_obj: dict) -> None:
        db_idp_config = config ()
        db_idp_config['params'] = ''
        params = ''
        db_idp_key = 'db_name'
        for k, v in config_obj.items ():
            if k != db_idp_key:
                if params == '':
                    params = f'-{k}={v}'
                else:
                    params = f'{params} -{k}={v}'

        db_idp_config['params'] = params
        db_name = config_obj[db_idp_key] if db_idp_key in config_obj else '_all'
        self.set_database_conf (db_idp_config, db_name, False)

    # }}}
    # {{{ Initialize

    def initialize(self, name, image, num_nodes, device_type, force, platform,
                   db_version=None, os_version=None, re_version = None,
                   img_version=None, license=None,
                   add_archive_volume=True, def_owner = None,
                   saas = False, initial_sql = None,
                   default_sys_passwd_hash = None,
                   additional_sys_passwd_hashes = None,
                   idp_config = None, ui_domain = None,
                   no_db = False, no_odirect = False,
                   cloud_data_volume = None,
                   cloud_sync_volume = None,
                   cache_volume_disk = None,
                   quiet = False, template_mode = False, db_params = None) -> None:
        """
        Initializes the current EXAConf instance. If 'force' is true, it will be
        re-initialized and the current content will be cleared.

        NOTE: 'def_owner' MUST be a tuple of UID:GID in this case (not names),
        because we keep the UID but always use the default username (see 'add_default_users()').
        """

        # check if EXAConf is already initialized
        if self.initialized():
            if not force and not quiet:
                logging.info("EXAConf file '%s' is already initialized!" % self.conf_path)
                return
            else:
                self.clear_config()
        # sanity checks
        if not self.platform_valid(platform):
            raise EXAConfError("Platform '%s' is not valid!") % platform
        # set db and os version if given
        if db_version and db_version.strip() != "":
            self.set_db_version(db_version.strip())
        if os_version and os_version.strip() != "":
            self.set_os_version(os_version.strip())
        if img_version and img_version.strip() != "":
            self.img_version = img_version
        # determine owner of default entries
        if def_owner is None:
            def_owner = (get_euid(), get_egid())
        else:
            if not isinstance(def_owner[0], int) or not isinstance(def_owner[1], int):
                raise EXAConfError("'def_owner' must be a tuple of integers (UID:GID)!")

        cloud_data_volume_name = None
        cloud_volume_credentials = None
        if cloud_data_volume is not None:
            vol_type = cloud_data_volume[0]

            if ((vol_type == "s3") or (vol_type == "gs")):
                region, bucket, prefix, conn_threads, max_conns = cloud_data_volume[1:]
                conn_threads, max_conns, end_point = \
                    int(conn_threads) if conn_threads != '' else None, \
                    int(max_conns) if max_conns != '' else None, \
                    None
                if prefix == '': prefix = None

            elif (vol_type == "azure"):
                region, az_account, az_key, bucket, prefix, conn_threads, max_conns= cloud_data_volume[1:]
                conn_threads, max_conns, end_point  = \
                    int(conn_threads) if conn_threads != '' else None, \
                    int(max_conns) if max_conns != '' else None, \
                    None
                cloud_volume_credentials = az_account + ":" + az_key

                if prefix == '': prefix = None
            elif ((len(cloud_data_volume) >= 3) or (len(cloud_data_volume) <= 5)):
                # Collecting some legacy cloud formation code together
                if len(cloud_data_volume) == 3:
                    vol_type, region, bucket = cloud_data_volume
                    prefix, conn_threads, max_conns, end_point = None, None, None, None
                elif len(cloud_data_volume) == 4:
                    vol_type, region, bucket, prefix = cloud_data_volume
                    conn_threads, max_conns, end_point = None, None, None
                elif len(cloud_data_volume) == 5:
                    vol_type, region, bucket, prefix, conn_threads = cloud_data_volume
                    conn_threads, max_conns, end_point = int(conn_threads), None, None
                if prefix == '': prefix = None
            else: raise RuntimeError('Wrong cloud data volume format!')
            cloud_data_volume_name = "DataVolume2"
            if conn_threads is None:
                conn_threads = self.def_storage_connection_threads
            if max_conns is None:
                max_conns = self.def_storage_max_connections
            self.def_conn_threads = conn_threads
            self.def_max_conns = max_conns

        # Global section
        self.config["Global"] = {}
        glob_sec = self.config["Global"]
        glob_sec["Revision"] = "0"
        glob_sec["Checksum"] = "COMMIT"
        glob_sec["ClusterName"] = name
        glob_sec["Platform"] = platform.title()
        glob_sec["LicenseFile"] = os.path.abspath(license) if license else ""
        glob_sec["CoredPort"] = self.def_cored_port
        glob_sec["SSHPort"] = self.def_ssh_port
        glob_sec["IssueTracker"] = self.def_issue_tracker
        glob_sec["XMLRPCPort"] = self.def_xmlrpc_port
        glob_sec["WebUIBackendPort"] = self.def_webui_backend_port
        glob_sec["WebUIDomain"] = ui_domain if ui_domain else self.def_ui_domain
        glob_sec["EnableLoadBalancer"] = self.def_loadbalancer_flag
        glob_sec["AuthenticationToken"] = self.generate_authentication_token()
        glob_sec["Networks"] = "private"
        glob_sec["NameServers"] = "8.8.8.8"
        glob_sec["C4Socket"] = self.def_c4socket
        glob_sec["Timezone"] = self.def_timezone
        glob_sec["LegacyPlatformManager"] = self.def_legacy_platform_manager
        glob_sec["LicenseRAWMEMWarnThreshold"] = self.def_license_rawmem_threshold
        glob_sec["Hugepages"] = self.def_hugepages
        glob_sec["StorageConnectionThreads"] = self.def_conn_threads
        glob_sec["StorageMaxConnections"] = self.def_max_conns
        glob_sec["MetaPackage"] = self.def_meta_package
        glob_sec["ConfVersion"] = self.version
        glob_sec["OSVersion"] = self.os_version
        glob_sec["DBVersion"] = self.db_version
        glob_sec["ImageVersion"] = self.img_version
        glob_sec["CoredUsePrivilegedPorts"] = True
        glob_sec["CoredAllowOnlySameSubnet"] = True
        glob_sec["CoredSubnets"] = ""
        # comments
        glob_sec.comments["MetaPackage"] = ["The meta package name, which was installed by C4 to run this system."]
        glob_sec.comments["Networks"] = ["List of networks for this cluster: 'private' is mandatory, 'public' is optional."]
        glob_sec.comments["NameServers"] = ["Comma-separated list of nameservers for this cluster."]
        glob_sec.comments["SearchDomains"] = ["Comma-separated list of search domains for this cluster."]
        glob_sec.comments["Hugepages"] = ["Nr. of hugepages ('0' = disabled, 'host' = manually configured on the host, 'auto' = set automatically based on DB config)"]
        glob_sec.comments["C4Socket"] = ["Path to c4 unix domain socket, used for deployments of new clusters and nodes."]
        glob_sec.comments["CoredUsePrivilegedPorts"] = ["Cored only accepts incomming messages from privileged ports (port number less than 1024)."]
        glob_sec.comments["CoredAllowOnlySameSubnet"] = ["Cored only accepts incomming messages from its own subnet."]
        glob_sec.comments["CoredSubnets"] = ["Which subnets Cored will allow messages from. Each subnet must be sepparated by a comma.",
                                         "A subnet is either an IPv4 or IPv6 address in CIDR notation."]

        # SSL section
        self.config["SSL"] = {}
        ssl_sec = self.config["SSL"]
        ssl_sec["Cert"] = os.path.join(self.container_root, self.conf_ssl_dir, "ssl.crt")
        ssl_sec["CertKey"] = os.path.join(self.container_root, self.conf_ssl_dir, "ssl.key")
        ssl_sec["CertAuth"] = os.path.join(self.container_root, self.conf_ssl_dir, "ssl.ca")
        ssl_sec["CertVerify"] = "none"
        ssl_sec["CertDomainName"] = ""
        #comments
        self.config.comments["SSL"] = ["\n","SSL options"]
        ssl_sec.comments["Cert"] = ["The SSL certificate, private key and CA for all EXASOL services"]
        ssl_sec.comments["CertVerify"] = ["Options to verify certificates: none, optional, required"]
        ssl_sec.comments["CertDomainName"] = ["Domain name in the certifcate"]

        # Docker section
        if platform.title() == "Docker":
            self.config["Docker"] = {}
            docker_sec = self.config["Docker"]
            docker_sec["RootDir"] = self.root
            docker_sec["Image"] = image
            docker_sec["DeviceType"] = device_type
            docker_sec["AdditionalVolumes"] = ""
            # comments
            self.config.comments["Docker"] = ["\n","Docker related options"]
            docker_sec.comments["RootDir"] = ["The directory that contains all data related to this docker cluster","(except for mapped devices)"]
            docker_sec.comments["Image"] = ["The EXASOL docker image used for all containers of this cluster"]
            docker_sec.comments["DeviceType"] = ["The type of storage devices for this cluster: 'block' or 'file'"]
            docker_sec.comments["AdditionalVolumes"] = ["Comma-separated list of volumes to be mounted in all containers (e. g. '/mnt/my_data:/exa/my_data:rw' )",
                    "These user-defined volumes are mounted additionally to the internal ones (like the node root volume)"]

        if saas:
            self.set_saas(True, "DB1", commit = False)
        if idp_config:
            self.set_idp_config(idp_config, commit = False)

        # default users and groups
        self.add_default_groups(def_owner[1])
        self.add_default_users(def_owner[0])

        # Node sections
        for node in range (1, num_nodes+1):
            node_id = self.max_reserved_node_id + node
            self.add_node(priv_net = "10.10.10.%i/24" % node_id, nid = node_id,
                          no_odirect = no_odirect, template_mode = template_mode,
                          commit = False)

        # EXAStorage sections
        self.config["EXAStorage"] = {}
        storage_sec = self.config["EXAStorage"]
        storage_sec["BgRecEnabled"] = True
        storage_sec["BgRecLimit"] = ""
        storage_sec["SpaceWarnThreshold"] = self.def_space_warn_threshold
        #comments
        self.config.comments["EXAStorage"] = ["\n", "Global EXAStorage options"]
        storage_sec.comments["BgRecEnabled"] = ["Enable or disable background recovery / data restoration (does not affect on-demand recovery)"]
        storage_sec.comments["BgRecLimit"] = ["Max. throughput for background recovery / data restoration (in MiB/s)"]
        storage_sec.comments["SpaceWarnThreshold"] = ["Space usage threshold (in percent, per node) for sending a warning"]

        # data volume
        self.add_volume(name = "DataVolume1", vol_type = "data", size = "",
                        disk = "disk1" if template_mode else "",
                        redundancy = 2 if num_nodes > 1 else 1,
                        nodes = list(self.get_nodes()),
                        owner = def_owner)
        # archive volume
        if add_archive_volume == True:
            self.add_volume(name = "ArchiveVolume1", vol_type = "archive", size = "",
                            disk = "disk1" if template_mode else "",
                            redundancy = 1,
                            nodes = list(self.get_nodes()),
                            owner = def_owner, shared = True)

        self.add_logging(logrotation_types = ["local"], commit=False)

        snapshot_volume_name = None
        data_volume_sync_name = None
        snapshot_volume_sync_name = None
        if cloud_data_volume is not None:
            self.add_object_volume(name = cloud_data_volume_name,
                vol_type = vol_type,
                region = region,
                bucket = bucket,
                prefix = prefix,
                end_point = end_point,
                owner = def_owner,
                credentials = cloud_volume_credentials)

            snapshot_volume_name = "SnapshotVolume"
            self.add_object_volume(name = snapshot_volume_name,
                vol_type = vol_type,
                region = region,
                bucket = bucket,
                prefix = prefix,
                end_point = end_point,
                owner = def_owner,
                shared = False,
                credentials = cloud_volume_credentials)

            #vol_type, region, bucket
            self.add_remote_volume(
                vol_type=vol_type,
                url = f'https://{bucket}.s3.{region}.amazonaws.com/{self.def_remote_logrotation_prefix}',
                owner = [0, 0],
                remote_volume_name = self.def_remote_logrotation_volume,
                options = "nocompression",
                commit = False,
            )
            self.add_remote_volume(
                vol_type=vol_type,
                url = f'https://{bucket}.s3.{region}.amazonaws.com/{self.def_remote_backup_prefix}',
                owner = def_owner,
                remote_volume_name = self.def_remote_backup_volume,
                commit = False,
            )

            self.update_logging(
                logrotation_types = ["local", "remote"],
                remote_logrotation_volume = self.def_remote_logrotation_volume,
                remote_logrotation_prefix = "",
                commit = False,
            )


            if cloud_sync_volume is not None:
                if len(cloud_sync_volume) == 3:
                    vol_type, region, bucket = cloud_sync_volume
                    prefix, conn_threads, max_conns, end_point = None, None, None, None
                elif len(cloud_sync_volume) == 4:
                    vol_type, region, bucket, prefix = cloud_sync_volume
                    conn_threads, max_conns, end_point = None, None, None
                    if prefix == '': prefix = None

            data_volume_sync_name = f"{cloud_data_volume_name}Sync"
            self.add_object_volume(name = data_volume_sync_name,
                vol_type = vol_type,
                region = region,
                bucket = bucket,
                owner = def_owner,
                shared = False,
                credentials = cloud_volume_credentials)

            snapshot_volume_sync_name = f"{snapshot_volume_name}Sync"
            self.add_object_volume(name = snapshot_volume_sync_name,
                vol_type = vol_type,
                region = region,
                bucket = bucket,
                owner = def_owner,
                shared = False,
                credentials = cloud_volume_credentials)

        if not no_db:
            # DB section
            self.add_database(name="DB1", version=str(self.db_version),
                              mem_size="%s GiB" % str(self.get_num_nodes() *2), #2 GiB per node
                              port = self.def_db_port,
                              owner = def_owner,
                              nodes = self.get_nodes(),
                              num_active_nodes = self.get_num_nodes(),
                              data_volume = "DataVolume1",
                              cloud_data_volume = cloud_data_volume_name,
                              snapshot_volume = snapshot_volume_name,
                              data_volume_sync = data_volume_sync_name,
                              snapshot_volume_sync = snapshot_volume_sync_name,
                              params = db_params,
                              enable_auditing = True,
                              initial_sql = initial_sql,
                              default_sys_passwd_hash = default_sys_passwd_hash,
                              additional_sys_passwd_hashes = additional_sys_passwd_hashes,
                              cache_volume_disk = cache_volume_disk,
                              commit = False)

        # The default BucketFS
        self.add_bucketfs(bucketfs_name = self.def_bucketfs, owner = def_owner,
                          http_port = self.def_bucketfs_http_port,
                          https_port = self.def_bucketfs_https_port,
                          sync_key = None, sync_period = None,
                          commit = False)

        # Bucket sub-section
        bfs_sec = self.config["BucketFS : %s" % self.def_bucketfs]
        bfs_sec["Bucket : %s" % self.def_bucket] = {}
        bucket_sec = bfs_sec["Bucket : %s" % self.def_bucket]
        bucket_sec["ReadPasswd"] = gen_bfs_passwd()
        bucket_sec["WritePasswd"] = gen_bfs_passwd()
        bucket_sec["Public"] = "True"
        # comments
        bfs_sec.comments["Bucket : default"] = ["The default bucket (auto-generated)"]

        self.commit()
        if not quiet:
            logging.info("Successfully initialized configuration in '%s'." % self.conf_path)

    # }}}
    # {{{ Validate the configuration

    def validate(self) -> None:
        """ Validates the EXAConf configuration file. """

        # validation only makes sense after initalization
        if not self.initialized():
            raise EXAConfError("Configuration is not initialized! Use 'init-cluster' in order to initialize it.")

        # public network is optional
        have_priv_net = self.has_priv_net()
        have_pub_net = self.has_pub_net()
        if not have_priv_net:
            raise EXAConfError("The private network is disabled! Please enable it and specify a private IP for each node.")

        # docker specific checks
        have_docker = (self.platform_is("Docker"))
        if have_docker:
            if not "Docker" in self.config.sections:
                raise EXAConfError("Docker platform is specified but 'Docker' section is missing!")

        # check for duplicate entries in node sections
        node_names = []
        docker_volumes = []
        all_priv_nets = []
        all_pub_nets = []
        for section in self.config.sections:
            if self.is_node(section):
                node_sec = self.config[section]
                # name
                name = node_sec.get("Name")
                if not name or name == "":
                    raise EXAConfError("Name is missing in section '%s'!" % section)
                node_names.append(name)
                # docker volume (only for Docker installations!)
                if have_docker:
                    volume = node_sec.get("DockerVolume")
                    if not volume or volume == "":
                        raise EXAConfError("Docker volume is missing in section '%s'!" % section)
                    docker_volumes.append(volume)
                # private network
                node_priv_net = node_sec.get("PrivateNet")
                if node_priv_net and node_priv_net != "":
                    if not self.net_is_valid(node_priv_net):
                        raise EXAConfError("Private network '%s' in section '%s' is invalid!" % (node_priv_net, section))
                    all_priv_nets.append(node_priv_net)
                elif have_priv_net:
                    raise EXAConfError("Private network is enabled but network is missing in section '%s'!" % section)
                # public network
                node_pub_net = node_sec.get("PublicNet")
                if node_pub_net and node_pub_net != "":
                    if not self.net_is_valid(node_pub_net):
                        raise EXAConfError("Public network '%s' in section '%s' is invalid!" % (node_pub_net, section))
                    all_pub_nets.append(node_pub_net)
                elif have_pub_net:
                    raise EXAConfError("Public network is enabled but network is missing in section '%s'!" % section)
                ### storage devices
                node_devices = []
                # extract disk name and devices
                for section in node_sec.sections:
                    if self.is_disk(section):
                        disk_sec = node_sec[section]
                        if "Devices" in disk_sec.scalars:
                            disk_devices = [ d.strip() for d in disk_sec["Devices"].split(",") if d.strip() != "" ]
                            # remember all disks and devices of the current node
                            node_devices += disk_devices
                # check for duplicate device names
                dup = self.get_duplicates(node_devices)
                if dup and len(dup) > 0:
                    raise EXAConfError("Detected duplicate devices in section '%s': %s" %(section, dup))
                # TODO : check specified type vs. actual device type
        # check for duplicates and list them
        dup = self.get_duplicates(node_names)
        if dup and len(dup) > 0:
            raise EXAConfError("Detected duplicate node names: %s!" % dup)
        dup = self.get_duplicates(docker_volumes)
        if dup and len(dup) > 0:
            raise EXAConfError("Detected duplicate docker volumes: %s!" % dup)
        dup = self.get_duplicates(all_priv_nets)
        if dup and len(dup) > 0:
            raise EXAConfError("Detected duplicate private networks: %s!" % dup)
        dup = self.get_duplicates(all_pub_nets)
        if dup and len(dup) > 0:
            raise EXAConfError("Detected duplicate public networks: %s!" % dup)

    # }}}
    # {{{ Check if section is a node

    def is_node(self, section):
        """
        Returns true if the given section is a node.
        """
        return section.split(":")[0].strip() == "Node"

    # }}}
    # {{{ Check if section is a volume

    def is_volume(self, section):
        """
        Returns true if the given section is an EXAStorage volume.
        """
        return section.split(":")[0].strip() == "EXAVolume"

    # }}}
    # {{{ Check if section is a object volume

    def is_object_volume(self, section):
        """
        Returns true if the given section is a object volume.
        """
        return section.split(":")[0].strip() == "ObjectVolume"

    # }}}
    # {{{ Check if section is a remote volume

    def is_remote_volume(self, section):
        """
        Returns true if the given section is a remote volume.
        """
        return section.split(":")[0].strip() == "RemoteVolume"

    # }}}
    # {{{ Check if section is a database

    def is_database(self, section):
        """
        Returns true if the given section is an EXASolution database.
        """
        return section.split(":")[0].strip() == "DB"

    # }}}
    # {{{ Check if section is a backup schedule

    def is_backup(self, section):
        """
        Returns true if the given section is an EXASolution backup schedule.
        """
        return section.split(":")[0].strip() == "Backup"

    # }}}
    # {{{ Check if section is a snapshot backup schedule

    def is_snapshot_backup(self, section):
        """
        Returns true if the given section is an EXASolution snapshot backup schedule.
        """
        return section.split(":")[0].strip() == "Snapshot"

    # }}}
    # {{{ Check if section is a BucketFS

    def is_bucketfs(self, section):
        """
        Returns true if the given section is a BucketFS.
        """
        # don't try to split the global section!
        return section != "BucketFS" and section.split(":")[0].strip() == "BucketFS"

    # }}}
    # {{{ Check if section is a bucket

    def is_bucket(self, section):
        """
        Returns true if the given section is a bucket.
        """
        return section.split(":")[0].strip() == "Bucket"

    # }}}
    # {{{ Check if section is a disk

    def is_disk(self, section):
        """
        Returns true if the given section is a disk.
        """
        return section.split(":")[0].strip() == "Disk"

    # }}}
    # {{{ Check if IP is valid

    def ip_is_valid(self, ip) -> bool:
        """
        Returns true if the given string is a valid IP address (v4 or v6).
        """
        try:
            ipaddr.IPAddress(ip)
            return True
        except ValueError:
            return False

    # }}}
    # {{{ Check if network is valid

    def net_is_valid(self, net) -> bool:
        """
        Returns true if the given string is a valid IP network (v4 or v6).
        """
        try:
            ipaddr.IPNetwork(net)
            return True
        except ValueError:
            return False

    # }}}
    # {{{ Get network prefix len

    def get_net_pref_len(self, net):
        """
        Returns the prefix len of the given network (or -1 if network is not valid).
        """
        try:
            plen = ipaddr.IPNetwork(net).prefixlen
            return plen
        except ValueError:
            return -1

    # }}}
    # {{{ To net string

    def to_net_str(self, net, nid) -> str:
        """
        Checks and converts the given string to a full network string (e.g. by adding the prefixlen).
        """
        # replace 'x' and 'X' in IP with the node ID
        node_net = re.sub('[xX]+', str(nid), net)
        if not self.net_is_valid(node_net):
            raise EXAConfError("String '%s' is not a valid network (valid example: '10.10.10.11/16')!" % node_net)
        try:
            res = str(ipaddr.IPNetwork(net))
            return res
        except ValueError:
            raise EXAConfError("Failed to convert '%s' to an IP network." % net)

    # }}}
    # {{{ IP type

    def ip_type(self, ip) -> int:
        """
        Returns 4 if the given string is a valid IPv4 address and 6 if it's
        a valid IPv6 address. Returns 0 if neither.
        """
        try:
            ipaddr.IPv4Address(ip)
            return 4
        except ipaddr.AddressValueError: pass
        try:
            ipaddr.IPv6Address(ip)
            return 6
        except ipaddr.AddressValueError: pass
        return 0

    # }}}
    # {{{ Has private network

    def has_priv_net(self) -> bool:
        """
        Returns true if a private network is enabled in EXAConf.
        """
        try:
            priv = "private" in [ n.strip() for n in self.config["Global"]["Networks"].split(",") ]
        except ValueError:
            return False
        return priv

    # }}}
    # {{{ Has public network

    def has_pub_net(self) -> bool:
        """
        Returns true if a public network is enabled in EXAConf.
        """
        try:
            pub = "public" in [ n.strip() for n in self.config["Global"]["Networks"].split(",") ]
        except ValueError:
            return False
        return pub

    # }}}
    # {{{
    def reset_node_affinity (self, commit: Optional[bool] = True) -> None:
        nodes = self.get_nodes()
        for nid in nodes.keys():
            node_sec = self.config[f'Node : {nid}']
            node_sec['Affinity'] = self._affinity_ctrl.get_affinity(int(nid))
        if commit:
            self.commit()
    # }}}
    # {{{ Get the working copy version tag for the system
    def get_meta_package(self):
        if "MetaPackage" in self.config["Global"]:
            return self.config["Global"]["MetaPackage"]
        return self.def_meta_package

    # }}}
    # {{{ Save the working copy version tag for the system
    def set_meta_package(self, meta_package: str) -> None:
        self.config["Global"]["MetaPackage"] = str(meta_package)
        self.commit()

    # }}}
    # {{{ Add node

    def add_node(self, priv_net, nid = None, name = None, pub_net = None, UUID = None,
                 no_odirect = False, template_mode = False, state = None, affinity = None, commit = True):
        """
        Adds a new node to the EXAConf. The ID is determined automatically
        if 'nid' is None. The name is built from the ID if not given.
        'priv_net' is mandatory, 'pub_net' is optional.
        """

        # determine ID and name
        node_id = int(nid) if nid is not None else None
        if node_id is None:
            node_id = self.get_max_node_id() + 1
        elif self.node_exists(node_id):
            raise EXAConfError("Node with ID %s already exists!" % str(node_id))
        node_name = name
        if node_name is None:
            node_name = "n" + str(node_id)

        # sanity checks
        if priv_net is None:
            raise EXAConfError("The private network has to be specified when adding a node!")

        if priv_net != None:
            priv_net = self.to_net_str(priv_net, node_id)
        if pub_net != None:
            pub_net = self.to_net_str(pub_net, node_id)

        # create the node section
        node_sec_name = "Node : " + str(node_id)
        self.config[node_sec_name] = {}
        node_sec = self.config[node_sec_name]
        node_sec["PrivateNet"] = priv_net if priv_net is not None else ""
        node_sec["PublicNet"] = pub_net if pub_net is not None else ""
        node_sec["Name"] = node_name
        node_sec["Affinity"] = affinity if affinity and isinstance(affinity, int) else self._affinity_ctrl.get_affinity(node_id)
        node_sec.comments["Affinity"] = ["Affinity decides how this node is used in the cluster. i.e, the possibility to become master"]
        # Add device template in template mode (given device is ignored)
        if template_mode:
            node_sec["Disk : disk1"] = {"Devices" : "dev.1 #'dev.1' must be located in '%s'" % os.path.join(self.container_root, self.storage_dir)}
            if no_odirect:
                node_sec["Disk : disk1"]["DirectIO"] = "False"

        # FIXME: this should be made cloud-agnostic
        for platform in ["AWS", "GCP"]:
            if self.platform_is(platform):
                node_sec["UUID"] = f"{self.config[platform]['ID'].upper().replace('-', '')}{node_id:08d}" if UUID is None else UUID

        # Docker specific options:
        if self.platform_is("Docker"):
            node_sec["UUID"] = gen_node_uuid() if UUID is None else UUID
            node_sec["DockerVolume"] = "n" + str(node_id)
            node_sec["ExposedPorts"] = str(self.def_db_port) + ":" + str(self.def_db_port + node_id)
            if self.def_bucketfs_http_port > 0:
                node_sec["ExposedPorts"] +=  ", " + str(self.def_bucketfs_http_port) + ":" + str(self.def_bucketfs_http_port + node_id)
            if self.def_bucketfs_https_port > 0:
                node_sec["ExposedPorts"] +=  ", " + str(self.def_bucketfs_https_port) + ":" + str(self.def_bucketfs_https_port + node_id)
            # Docker comments
            node_sec.comments["ExposedPorts"] = ["Ports to be exposed (container : host)"]
        # other platform options
        elif self.platform_is("VM"):
            node_sec["PrivateInterface"] = "eth0"
            node_sec["PublicInterface"] = "eth1"
        if state is not None and str(state).strip() != "":
            node_sec["State"] = str(state).strip()
        #comments
        self.config.comments[node_sec_name] = ["\n"]

        if commit:
            self.commit()
        return (node_id, node_name, node_sec["UUID"])

    # }}}
    # {{{ Set node conf

    def set_node_conf(self, node_conf, node_id, remove_disks=False, commit=True) -> None:
        """
        Changes the values of the given keys for the given node (or all nodes).
        The node ID can't be changed. If 'remove_disks' is True, all disks
        that are not part of the given node_conf will be deleted. Existing disks
        will always be updated and new ones added.

        If 'node_id' == '_all', the given node_configuration is applied to all nodes.
        Take care to remove all options that should not be changed (e. g. the
        network address).

        If a node with the given ID does not exist, it will be added and the
        given configuration will be applied. In that case the given 'node_conf'
        must contain a valid 'private_net'!
        """

        # add node if it doesn't exist
        if node_id != "_all" and not self.node_exists(node_id):
            self.add_node(priv_net = node_conf.private_net, nid = node_id)
            # nothing to change, since the ID can't be "_all"
            return

        # change configuration for the given node(s)
        nodes = self.get_nodes()
        for node in nodes.items():
            if node_id == "_all" or node[0] == node_id:
                node_sec = self.config[node[1]._sec_name]
                if "name" in node_conf:
                    node_sec["Name"] = node_conf.name
                if "uuid" in node_conf:
                    node_sec["UUID"] = str(node_conf.uuid)
                if "affinity" in node_conf:
                    node_sec["Affinity"] = node_conf.affinity
                # private and public network
                if "private_net" in node_conf:
                    node_sec["PrivateNet"] = self.to_net_str(node_conf.private_net, node[0])
                if "public_net" in node_conf:
                    node_sec["PublicNet"] = self.to_net_str(node_conf.public_net, node[0])
                # private and public IP
                if "private_ip" in node_conf:
                    if "PrivateNet" in node_sec.scalars and node_sec["PrivateNet"].strip() != "":
                        node_sec["PrivateNet"] = "/".join([node_conf.private_ip, str(self.get_net_pref_len(node_sec["PrivateNet"]))])
                    else:
                        raise EXAConfError("Public IP given for node %s but it has no public network!" % node_id)
                if "public_ip" in node_conf:
                    if "PublicNet" in node_sec.scalars and node_sec["PublicNet"].strip() != "":
                        node_sec["PublicNet"] = "/".join([node_conf.public_ip, str(self.get_net_pref_len(node_sec["PublicNet"]))])
                    else:
                        raise EXAConfError("Public IP '%s' given for node %s but it has no public network!" % (node_conf.public_ip, node_id))
                if "docker_volume" in node_conf:
                    node_sec["DockerVolume"] = os.path.basename(node_conf.docker_volume)
                if "exposed_ports" in node_conf:
                    ports = ", ".join(f"{p0}:{p1}" for p0, p1 in node_conf.exposed_ports)
                    node_sec["ExposedPorts"] = ports
                if "state" in node_conf and str(node_conf.state).strip() != "":
                    node_sec["State"] = str(node_conf.state).strip()
                # disks
                # a.) delete disks that don't exist in the node_conf
                if remove_disks is True:
                    for sect in tuple(node_sec.sections):
                        if self.is_disk(sect) and self.get_section_id(sect) not in node_conf.disks:
                            del node_sec[sect]
                # b.) update / add disks
                if "disks" in node_conf:
                    for name, disk in node_conf.disks.items():
                        disk_sec = odict()
                        if name in node_sec.sections:
                            disk_sec = node_sec[name]
                        if "component" in disk:
                            disk_sec["Component"] = disk.component
                        if "devices" in disk:
                            disk_sec["Devices"] = ", ".join(disk.devices)
                        if "drives" in disk:
                            disk_sec["Drives"] = ", ".join(disk.drives)
                        if "ephemeral" in disk and disk.ephemeral:
                            disk_sec["Ephemeral"] = True
                        if "mapping" in disk:
                            disk_sec["Mapping"] = ", ".join(f"{m0}:{m1}" for m0, m1 in disk.mapping)
                        if "direct_io" in disk and disk.direct_io is False: # omit 'True' since it's the default case
                            disk_sec["DirectIO"] = str(disk.direct_io)
                        node_sec["Disk : " + name] = disk_sec
        if commit:
            self.commit()

    # }}}
    # {{{ Remove node

    def remove_node(self, nid, force  = False, commit = True) -> None:
        """
        Removes the given node from the EXAConf if it is not part of an EXAStorage volume
        or a database (this check is skipped if 'force' is True).
        """

        node_sec = self.node_exists(nid)
        if not node_sec:
            raise EXAConfError("Node '%s' can't be removed because it does not exist in EXAConf!" % str(nid))

        if force is False:
            usage = self.get_node_usage(nid)
            if usage is not None:
                raise EXAConfError("Node '%s' can't be removed because it's in use!" % str(nid))

        del self.config[node_sec]

        if commit:
            self.commit()

    # }}}

    # {{{ Add volume

    def add_volume(self, *, name, vol_type, size, disk, redundancy, nodes, owner,
                   num_master_nodes = None, permissions = None, labels = None,
                   block_size = None,  stripe_size = None,
                   partition_size = None, shared = None,
                   http_port = None, https_port = None,
                   ftp_port = None, ftps_port = None, sftp_port = None,
                   commit = True) -> None:
        """
        Adds a new volume to the EXAConf. 'size' can either be an int (repr. the nr. of bytes)
        o ar a string (repr. units, as created by 'utils.bytes2units'). 'owner' must be a tuple
        of IDs.
        """

        # sanity checks
        if vol_type not in ('data', 'archive'):
            raise EXAConfError("Volume type '%s' is not valid!" % vol_type)
        # set default paramaters
        if permissions is None:
            permissions = self.def_vol_perm
        if block_size is None:
            if vol_type == "data":
                block_size = self.def_vol_block_size
            elif vol_type == "archive":
                block_size = self.def_arch_vol_block_size
        if stripe_size is None:
            if vol_type == "data":
                stripe_size = self.def_vol_stripe_size
            elif vol_type == "archive":
                stripe_size = self.def_arch_vol_stripe_size
        if num_master_nodes is None:
            num_master_nodes = len(nodes)
        if shared is None:
            shared = self.def_vol_shared if vol_type == "data" else self.def_arch_vol_shared
        # check if volume already exists
        if self.volume_exists(name):
            raise EXAConfError(f"Volume '{name}' can't be added because that name is already in use!")
        # check if given owner exists
        if not self.uid_exists(owner[0]):
            raise EXAConfError(f"Can't add volume '{name}' because owner with UID {owner[0]} doesn't exist!\n")
        if not self.gid_exists(owner[1]):
            raise EXAConfError(f"Can't add volume '{name}' because owner group with GID {owner[1]} doesn't exist!\n")

        # create the volume section
        vol_sec_name = "EXAVolume : " + str(name)
        self.config[vol_sec_name] = {}
        vol_sec = self.config[vol_sec_name]
        vol_sec["Type"] = vol_type
        vol_sec["Size"] = size if isinstance(size, str) else bytes2units(size)
        vol_sec["Disk"] = disk
        vol_sec["Nodes"] = ','.join(map(str, nodes))
        vol_sec["NumMasterNodes"] = str(num_master_nodes)
        vol_sec["Redundancy"] = redundancy
        vol_sec["BlockSize"] = block_size if isinstance(block_size, str) else bytes2units(block_size)
        vol_sec["StripeSize"] = stripe_size if isinstance(stripe_size, str) else bytes2units(stripe_size)
        if vol_type == 'archive':
            if partition_size != None:
                vol_sec["PartitionSize"] = partition_size if isinstance(partition_size, str) else bytes2units(partition_size)
        vol_sec["Owner"] = str(owner[0]) + " : " + str(owner[1])
        vol_sec["Permissions"] = permissions
        # optional values
        if labels:
            vol_sec["Labels"] = [ str(l) for l in labels ]
        for port_type in ('http', 'https', 'ftp', 'ftps', 'sftp'):
            varname, confname = '%s_port' % port_type, '%sPort' % port_type.capitalize()
            portvalue = locals().get(varname, None)
            if portvalue is None: continue
            if vol_type != 'archive':
                raise EXAConfError("Can't add volume %s because access port's can only be specified on archive volumes." % repr(name))
            try: portvalue = int(portvalue)
            except: raise EXAConfError("Can't add volume %s because %s port is not an integer: %s." % (repr(name), port_type, repr(portvalue)))
            vol_sec[confname] = portvalue
        vol_sec["Shared"] = str(shared)
        vol_sec["Priority"] = self.def_vol_prio
        #comments
        self.config.comments[vol_sec_name] = ["\n", "An EXAStorage volume"]
        vol_sec.comments["Type"] = ["Type of volume: 'data' | 'archive'"]
        vol_sec.comments["Owner"] = ["User and group IDs that own this volume (e. g. '1000:1005')"]
        vol_sec.comments["Disk"] = ["Name of the disk to be used for this volume.","This disk must exist on all volume nodes."]
        vol_sec.comments["Size"] = ["Volume size (e. g. '1 TiB')"]
        vol_sec.comments["Redundancy"] = ["Desired redundancy for this volume"]
        vol_sec.comments["Nodes"] = ["Comma-separated list of node IDs for this volume (put dedicated redundancy nodes at the end, if any)"]
        vol_sec.comments["NumMasterNodes"] = ["OPTIONAL: Nr. of master nodes for this volume. Remaining nodes will be used for redundancy only."]
        vol_sec.comments["Priority"] = ["OPTIONAL: I/O priority (0 = highest, 20 = lowest)"]
        vol_sec.comments["Shared"] = ["OPTIONAL: shared volumes can be opened (for writing) by multiple clients simultaneously"]
        vol_sec.comments["Labels"] = ["OPTIONAL: a comma-separated list of labels for this volume"]
        if commit:
            self.commit()

    # }}}
    # {{{ Add remote volume

    def add_remote_volume(self, vol_type, url, owner, remote_volume_name = None, remote_volume_id = None,
                          labels = None, username = None, password = None,
                          options = None, commit = True):
        """
        Adds a new remote volume to the EXAConf.
        """

        # sanity checks
        if vol_type not in self.valid_remote_vol_types:
            raise EXAConfError(f"Remote volume type '{vol_type}' is not valid!")
        if username and username.strip() == '':
            raise EXAConfError(f"Username '{username}' is not valid")
        # create ID if not given
        if remote_volume_id is None:
            remote_volume_id = int(self.get_max_remote_volume_id()) + 1
        # create remote_volume_name from remote_volume_id if not given
        if remote_volume_name is None:
            remote_volume_name = "r%04i" % (int(remote_volume_id) - self.remote_vol_id_offset)
        # check if volume already exists
        if self.volume_exists(remote_volume_name):
            raise EXAConfError(f"Remote volume '{remote_volume_name}' can't be added because that name is already in use!")
        # check if ID is over 10000
        if remote_volume_id < self.remote_vol_id_offset:
            raise EXAConfError(f"Remote volume IDs must be >= {self.remote_vol_id_offset}!")
        # check if ID is not yet used
        if self.remote_volume_id_exists(remote_volume_id):
            raise EXAConfError(f"Remote volume with ID {remote_volume_id} already exists!")
        # check if given owner exists
        if not self.uid_exists(owner[0]):
            raise EXAConfError(f"Can't add remote volume '{remote_volume_name}' because owner with UID {owner[0]} doesn't exist!\n")
        if not self.gid_exists(owner[1]):
            raise EXAConfError(f"Can't add remote volume '{remote_volume_name}' because owner group with GID {owner[1]} doesn't exist!\n")

        # create the volume section
        vol_sec_name = "RemoteVolume : " + str(remote_volume_name)
        self.config[vol_sec_name] = {}
        self.config.comments[vol_sec_name] = ["\n", "An EXAStorage remote volume"]
        vol_sec = self.config[vol_sec_name]
        vol_sec["Type"] = vol_type
        vol_sec["ID"] = remote_volume_id
        vol_sec["URL"] = url
        vol_sec["Owner"] = str(owner[0]) + " : " + str(owner[1])
        # optional values
        if username:
            vol_sec["Username"] = username
        if password:
            password = encode_remote_vol_passwd(password)
            vol_sec["Passwd"] = password
        if labels:
            vol_sec["Labels"] = [ str(l) for l in labels ]
        if options:
            vol_sec["Options"] = options

        if commit:
            self.commit()
        return (remote_volume_id, remote_volume_name)

    # }}}
    # {{{ Add object volume

    def add_object_volume(self, name, vol_type,
                          bucket, owner, region,
                          prefix = None,
                          end_point = None,
                          credentials = None,
                          permissions = None,
                          shared = None,
                          labels = None,
                          options = None,
                          commit = True) -> None:
        """
        Adds a new remote volume to the EXAConf.
        """

        # sanity checks
        if vol_type not in self.valid_object_vol_types:
            raise EXAConfError("Object volume type '%s' is not valid!" % vol_type)
        # check if volume already exists
        if self.volume_exists(name):
            raise EXAConfError("Volume '%s' can't be added because that name is already in use!" % name)
        if permissions is None:
            permissions = self.def_vol_perm
        if shared is None:
            shared = self.def_vol_shared
        # check if given owner exists
        if not self.uid_exists(owner[0]):
            raise EXAConfError("Can't add volume '%s' because owner with UID %i doesn't exist!\n" % (name, owner[0]))
        if not self.gid_exists(owner[1]):
            raise EXAConfError("Can't add volume '%s' because owner group with GID %i doesn't exist!\n" % (name, owner[1]))

        # create the volume section
        vol_sec_name = "ObjectVolume : " + str(name)
        self.config[vol_sec_name] = {}
        vol_sec = self.config[vol_sec_name]
        vol_sec["Type"] = vol_type
        vol_sec["Region"] = region
        vol_sec["Bucket"] = bucket
        if prefix:
            vol_sec["Prefix"] = prefix
            vol_sec.comments["Prefix"] = ["OPTIONAL: Prefix inside the bucket to use for files."]

        vol_sec["Owner"] = str(owner[0]) + " : " + str(owner[1])
        vol_sec["Permissions"] = permissions
        vol_sec["Shared"] = str(shared)
        vol_sec["Priority"] = self.def_vol_prio
        # optional values
        if credentials: vol_sec["Credentials"] = credentials
        if end_point:
            vol_sec["EndPoint"] = str(end_point)
            vol_sec.comments["EndPoint"] = ["OPTIONAL: endpoint to use."]
        if labels: vol_sec["Labels"] = [ str(l) for l in labels ]
        if options: vol_sec["Options"] = options
        #comments
        self.config.comments[vol_sec_name] = ["\n", "An EXAStorage volume"]
        vol_sec.comments["Type"] = [f"Type of volume: s3, gs or azure"]
        vol_sec.comments["Region"] = ["Region used for the storage (e.g. 'eu-west-1')"]
        vol_sec.comments["Bucket"] = ["Cloud bucket to use for storage."]
        vol_sec.comments["Owner"] = ["User and group IDs that own this volume (e.g. '1000:1005')"]
        vol_sec.comments["credentials"] = ["OPTIONAL: credentials for access the bucket."]
        vol_sec.comments["Priority"] = ["OPTIONAL: I/O priority (0 = highest, 20 = lowest)"]
        vol_sec.comments["Shared"] = ["OPTIONAL: shared volumes can be opened (for writing) by multiple clients simultaneously"]
        vol_sec.comments["Labels"] = ["OPTIONAL: a comma-separated list of labels for this volume"]
        if commit:
            self.commit()

    # }}}
    # {{{ Remove volume

    def remove_volume(self, name, force = False, commit = True) -> None:
        """
        Removes the given volume from the EXAConf if it is not used by a database
        (this check is skipped if 'force' is True).
        """

        vol_section = self.exa_volume_exists(name)
        if not vol_section:
            raise EXAConfError("Volume '%s' can't be removed because it doesn't exist in EXAConf!" % name)

        if force is False:
            usage = self.get_volume_usage(name)
            if usage is not None:
                raise EXAConfError("Volume '%s' can't be removed because it's in use!" % name)

        del self.config[vol_section]

        if commit:
            self.commit()

    # }}}
    # {{{ Remove object volume

    def remove_object_volume(self, name, force = False, commit = True) -> None:
        """
        Removes the given volume from the EXAConf if it is not used by a database
        (this check is skipped if 'force' is True).
        """

        vol_section = self.object_volume_exists(name)
        if not vol_section:
            raise EXAConfError("Volume '%s' can't be removed because it doesn't exist!" % name)

        if force is False:
            usage = self.get_volume_usage(name)
            if usage is not None:
                raise EXAConfError("Volume '%s' can't be removed because it's in use!" % name)

        del self.config[vol_section]

        if commit:
            self.commit()

    # }}}
    # {{{ Remove remote volume

    def remove_remote_volume(self, remote_volume_name=None, remote_volume_id=None, force = False, commit = True) -> None:
        """
        Removes the given remote volume from the EXAConf if it is not used by a database
        (this check is skipped if 'force' is True).
        """

        # sanity checks
        if remote_volume_name is None and remote_volume_id is None:
            raise EXAConfError("Either name or ID must be given when deleting a remote volume!")
        if remote_volume_name is None:
            remote_volume_name = "r%04i" % (int(remote_volume_id) - self.remote_vol_id_offset)
        vol_section = self.remote_volume_exists(remote_volume_name)
        if not vol_section:
            raise EXAConfError("Volume '%s' can't be removed because it doesn't exist!" % remote_volume_name)

        if force is False:
            usage = self.get_volume_usage(remote_volume_name)
            if usage is not None:
                raise EXAConfError("Remote volume '%s' can't be removed because it's in use!" % remote_volume_name)

        del self.config[vol_section]

        if commit:
            self.commit()

    # }}}
    # {{{ Set volume conf

    def set_volume_conf(self, vol_conf, vol_name, commit=True) -> None:
        """
        Changes the values of the given keys for the given volume
        (or all volumes). The  following options can't be changed and are
        therefore ignored: 'name', 'type', 'block_size' and 'stripe_size'.

        If 'vol_name' == '_all', the given vol_configuration is applied to all volumes.
        Take care to remove all options that should not be changed.

        If a volume with the given name does not exist, it will be added and the
        given configuration will be applied. In that case the given 'vol_conf'
        must contain all mandatory parameters for 'add_volume()'!
        """

        #add volume if it doesn't exist
        if vol_name != "_all" and not self.volume_exists(vol_name):
            self.add_volume(name = vol_conf.name,
                            vol_type = vol_conf.type,
                            size = vol_conf.size,
                            disk = vol_conf.disk,
                            redundancy = vol_conf.redundancy,
                            nodes = vol_conf.nodes,
                            owner = vol_conf.owner,
                            partition_size = vol_conf.partition_size if "partition_size" in vol_conf else None,
                            num_master_nodes = vol_conf.num_master_nodes if "num_master_nodes" in vol_conf else None,
                            permissions = vol_conf.permissions if "permissions" in vol_conf else None,
                            labels = vol_conf.labels if "labels" in vol_conf else None,
                            block_size = vol_conf.block_size if "block_size" in vol_conf else None,
                            stripe_size = vol_conf.stripe_size if "stripe_size" in vol_conf else None,
                            shared = vol_conf.shared if "shared" in vol_conf else None,
                            http_port = vol_conf.http_port if "http_port" in vol_conf else None,
                            https_port = vol_conf.https_port if "https_port" in vol_conf else None,
                            ftp_port = vol_conf.ftp_port if "ftp_port" in vol_conf else None,
                            ftps_port = vol_conf.ftps_port if "ftps_port" in vol_conf else None,
                            sftp_port = vol_conf.sftp_port if "sftp_port" in vol_conf else None,
                            commit = commit)
            # nothing to change, since the ID can't be "_all"
            return

        #change configuration for existing volumes
        volumes = self.get_volumes()
        for vol in volumes.items():
            if vol_name == "_all" or vol[0] == vol_name:
                vol_sec = self.config[vol[1]._sec_name]
                if "size" in vol_conf:
                    vol_sec["Size"] = vol_conf.size if isinstance(vol_conf.size, str) else bytes2units(vol_conf.size)
                if "disk" in vol_conf:
                    vol_sec["Disk"] = vol_conf.disk
                if "redundancy" in vol_conf:
                    vol_sec["Redundancy"] = str(vol_conf.redundancy)
                if "owner" in vol_conf:
                    vol_sec["Owner"] = str(vol_conf.owner[0]) + " : " + str(vol_conf.owner[1])
                if "permissions" in vol_conf:
                    vol_sec["Permissions"] = str(vol_conf.permissions)
                if "nodes" in vol_conf:
                    vol_sec["Nodes"] = ','.join(map(str, vol_conf.nodes))
                if "num_master_nodes" in vol_conf:
                    vol_sec["NumMasterNodes"] = str(vol_conf.num_master_nodes)
                if vol_conf.type == 'archive' and 'partition_size' in vol_conf:
                    vol_sec["PartitionSize"] = vol_conf.partition_size if isinstance(vol_conf.size, str) else bytes2units(vol_conf.partition_size)
                if "priority" in vol_conf:
                    vol_sec["Priority"] = str(vol_conf.priority)
                if "shared" in vol_conf:
                    vol_sec["Shared"] = str(vol_conf.shared)
                if "labels" in vol_conf:
                    vol_sec["Labels"] = [ str(l) for l in vol_conf.labels ]
                for port_type in ('http', 'https', 'ftp', 'ftps', 'sftp'):
                    varname, confname = '%s_port' % port_type, '%sPort' % port_type.capitalize()
                    if varname in vol_conf:
                        if vol_conf[varname] is None or int(vol_conf[varname]) == 0:
                            if confname in vol_sec:
                                del vol_sec[confname]
                        else: vol_sec[confname] = int(vol_conf[varname])
        if commit:
            self.commit()

    # }}}
    # {{{ Set volume conf

    def set_object_volume_conf(self, vol_conf, vol_name, commit=True) -> None:
        """
        Changes the values of the given keys for the given volume
        (or all volumes).

        If 'vol_name' == '_all', the given vol_configuration is applied to all volumes.
        Take care to remove all options that should not be changed.

        If a volume with the given name does not exist, it will be added and the
        given configuration will be applied. In that case the given 'vol_conf'
        must contain all mandatory parameters for 'add_object_volume()'!
        """

        #add volume if it doesn't exist
        if vol_name != "_all" and not self.volume_exists(vol_name):
            self.add_object_volume(vol_conf.name, vol_conf.type, vol_conf.bucket,
                                   vol_conf.owner, vol_conf.region,
                                   vol_conf.prefix if "prefix" in vol_conf else None,
                                   vol_conf.end_point if "end_point" in vol_conf else None,
                                   vol_conf.credentials if "credentials" in vol_conf else None,
                                   vol_conf.permissions if "permissions" in vol_conf else None,
                                   vol_conf.shared if "shared" in vol_conf else None,
                                   vol_conf.labels if "labels" in vol_conf else None,
                                   vol_conf.options if "options" in vol_conf else None,
                                   commit = commit)
            # nothing to change, since the ID can't be "_all"
            return

        #change configuration for existing volumes
        volumes = self.get_volumes()
        for vol in volumes.items():
            if vol_name == "_all" or vol[0] == vol_name:
                vol_sec = self.config[vol[1]._sec_name]
                if "bucket" in vol_conf:
                    vol_sec["Bucket"] = str(vol_conf.bucket)
                if "prefix" in vol_conf:
                    vol_sec["Prefix"] = str(vol_conf.prefix)
                if "owner" in vol_conf:
                    vol_sec["Owner"] = str(vol_conf.owner[0]) + " : " + str(vol_conf.owner[1])
                if "region" in vol_conf:
                    vol_sec["Region"] = str(vol_conf.region)
                if "end_point" in vol_conf:
                    vol_sec["EndPoint"] = str(vol_conf.end_point)
                if "credentials" in vol_conf:
                    vol_sec["Credentials"] = str(vol_conf.credentials)
                if "permissions" in vol_conf:
                    vol_sec["Permissions"] = str(vol_conf.permissions)
                if "shared" in vol_conf:
                    vol_sec["Shared"] = str(vol_conf.shared)
                if "priority" in vol_conf:
                    vol_sec["Priority"] = str(vol_conf.priority)
                if "labels" in vol_conf:
                    vol_sec["Labels"] = [ str(l) for l in vol_conf.labels ]
        if commit:
            self.commit()

    # }}}
    # {{{ Set remote volume conf

    def set_remote_volume_conf(self, vol_conf, vname) -> None:
        """
        Changes the values of the given keys for the given remote volumes
        (or all volumes). The  following options can't be changed and are
        therefore ignored: 'name' and 'type'

        If 'volume' == '_all', the given vol_confuration is applied to all
        remote volumes. Take care to remove all options that should not be
        changed.

        If a remote volume with the given ID does not exist, it will be added and the
        given configuration will be applied. In that case the given 'vol_conf'
        must contain all mandatory parameters for 'add_remote_volume()'!
        """

        #add volume if it doesn't exist
        if vname != "_all" and not self.remote_volume_exists(vname):
            self.add_remote_volume(vol_conf.name, vol_conf.type, vol_conf.url,
                                   vol_conf.username if "username" in vol_conf else None,
                                   vol_conf.password if "password" in vol_conf else None,
                                   vol_conf.labels if "labels" in vol_conf else None,
                                   vol_conf.options if "options" in vol_conf else None,
                                   vol_conf.owner if "owner" in vol_conf else None)
            # nothing to change, since the ID can't be "_all"
            return

        #change configuration for existing volumes
        volumes = self.get_remote_volumes()
        for vol in volumes.items():
            if vname == "_all" or vol[0] == vname:
                vol_sec = self.config[vol[1]._sec_name]
                if "url" in vol_conf:
                    vol_sec["URL"] = vol_conf.url
                if "username" in vol_conf:
                    vol_sec["Username"] = vol_conf.username
                if "passwd" in vol_conf:
                    vol_sec["Passwd"] = vol_conf.password
                if "labels" in vol_conf:
                    vol_sec["Labels"] = [ str(l) for l in vol_conf.labels ]
                if "options" in vol_conf:
                    vol_sec["Options"] = vol_conf.options
                if "owner" in vol_conf:
                    vol_sec["Owner"] = vol_conf.owner

        self.commit()

    # }}}

    # {{{ parse the oracle url
    @staticmethod
    def set_oracle_from_url(oracle_sec: dict, url: str) -> None:
        if not url or not url.startswith('bucketfs://'):
            raise EXAConfError(f'Given url {url} is invalid')

        bucketfs_items = url[len('bucketfs://'):].split('/', maxsplit=2)
        if not bucketfs_items or len(bucketfs_items) != 3:
            raise EXAConfError(f'Given url {url} is invalid')

        if isinstance(oracle_sec, config):
            oracle_sec.bucketfs = bucketfs_items[0]
            oracle_sec.bucket = bucketfs_items[1]
            oracle_sec.dir = bucketfs_items[2]
        else:
            oracle_sec["BucketFS"] = bucketfs_items[0]
            oracle_sec["Bucket"] = bucketfs_items[1]
            oracle_sec["Dir"] = bucketfs_items[2]
    # }}}

    # {{{ parse the jdbc urls
    @staticmethod
    def set_jdbc_from_urls(jdbc_sec: dict, urls: List[str]) -> None:
        bucketfs_items = None
        additional_urls = []
        for url in urls:
            if url.startswith('bucketfs://') and bucketfs_items is None:
                bucketfs_items = url[len('bucketfs://'):].split('/', maxsplit=2)
            else:
                additional_urls.append(url)

        if not bucketfs_items or len(bucketfs_items) != 3:
            raise EXAConfError(f'No valid url for bucketfs settings in the url list: {urls}')

        if isinstance(jdbc_sec, config):
            jdbc_sec.bucketfs = bucketfs_items[0]
            jdbc_sec.bucket = bucketfs_items[1]
            jdbc_sec.dir = bucketfs_items[2]
            jdbc_sec.additional_urls = additional_urls if additional_urls else ''
        else:
            jdbc_sec["BucketFS"] = bucketfs_items[0]
            jdbc_sec["Bucket"] = bucketfs_items[1]
            jdbc_sec["Dir"] = bucketfs_items[2]
            jdbc_sec["AdditionalURLs"] = additional_urls if additional_urls else ''

    # }}}

    # {{{ Add database

    def add_database(self, name, version, mem_size, port, owner, nodes, num_active_nodes, data_volume, *,
                     db_uuid = None,
                     cloud_data_volume = None,
                     snapshot_volume = None,
                     data_volume_sync = None,
                     snapshot_volume_sync = None,
                     params = None,
                     ldap_servers = None,
                     cache_volume_disk = None,
                     enable_auditing = None,
                     interfaces = None,
                     volume_quota = None,
                     volume_move_delay = None,
                     master_database = None,
                     auto_start = None,
                     default_sys_passwd_hash = None,
                     additional_sys_passwd_hashes = None,
                     initial_sql = None,
                     builtin_script_language_name = None,
                     jdbc_urls = None,
                     oracle_url = None,
                     commit = True) -> None:
        """
        Adds a new database to EXAConf.
        """

        if self.database_exists(name, db_uuid=db_uuid):
            raise EXAConfError("Database '%s' can't be added because it already exists!" % name)
        if master_database and name.upper() == "MAIN":
            raise EXAConfError("Worker database cluster cannot be named 'MAIN'")
        # check if given owner exists
        if not self.uid_exists(owner[0]):
            raise EXAConfError("Can't add database '%s' because owner with UID %i doesn't exist!\n" % (name, owner[0]))
        if not self.gid_exists(owner[1]):
            raise EXAConfError("Can't add database '%s' because owner group with GID %i doesn't exist!\n" % (name, owner[1]))
        if db_uuid is not None:
            if not is_valid_b64_uuid(db_uuid):
                raise EXAConfError(f"Database '{name}' can't be added because uuid {db_uuid} is invalid!")
        elif is_valid_b64_uuid(name):
            # This is a transitionary workaround to ensure that the uuids
            # for databases added by the SaaS UI match their names until the
            # UI is updated to use the new API.
            db_uuid = name
        else:
            db_uuid = gen_b64_uuid()

        db_sec_name = "DB : " + str(name)
        self.config[db_sec_name] = {}
        db_sec = self.config[db_sec_name]
        db_sec["ID"] = str(db_uuid)
        db_sec["Version"] = version
        db_sec["MemSize"] = mem_size
        db_sec["Port"] = port
        db_sec["Owner"] = str(owner[0]) + " : " + str(owner[1])
        db_sec["Nodes"] = ','.join(map(str, nodes))
        db_sec["NumActiveNodes"] = num_active_nodes
        db_sec["DataVolume"] = data_volume
        # optional values
        if cloud_data_volume:
            db_sec["CloudDataVolume"] = cloud_data_volume
        if snapshot_volume:
            db_sec["SnapshotVolume"] = snapshot_volume
        if data_volume_sync:
            db_sec["CloudDataVolumeSync"] = data_volume_sync
        if snapshot_volume_sync:
            db_sec["SnapshotVolumeSync"] = snapshot_volume_sync
        if params:
            db_sec["Params"] = params
        if ldap_servers:
            db_sec["LdapServers"] = ldap_servers
        if builtin_script_language_name:
            db_sec["BuiltinScriptLanguageName"] = builtin_script_language_name
        if cache_volume_disk:
            cache_disk_name, cache_volume_size = cache_volume_disk
            db_sec["CacheVolumeDisk"] = f'{cache_disk_name.strip()}:{bytes2units(cache_volume_size)}'
            db_sec.comments["CacheVolumeDisk"] = ["Specify disks to use for cache: <diskname>:<volumesize>"]
        if enable_auditing is not None:
            db_sec["EnableAuditing"] = str(enable_auditing)
        if interfaces:
            db_sec["Interfaces"] = interfaces
        if volume_quota:
            db_sec["VolumeQuota"] = bytes2units(volume_quota)
        if volume_move_delay:
            db_sec["VolumeMoveDelay"] = volume_move_delay
        if master_database:
            db_sec["MasterDatabase"] = master_database
        else:
            if auto_start is not None:
                db_sec["AutoStart"] = auto_start
            if initial_sql:
                db_sec["InitialSQL"] = initial_sql
            if default_sys_passwd_hash:
                db_sec["DefaultSysPasswdHash"] = default_sys_passwd_hash
            if additional_sys_passwd_hashes:
                db_sec["AdditionalSysPasswdHashes"] = additional_sys_passwd_hashes

        # comments
        self.config.comments[db_sec_name] = ["\n", "An EXASOL database"]
        db_sec.comments["ID"] = ["Unique id of this database."]
        db_sec.comments["Version"] = ["Version nr. of this database."]
        db_sec.comments["Owner"] = ["User and group IDs that own this database (e. g. '1000:1005')."]
        db_sec.comments["MemSize"] = ["Memory size over all nodes (e. g. '1 TiB')."]
        db_sec.comments["Nodes"] = ["Comma-separated list of node IDs for this DB (put reserve nodes at the end, if any)."]
        db_sec.comments["NumActiveNodes"] = ["Nr. of initially active nodes for this DB. The remaining nodes will be reserve nodes."]
        db_sec.comments["DataVolume"] = ["Name of the data volume to be used by this database."]
        db_sec.comments["CloudDataVolume"] = ["OPTIONAL: Name of the volume to be used by this database in cloud storage."]
        db_sec.comments["AutoStart"] = ["Start database automatically on cluster boot."]
        db_sec.comments["InitialSQL"] = ["OPTIONAL: A YAML file with SQLs, which should be executed on database initialisation."]
        db_sec.comments["DefaultSysPasswdHash"] = ["OPTIONAL: Default password for user SYS as hash (can be generated with exaconf)."]
        db_sec.comments["AdditionalSysPasswdHashes"] = ["OPTIONAL: Additional passwords for user SYS as hashes (can be generated with exaconf)."]
        db_sec.comments["Params"] = ["OPTIONAL: DB parameters"]
        db_sec["JDBC"] = {}
        jdbc_sec = db_sec["JDBC"]
        if not jdbc_urls:
            # default JDBC sub-section
            jdbc_sec["BucketFS"] = self.def_bucketfs
            jdbc_sec["Bucket"] = self.def_bucket
            jdbc_sec["Dir"] = self.def_jdbc_driver_dir
            jdbc_sec["AdditionalURLs"] = ''
        else:
            EXAConf.set_jdbc_from_urls(jdbc_sec, jdbc_urls)

        db_sec.comments["JDBC"] = ["JDBC driver configuration"]
        jdbc_sec.comments["BucketFS"] = ["BucketFS that contains the JDBC driver"]
        jdbc_sec.comments["Bucket"] = ["Bucket that contains the JDBC driver"]
        jdbc_sec.comments["Dir"] = ["Directory within the bucket that contains the drivers"]
        jdbc_sec.comments["AdditionalURLs"] = ["Additional URLs used to search for JDBC drivers, like: bucketfs://bfs2/bucket3/jdbcdir or file:///exa/etc/jdbc_drivers."]
        # default Oracle sub-section
        db_sec["Oracle"] = {}
        if not oracle_url:
            oracle_sec = db_sec["Oracle"]
            oracle_sec["BucketFS"] = self.def_bucketfs
            oracle_sec["Bucket"] = self.def_bucket
            oracle_sec["Dir"] = self.def_oracle_driver_dir
        else:
            oracle_sec = db_sec["Oracle"]
            EXAConf.set_oracle_from_url(oracle_sec, oracle_url)

        db_sec.comments["Oracle"] = ["Oracle driver configuration"]
        oracle_sec.comments["BucketFS"] = ["BucketFS that contains the JDBC drivers"]
        oracle_sec.comments["Bucket"] = ["Bucket that contains the JDBC drivers"]
        oracle_sec.comments["Dir"] = ["Directory within the bucket that contains the drivers"]

        if commit:
            self.commit()

    # }}}
    # {{{ Remove database

    def remove_database(self, name, commit = True) -> None:
        """
        Remove the given database from EXAConf.
        """

        db_sec = self.database_exists(name)
        if not db_sec:
            raise EXAConfError("Database '%s' can't be removed because it doesn't exist!" % name)

        del self.config[db_sec]

        if commit:
            self.commit()

    # }}}
    # {{{ Set database conf

    def set_database_conf(self, db_conf, db_name: str, commit: bool = True) -> None:
        """
        Changes the values of the given keys for the given database
        (or all databases). The name can't be changed and is therefore
        ignored. Subsections like 'JDBC' must be complete in 'db_conf'
        (single options can't be changed).

        If 'db_name' == '_all', the given db_configuration is applied to all databases.
        Take care to remove all options that should not be changed.

        If a database with the given name does not exist, it will be added and the
        given configuration will be applied. In that case the given 'db_conf'
        must contain all mandatory parameters for 'add_database()'!
        """

        # add volume if it doesn't exist
        if db_name != "_all" and not self.database_exists(db_name):
            self.add_database(name = db_name, version = db_conf.version, mem_size = db_conf.mem_size,
                              port = db_conf.port, owner = db_conf.owner, nodes = db_conf.nodes,
                              num_active_nodes = db_conf.num_active_nodes, data_volume = db_conf.data_volume,
                              cloud_data_volume = db_conf.cloud_data_volume if "cloud_data_volume" in db_conf else None,
                              snapshot_volume = db_conf.snapshot_volume if "snapshot_volume" in db_conf else None,
                              data_volume_sync = db_conf.data_volume_sync if "data_volume_sync" in db_conf else None,
                              snapshot_volume_sync = db_conf.snapshot_volume_sync if "snapshot_volume_sync" in db_conf else None,
                              params = db_conf.params if "params" in db_conf else None,
                              ldap_servers = db_conf.ldap_servers if "ldap_servers" in db_conf else None,
                              builtin_script_language_name = db_conf.builtin_script_language_name if "builtin_script_language_name" in db_conf else None,
                              initial_sql = db_conf.initial_sql if "initial_sql" in db_conf else None,
                              auto_start = db_conf.auto_start if "auto_start" in db_conf else None,
                              default_sys_passwd_hash = db_conf.default_sys_passwd_hash if "default_sys_passwd_hash" in db_conf else None,
                              additional_sys_passwd_hashes = db_conf.additional_sys_passwd_hashes if "additional_sys_passwd_hashes" in db_conf else None,
                              cache_volume_disk = db_conf.cache_volume_disk if "cache_volume_disk" in db_conf else None,
                              enable_auditing = db_conf.enable_auditing if "enable_auditing" in db_conf else None,
                              interfaces = db_conf.interfaces if "interfaces" in db_conf else None,
                              volume_quota = db_conf.volume_quota if "volume_quota" in db_conf else None,
                              volume_move_delay = db_conf.volume_move_delay if "volume_move_delay" in db_conf else None,
                              master_database = db_conf.master_database if "master_database" in db_conf else None)
            # nothing to change, since the ID can't be "_all"
            return

        dbs = self.get_databases()
        for db in dbs.items():
            if db_name == "_all" or db[0] == db_name:
                db_sec = self.config[db[1]._sec_name]
                if "version" in db_conf:
                    db_sec["Version"] = db_conf.version
                if "data_volume" in db_conf:
                    db_sec["DataVolume"] = db_conf.data_volume
                if "cloud_data_volume" in db_conf:
                    db_sec["CloudDataVolume"] = db_conf.cloud_data_volume
                if "mem_size" in db_conf:
                    db_sec["MemSize"] = bytes2units(db_conf.mem_size * 1048576) #given in MiB
                if "port" in db_conf:
                    db_sec["Port"] = db_conf.port
                if "nodes" in db_conf:
                    db_sec["Nodes"] = ','.join(map(str, db_conf.nodes))
                if "num_active_nodes" in db_conf:
                    db_sec["NumActiveNodes"] = db_conf.num_active_nodes
                if "owner" in db_conf:
                    db_sec["Owner"] = str(db_conf.owner[0]) + " : " + str(db_conf.owner[1])
                if "params" in db_conf:
                    db_sec["Params"] = db_conf.params
                if "ldap_servers" in db_conf:
                    db_sec["LdapServers"] = db_conf.ldap_servers
                if "builtin_script_language_name" in db_conf:
                    db_sec["BuiltinScriptLanguageName"] = db_conf.builtin_script_language_name
                if "cache_volume_disk" in db_conf:
                    cache_disk_name, cache_volume_size = db_conf.cache_volume_disk
                    db_sec["CacheVolumeDisk"] = '%s:%s' % (cache_disk_name, bytes2units(cache_volume_size))
                if "enable_auditing" in db_conf:
                    db_sec["EnableAuditing"] = str(db_conf.enable_auditing)
                if "interfaces" in db_conf:
                    db_sec["Interfaces"] = db_conf.interfaces
                if "volume_quota" in db_conf:
                    db_sec["VolumeQuota"] = bytes2units(db_conf.volume_quota)
                if "volume_move_delay" in db_conf:
                    db_sec["VolumeMoveDelay"] = db_conf.volume_move_delay
                if "auto_start" in db_conf:
                    db_sec["AutoStart"] = db_conf.auto_start
                if "initial_sql" in db_conf:
                    db_sec["InitialSQL"] = db_conf.initial_sql
                if "default_sys_passwd_hash" in db_conf:
                    db_sec["DefaultSysPasswdHash"] = db_conf.default_sys_passwd_hash
                if "additional_sys_passwd_hashes" in db_conf:
                    db_sec["AdditionalSysPasswdHashes"] = db_conf.additional_sys_passwd_hashes
                if "master_database" in db_conf:
                    db_sec["MasterDatabase"] = db_conf.master_database
                if "jdbc" in db_conf:
                    if "JDBC" in db_sec.sections:
                        db_sec["JDBC"].update(db_conf.jdbc.to_section())
                    else:
                        db_sec["JDBC"] = db_conf.jdbc.to_section()
                if "oracle" in db_conf:
                    if "Oracle" in db_sec.sections:
                        db_sec["Oracle"].update(db_conf.oracle.to_section())
                    else:
                        db_sec["Oracle"] = db_conf.oracle.to_section()

        if commit:
            self.commit()

    # }}}
    # {{{ Add backup schedule

    def add_backup_schedule(self, db_name, backup_name, volume, level, minute, hour, day, month, weekday,
                            expire = 0, enabled = True, commit = True) -> None:
        """
        Add a new backup schedule to the given database.
        """

        db_sec_name = self.database_exists(db_name)
        if not db_sec_name:
            raise EXAConfError("Database '%s' does not exist!" % db_name)

        db_sec = self.config[db_sec_name]
        ba_sec_name = self.db_backup_exists(db_name, backup_name)
        if ba_sec_name is not None:
            raise EXAConfError("Backup '%s' already exists in database '%s'!" % (backup_name, db_name))
        ba_sec_name = 'Backup : ' + backup_name

        db_sec[ba_sec_name] = {}
        ba_sec = db_sec[ba_sec_name]
        ba_sec['Enabled'] = str(enabled)
        ba_sec['Volume'] = str(volume)
        ba_sec['Level'] = str(level)
        ba_sec['Minute'] = str(minute)
        ba_sec['Hour'] = str(hour)
        ba_sec['Day'] = str(day)
        ba_sec['Month'] = str(month)
        ba_sec['Weekday'] = str(weekday)
        ba_sec['Expire'] = str(expire)

        if commit:
            self.commit()

    # }}}
    # {{{ Remove backup schedule

    def remove_backup_schedule(self, db_name, backup_name, commit = True) -> None:
        """
        Remove an existing backup schedule from the given database.
        """

        db_sec_name = self.database_exists(db_name)
        if not db_sec_name:
            raise EXAConfError("Database '%s' does not exist!" % db_name)

        db_sec = self.config[db_sec_name]
        ba_sec_name = self.db_backup_exists(db_name, backup_name)
        if ba_sec_name is None:
            raise EXAConfError("Backup '%s' does not exist in database '%s'!" % (backup_name, db_name))

        del db_sec[ba_sec_name]

        if commit:
            self.commit()

    # }}}
    # {{{ Set backup schedule conf

    def set_backup_schedule_conf(self, backup_conf, db_name, backup_name, commit = True) -> None:
        """
        Changes the values of the given keys in the given backup section.

        If 'backup_name' == '_all', the given backup_configuration is applied to all backup
        schedules of the given database. Take care to remove all options that should not be changed.

        If a backup schedule with the given name does not exist, it will be added and the
        given configuration will be applied. In that case the given 'backup_conf'
        must contain all mandatory parameters for 'add_backup_schedule()'!
        """

        db_sec_name = self.database_exists(db_name)
        if not db_sec_name:
            raise EXAConfError("Database '%s' does not exist!" % db_name)

        if backup_name != '_all':
            ba_sec_name = self.db_backup_exists(db_name, backup_name)
        else: ba_sec_name = None
        if backup_name != "_all" and not ba_sec_name:
            self.add_backup_schedule(db_name = db_name,
                                     backup_name = backup_name,
                                     volume = backup_conf.volume,
                                     level = backup_conf.level,
                                     minute = backup_conf.minute,
                                     hour = backup_conf.hour,
                                     day = backup_conf.day,
                                     month = backup_conf.month,
                                     weekday = backup_conf.weekday,
                                     expire = backup_conf.expire,
                                     enabled = backup_conf.enabled,
                                    commit = commit)
            return

        db_sec = self.config[db_sec_name]
        for section in db_sec.sections:
            if backup_name == '_all' or section == ba_sec_name:
                ba_sec = db_sec[section]
                ba_sec.update(backup_conf.to_section())

        if commit is True:
            self.commit()

    # }}}
    # {{{ Add snapshot backup schedule

    def add_snapshot_backup_schedule(self, db_name, backup_name, minute, hour, day, month, weekday,
                            expire = 0, enabled = True, commit = True) -> None:
        """
        Add a new snapshot backup schedule to the given database.
        """

        db_sec_name = self.database_exists(db_name)
        if not db_sec_name:
            raise EXAConfError("Database '%s' does not exist!" % db_name)

        db_sec = self.config[db_sec_name]
        ba_sec_name = self.db_snapshot_backup_exists(db_name, backup_name)
        if ba_sec_name is not None:
            raise EXAConfError("Snapshot Backup '%s' already exists in database '%s'!" % (backup_name, db_name))
        ba_sec_name = 'Snapshot : ' + backup_name

        db_sec[ba_sec_name] = {}
        ba_sec = db_sec[ba_sec_name]
        ba_sec['Enabled'] = str(enabled)
        ba_sec['Minute'] = str(minute)
        ba_sec['Hour'] = str(hour)
        ba_sec['Day'] = str(day)
        ba_sec['Month'] = str(month)
        ba_sec['Weekday'] = str(weekday)
        ba_sec['Expire'] = str(expire)

        if commit:
            self.commit()
    # }}}
    # {{{ Remove snapshot backup schedule

    def remove_snapshot_backup_schedule(self, db_name, backup_name, commit = True) -> None:
        """
        Remove an existing snapshot backup schedule from the given database.
        """

        db_sec_name = self.database_exists(db_name)
        if not db_sec_name:
            raise EXAConfError("Database '%s' does not exist!" % db_name)

        db_sec = self.config[db_sec_name]
        ba_sec_name = self.db_snapshot_backup_exists(db_name, backup_name)
        if ba_sec_name is None:
            raise EXAConfError("Snapshot backup '%s' does not exist in database '%s'!" % (backup_name, db_name))

        del db_sec[ba_sec_name]

        if commit:
            self.commit()

    # }}}
    # {{{ Set snapshot backup schedule conf

    def set_snapshot_backup_schedule_conf(self, backup_conf, db_name, backup_name, commit = True) -> None:
        """
        Changes the values of the given keys in the given snapshot backup section.

        If 'snapshot_backup_name' == '_all', the given snapshot backup_configuration is applied to all snapshot backup
        schedules of the given database. Take care to remove all options that should not be changed.

        If a snapshot backup schedule with the given name does not exist, it will be added and the
        given configuration will be applied. In that case the given 'backup_conf'
        must contain all mandatory parameters for 'add_snapshot_backup_schedule()'!
        """

        db_sec_name = self.database_exists(db_name)
        if not db_sec_name:
            raise EXAConfError("Database '%s' does not exist!" % db_name)

        if backup_name != '_all':
            ba_sec_name = self.db_snapshot_backup_exists(db_name, backup_name)
        else: ba_sec_name = None
        if backup_name != "_all" and not ba_sec_name:
            self.add_snapshot_backup_schedule(db_name = db_name,
                                     backup_name = backup_name,
                                     minute = backup_conf.minute,
                                     hour = backup_conf.hour,
                                     day = backup_conf.day,
                                     month = backup_conf.month,
                                     weekday = backup_conf.weekday,
                                     expire = backup_conf.expire,
                                     enabled = backup_conf.enabled,
                                    commit = commit)
            return

        db_sec = self.config[db_sec_name]
        for section in db_sec.sections:
            if backup_name == '_all' or section == ba_sec_name:
                ba_sec = db_sec[section]
                ba_sec.update(backup_conf.to_section())

        if commit is True:
            self.commit()

    # }}}
    # {{{ Add BucketFS

    def add_bucketfs(self, bucketfs_name, owner,
                     http_port, https_port,
                     mode = 'rsync',
                     sync_key = None, sync_period = None,
                     path = None,
                     bucketvolume = None,
                     commit = True) -> None:
        """
        Adds a new BucketFS with given bucketfs_name and parameters.
        """
        if self.bucketfs_exists(bucketfs_name):
            raise EXAConfError("BucketFS '%s' can't be added because that name is already in use!" % bucketfs_name)
        # check if given owner exists
        if not self.uid_exists(owner[0]):
            raise EXAConfError("Can't add BucketFS '%s' because owner with UID %i doesn't exist!\n" % (bucketfs_name, owner[0]))
        if not self.gid_exists(owner[1]):
            raise EXAConfError("Can't add BucketFS '%s' because owner group with GID %i doesn't exist!\n" % (bucketfs_name, owner[1]))

        self.config["BucketFS : %s" % bucketfs_name] = {}
        bfs_sec = self.config["BucketFS : %s" % bucketfs_name]
        bfs_sec["Owner"] = str(owner[0]) + " : " + str(owner[1])
        bfs_sec["HttpPort"] = http_port
        bfs_sec["HttpsPort"] = https_port
        bfs_sec["SyncKey"] = encode_bfs_passwd(sync_key) if sync_key else gen_bfs_passwd()
        bfs_sec["SyncPeriod"] = sync_period if sync_period else str(self.def_bucketfs_sync_period)
        bfs_sec["mode"] = mode
        bfs_sec["bucketvolume"] = bucketvolume
        if path is not None:
            bfs_sec["Path"] = path

        # comments
        self.config.comments["BucketFS : %s" % self.def_bucketfs] = ["\n","The default BucketFS (auto-generated)"]
        bfs_sec.comments["HttpPort"] = ["HTTP port number (0 = disabled)"]
        bfs_sec.comments["HttpsPort"] = ["HTTPS port number (0 = disabled)"]
        if path is not None:
            bfs_sec.comments["Path"] = ["OPTIONAL: path to this BucketFS (default: %s)" % os.path.join(self.container_root, self.bucketfs_dir)]

        if commit is True:
            self.commit()

    # }}}
    # {{{ Set BucketFS conf

    def set_bucketfs_conf(self, bfs_conf, bucketfs_name, commit = True) -> None:
        """
        Changes the values of the given keys in the given BucketFS section.

        If 'bucketfs_name' == '_all', the given bfs_configuration is applied to all BucketFSs.
        Take care to remove all options that should not be changed.

        If a BucketFS with the given name does not exist, it will be added and the
        given configuration will be applied. In that case the given 'bfs_conf'
        must contain all mandatory parameters for 'add_bucketfs()'!
        """

        if bucketfs_name != "_all" and not self.bucketfs_exists(bucketfs_name):
            self.add_bucketfs(bucketfs_name = bucketfs_name,
                              owner = bfs_conf.owner,
                              http_port = bfs_conf.http_port,
                              https_port = bfs_conf.https_port,
                              mode = bfs_conf.mode,
                              sync_key = bfs_conf.sync_key if "sync_key" in bfs_conf else None,
                              sync_period = bfs_conf.sync_perion if "sync_period" in bfs_conf else None,
                              path = bfs_conf.path if "path" in bfs_conf else None,
                              bucketvolume = bfs_conf.bucketvolume,# if "bucketvolume" in bfs_conf else None,
                              commit = commit)
            return

        bucketfs_conf = self.get_bucketfs()
        for bfs in bucketfs_conf.values():
            if bfs.name == bucketfs_name or bucketfs_name == "_all":
                bfs_sec = self.config[bfs._sec_name]
                bfs_sec.update(bfs_conf.to_section(ignore_keys = ['bfs_name']))

        if commit is True:
            self.commit()

    # }}}
    # {{{ Remove BucketFS

    def remove_bucketfs(self, bucketfs_name, commit = True) -> None:
        """
        Remove the given BucketFS from EXAConf.
        """
        bfs_sec_name = self.bucketfs_exists(bucketfs_name)
        if not bfs_sec_name:
            raise EXAConfError("BucketFS '%s' can't be removed because it doesn't exist!" % bucketfs_name)

        del self.config[bfs_sec_name]

        if commit is True:
            self.commit()

    # }}}
    # {{{ Add Bucket

    def add_bucket(self, bucket_name, bucketfs_name, public,
                   read_password = None, write_password = None,
                   additional_files = None, commit = True) -> None:
        """
        Adds a new Bucket to the given BucketFS.
        """
        if not self.bucketfs_exists(bucketfs_name):
            raise EXAConfError("Bucket '%s' can't be added because BucketFS '%s' does not exist!" % (bucket_name, bucketfs_name))
        if self.bucket_exists(bucket_name, bucketfs_name):
            raise EXAConfError("Bucket '%s' already exists in BucketFS '%s'!" % (bucket_name, bucketfs_name))
        bfs_sec = self.config["BucketFS : %s" % bucketfs_name]
        bfs_sec["Bucket : %s" % bucket_name] = {}
        b_sec = bfs_sec["Bucket : %s" % bucket_name]
        b_sec["Public"] = str(public)
        b_sec["ReadPasswd"] = encode_bfs_passwd(read_password) if read_password else gen_bfs_passwd()
        b_sec["WritePasswd"] = encode_bfs_passwd(write_password) if write_password else gen_bfs_passwd()
        if additional_files:
            b_sec["AdditionalFiles"] = ",".join(additional_files)


        if commit is True:
            self.commit()

    # }}}
    # {{{ Set Bucket conf

    def set_bucket_conf(self, b_conf, bucket_name, bucketfs_name, commit = True) -> None:
        """
        Changes the values of the given keys in the given Bucket section.

        If 'bucket_name' == '_all', the given bucket_configuration is applied to all Buckets
        of the given BucketFS. Take care to remove all options that should not be changed.

        If a Bucket with the given name does not exist in the given BucketFS, it will be
        added and the  given configuration will be applied. In that case the given 'b_conf'
        must contain all mandatory parameters for 'add_bucket()'!
        """

        if not self.bucketfs_exists(bucketfs_name):
            raise EXAConfError("Bucket '%s' can't be modified because BucketFS '%s' does not exist!" % (bucket_name, bucketfs_name))
        if bucket_name != "_all" and not self.bucket_exists(bucket_name, bucketfs_name):
            self.add_bucket(bucket_name = bucket_name, bucketfs_name = bucketfs_name,
                            public = b_conf.public,
                            read_password = b_conf.read_passwd if 'read_passwd' in b_conf else None,
                            write_password = b_conf.write_passwd if 'write_passwd' in b_conf else None,
                            additional_files = b_conf.additional_files if 'additional_files' in b_conf else None,
                            commit = commit)
            return

        bucketfs_conf = self.get_bucketfs()
        for bfs in bucketfs_conf.values():
            if bfs.name == bucketfs_name:
                for b in bfs.buckets.values():
                    if b.name == bucket_name or bucket_name == "_all":
                        b_sec = self.config[bfs._sec_name][b._sec_name]
                        b_sec.update(b_conf.to_section(ignore_keys = ['bucket_name']))
                        # make sure that passwords are always encoded (SPOT-15477)
                        b_sec['ReadPasswd'] = encode_bfs_passwd(b_sec['ReadPasswd'])
                        b_sec['WritePasswd'] = encode_bfs_passwd(b_sec['WritePasswd'])

        if commit is True:
            self.commit()

    # }}}
    # {{{ Remove Bucket

    def remove_bucket(self, bucket_name, bucketfs_name, commit = True) -> None:
        """
        Remove the given Bucket from the given BucketFS.
        """
        bfs_sec_name = self.bucketfs_exists(bucketfs_name)
        if not bfs_sec_name:
            raise EXAConfError("Bucket '%s' can't be removed because BucketFS '%s' it doesn't exist!" % (bucket_name, bucketfs_name))
        bucket_sec_name = self.bucket_exists(bucket_name, bucketfs_name)
        if not bucket_sec_name:
            raise EXAConfError("Bucket '%s' does not exist in BucketFS '%s'!" % (bucket_name, bucketfs_name))
        bfs_sec = self.config[bfs_sec_name]
        del bfs_sec[bucket_sec_name]

        if commit is True:
            self.commit()

    # }}}

    # {{{ Add user

    def add_user(self, *, username, userid, group, login_enabled,
                 password = None, encode_passwd = False,
                 additional_groups = None, authorized_keys = None,
                 commit = True) -> None:
        """
        Add the given user to EXAConf. If 'encode_password' is True, the given password string
        will be encoded as an /etc/shadow compatible SHA512 hash (otherwise it will be automatically
        encoded if it's not in an /etc/shadow compatible format).
        """

        if not isinstance(userid, int):
            raise EXAConfError("UID must be an integer!")
        if self.user_exists(username):
            raise EXAConfError("User '%s' can't be added because a user with that name already exists (or name is reserved)!" % username)
        if self.uid_exists(userid):
            raise EXAConfError("User '%s' can't be added because a user with ID %i already exists (or ID is reserved)!" % (username, int(userid)))
        group = self.to_gname(group)
        if not self.group_exists(group):
            raise EXAConfError("Main group '%s' of user '%s' does not exist!" % (group, username))
        # create user section
        if "Users" not in self.config.sections:
            self.config["Users"] = {}
        users_sec = self.config["Users"]
        users_sec[username] = {}
        user_sec = users_sec[username]
        user_sec["ID"] = str(userid)
        user_sec["Group"] = group
        user_sec["LoginEnabled"] = login_enabled
        if password is not None:
            if encode_passwd is True or is_shadow_encoded(password) is False:
                user_sec["Passwd"] = encode_shadow_passwd(password)
            else:
                user_sec["Passwd"] = password
        #optional parameters
        if additional_groups:
            user_sec["AdditionalGroups"] = [ str(g) for g in additional_groups ]
        if authorized_keys:
            user_sec["AuthorizedKeys"] = [ str(k) for k in authorized_keys ]

        #comments
        self.config.comments["Users"] = ["\n"]

        if commit:
            self.commit()

    # }}}
    # {{{ Set user conf

    def set_user_conf(self, user_conf, username, encode_passwd = False, extend_groups = False, extend_keys = False) -> None:
        """
        Changes the values of the given keys for the given user (or all users).
        The username and ID can't be changed.  If 'encode_passwd' is True, the
        password will be encoded as an /etc/shadow compatible SHA512 hash
        (otherwise it will be automatically encoded if it's not in an
        /etc/shadow compatible format).

        If 'username' == '_all', the given user_conf is applied to all users.
        Take care to remove all options that should not be changed.

        If a user with the given name does not exist, it will be added and the
        given configuration will be applied. In that case the given 'user_conf'
        must contain all mandatory parameters for 'add_user()'!
        """
        if username != "_all" and not self.user_exists(username):
            self.add_user(username = username, userid = user_conf.id,
                          group = user_conf.group, login_enabled = user_conf.login_enabled,
                          password = user_conf.passwd if "passwd" in user_conf else None,
                          encode_passwd = encode_passwd,
                          additional_groups = user_conf.additional_groups if "additional_groups" in user_conf else None,
                          authorized_keys = user_conf.authorized_keys if "authorized_keys" in user_conf else None)
            return
        # change configuration for existing users
        users = self.get_users()
        for user in users.items():
            if username == "_all" or user[0] == username:
                # add missing config options in order to create a complete user entry
                user_conf.update({k:v for k,v in self.get_users()[username].items() if k not in user_conf})
                user_sec = self.config["Users"][username]
                # encode password and merge groups right in the user_conf
                if "passwd" in user_conf and len(user_conf.passwd) > 0:
                    if encode_passwd is True or is_shadow_encoded(user_conf.passwd) is False:
                        user_conf.passwd = encode_shadow_passwd(user_conf.passwd)
                if "additional_groups" in user_conf and len(user_conf.additional_groups) > 0:
                    if extend_groups and "additional_groups" in user[1]:
                        # make the items unique
                        user_conf.additional_groups = list(set(user[1].additional_groups + user_conf.additional_groups))
                if "authorized_keys" in user_conf and len(user_conf.authorized_keys) > 0:
                    if extend_keys and "authorized_keys" in user[1]:
                        user_conf.authorized_keys = list(set(user[1].authorized_keys + user_conf.authorized_keys))
                user_sec.update(user_conf.to_section())
        self.commit()

    # }}}
    # {{{ Remove user

    def remove_user(self, username, commit = True) -> None:
        """
        Removes the given user from EXAConf.
        """

        if not self.user_exists(username):
            raise EXAConfError("User '%s' can't be removed because he doesn't eixist!" % username)

        del self.config["Users"][username]

        if commit:
            self.commit()

    # }}}

    # {{{ Add group

    def add_group(self, *, groupname, groupid,
                  commit = True) -> None:
        """
        Add the given group to EXAConf.
        """

        if not isinstance(groupid, int):
            raise EXAConfError("GID must be an integer!")
        if self.group_exists(groupname):
            raise EXAConfError("Group '%s' can't be added because a group with that name already exists (or name is reserved)!" % groupname)
        if self.gid_exists(groupid):
            raise EXAConfError("Group '%s' can't be added because a group with ID %i already exists (or ID is reserved)!" % (groupname, int(groupid)))
        # create group section
        if "Groups" not in self.config.sections:
            self.config["Groups"] = {}
        groups_sec = self.config["Groups"]
        groups_sec[groupname] = {}
        group_sec = groups_sec[groupname]
        group_sec["ID"] = str(groupid)

        #comments
        self.config.comments["Groups"] = ["\n"]

        if commit:
            self.commit()

    # }}}
    # {{{ Set group conf

    def set_group_conf(self, group_conf, group_name) -> None:
        """
        Changes the values of the given keys for the given group (or all groups).
        The groupname can't be changed.

        If 'group_name' == '_all', the given group_conf is applied to all groups.
        Take care to remove all options that should not be changed.

        If a group with the given name does not exist, it will be added and the
        given configuration will be applied. In that case the given 'group_conf'
        must contain all mandatory parameters for 'add_group()'!
        """

        if group_name != "_all" and not self.group_exists(group_name):
            self.add_group(groupname = group_name, groupid = group_conf.id)
            return

        # change configuration for existing groups
        groups = self.get_groups()
        for group in groups:
            if group_name == "_all" or group == group_name:
                group_sec = self.config["Groups"][group_name]
                group_sec.update(group_conf.to_section())
        self.commit()

    # }}}
    # {{{ Remove group

    def remove_group(self, groupname, commit = True) -> None:
        """
        Removes the given group from EXAConf.
        """

        if not self.group_exists(groupname):
            raise EXAConfError("Group '%s' can't be removed because it doesn't eixist!" % groupname)

        del self.config["Groups"][groupname]

        if commit:
            self.commit()

    # }}}

    # {{{ Add missing users and groups

    def add_missing_users_and_groups(self) -> None:
        """
        Checks all existing owners and adds corresponding users and groups if they are missing.
        Also adds the default users and groups.
        """
        # find the first existing owner and use it as default UID / GID
        def_owner = None
        for db in self.get_databases().values():
            if "owner" in db:
                def_owner = db.owner
                break
        # add default users and groups
        self.add_default_groups(exadefgid = def_owner[1] if def_owner is not None else None)
        self.add_default_users(exadefuid = def_owner[0] if def_owner is not None else None)
        # now check for missing owners
        suffixes = [1,1]
        def check_create_missing(owner, name):
            uid, gid = owner[0], owner[1]
            uname = gname = None
            if not self.gid_exists(gid):
                gname = "exausers" + str(suffixes[1])
                suffixes[1] += 1
                logging.info("Adding group '%s' with GID '%i' (owner of '%s')." % (gname, gid, name))
                self.add_group(groupname=gname, groupid=gid, commit=False)
            if not self.uid_exists(uid):
                uname = "exadefusr" + str(suffixes[0])
                suffixes[0] += 1
                logging.info("Adding user '%s' with UID '%i' (owner of '%s')." % (uname, uid, name))
                self.add_user(username=uname, userid=uid,
                              group=gname if gname else "exausers",
                              login_enabled=False, commit=False)
        # databases
        for dbname, dbconf in self.get_databases().items():
            check_create_missing(dbconf.owner, dbname)
        # volumes
        for volname, volconf in self.get_volumes().items():
            check_create_missing(volconf.owner, volname)
        # remote volumes
        for volname, volconf in self.get_remote_volumes().items():
            check_create_missing(volconf.owner, volname)

    # }}}
    # {{{ Add default users

    def add_default_users(self, exadefuid=None) -> None:
        """
        Adds the default user entries to EXAConf. You can specify the UID of the default user with 'exadefuid'.

        Three users will be created:

        - 'exadefusr' : UID of the current user the given one. Owns all default objects in EXAConf
          (databases, BucketFS, volumes).
        - 'root' : UID 0 and GID 0
        - 'exasaasusr' : executes all SaaS jobs (required because they're using a different authentication)
        """

        if exadefuid is None:
            exadefuid = get_euid()
        # if exadefuid is 0, we pick the default value, otherwise
        # it will be impossible to create 'exadefuser'
        if exadefuid == 0:
            exadefuid = self.def_users_start_id
        exagroups = [ g for g in self.get_groups() if g.startswith("exa") ]
        # add root user FIRST
        if not self.uid_exists(0):
            self.add_user(username = "root", userid = 0, group = "root", login_enabled = True,
                          additional_groups = exagroups)
        # add either current user or the given one as default user and make him a member of all 'exa' groups
        if not self.uid_exists(exadefuid):
            self.add_user(username="exadefusr", userid = exadefuid, group = "exausers",
                          login_enabled = False,
                          additional_groups = [ g for g in exagroups if g != "exausers" ])
        # create the default users either below 'exadefuid' or below
        # 'def_users_start_id', this keeps the range above free for outside usage
        users_start_id = max(exadefuid, self.def_users_start_id)
        self.add_user(username="exasaasusr",
                      userid = users_start_id-1,
                      group = "exausers",
                      login_enabled = False,
                      additional_groups = ["exasaasadm"])

    # }}}
    # {{{ Add default groups

    def add_default_groups(self, exadefgid=None) -> None:
        """
        Adds the default group entries to EXAConf. You can specify the GID of the 'exausers' group with 'exadefgid'.
        """

        if exadefgid is None:
            exadefgid = get_egid()
        # if exadefgid is 0, we pick the default value, otherwise
        # it will be impossible to create 'exausers'
        if exadefgid == 0:
            exadefgid = self.def_groups_start_id
        if not self.gid_exists(0):
            self.add_group(groupname = "root", groupid = 0)
        if not self.gid_exists(exadefgid):
            self.add_group(groupname = "exausers", groupid = exadefgid);
        # create the default groups either below 'exadefgid' or below
        # 'def_groups_start_id', this keeps the range above free for outside usage
        groups_start_id = max(exadefgid, self.def_groups_start_id)
        if not self.gid_exists(groups_start_id-1):
            self.add_group(groupname = "exaadm", groupid = groups_start_id-1)
        if not self.gid_exists(groups_start_id-2):
            self.add_group(groupname = "exadbadm", groupid = groups_start_id-2)
        if not self.gid_exists(groups_start_id-3):
            self.add_group(groupname = "exastoradm", groupid = groups_start_id-3)
        if not self.gid_exists(groups_start_id-4):
            self.add_group(groupname = "exabfsadm", groupid = groups_start_id-4)
        if not self.gid_exists(groups_start_id-5):
            self.add_group(groupname = "exasaasadm", groupid = groups_start_id-5)

    # }}}

    # {{{
    def update_logging(self, logrotation_types: List[str] = ['local'],
                       remote_logrotation_volume: Optional[str] = None,
                       remote_logrotation_prefix: Optional[str] = None,
                       commit: Optional[bool] = True) -> None:
        if ('remote' in logrotation_types
            and (remote_logrotation_volume is None or not self.volume_exists(remote_logrotation_volume))):
            raise EXAConfError(f"{remote_logrotation_volume} is missing or given remote volume volume {remote_logrotation_volume} doesn't exist.")
        for section in self.config.sections:
            if self.is_logging(section):
                log_sec = self.config[section]
                log_sec['LogRotationTypes'] = logrotation_types
                log_sec['RemoteLogRotationVolume'] = remote_logrotation_volume
                log_sec['RemoteLogRotationPrefix'] = remote_logrotation_prefix
                break
            if commit:
                self.commit()
    # }}}

    # {{{ Add Logging

    def add_logging(self, logrotation_types = ['local'],
                    remote_logrotation_volume = None,
                    remote_logrotation_prefix = None,
                    commit = True) -> None:
        """
        Adds the logging entry to EXAConf.
        """

        self.config["Logging"] = {}
        self.config.comments["Logging"] = ["\n", f"RemoteLogging : {remote_logrotation_volume}/{remote_logrotation_prefix}"]
        bfs_sec = self.config["Logging"]
        bfs_sec["LogRotationTypes"] = logrotation_types
        bfs_sec["RemoteLogRotationVolume"] = remote_logrotation_volume
        bfs_sec["RemoteLogRotationPrefix"] = remote_logrotation_prefix

        if commit is True:
            self.commit()


    # }}}
    # {{{ Get Logging

    def get_logging(self) -> config:
        """
        Returns a config containing entries for Logging.
        """
        bfs_config = config()
        for section in self.config.sections:
            if self.is_logging(section):
                bfs_sec = self.config[section]
                bfs_config.LogRotationTypes = bfs_sec["LogRotationTypes"]
                bfs_config.RemoteLogRotationVolume =bfs_sec["RemoteLogRotationVolume"]
                bfs_config.RemoteLogRotationPrefix = bfs_sec["RemoteLogRotationPrefix"]
        return bfs_config

    # }}}
    # {{{ Check if section is a Logging

    def is_logging(self, section):
        """
        Returns true if the given section is a BucketFS.
        """
        # don't try to split the global section!
        return section == "Logging"

    # }}}
    # {{{ Merge EXAConf

    def merge_exaconfs(self, exaconf_list, allow_self=False, force=False,
                       keep_license=True) -> None:
        """
        Merges the changes in the given list of EXAConf instances into this one.
        Done by selecting the EXAConf with the highest revision nr., copying
        its content to this instance and merging selected options later.

        If 'allow_self' is True, then the current instance can be chosen as
        the reference (if it has the highest revision nr.). This is used for
        merging the internal EXAConfs and the external during startup (in
        order to 'import' config changes into the cluster.

        If 'force' is True, the revision of the current instance is ignored
        and the merge is forced. This is used to make sure that the internal
        EXAConf is always copied to the external one during shutdown.

        If 'keep_license' is true, the license file path is now merged. This
        is necessary because the internal license path differs from the
        external (when using exadt).
        """

        ref_exaconf = None
        if allow_self is True:
            ref_exaconf = self
        # init 'max_rev' with 0 if the merge should be forced
        max_rev = self.get_revision() if force is False else 0
        for exaconf in exaconf_list:
            curr_rev = exaconf.get_revision()
            # test for '>=' if 'allow_self' is False in order
            # to always select a reference
            # -> except if all others are '<' (see below)
            if (allow_self is True and curr_rev > max_rev) or \
               (allow_self is False and curr_rev >= max_rev):
                max_rev = curr_rev
                ref_exaconf = exaconf

        # This can only happen if 'allow_self' and 'force' are False but the current instance has the highest revision.
        # That case has to be fixed manually.
        if ref_exaconf is None:
            raise EXAConfError("Failed to select a reference for EXAConf merge (no one has a revision >= '%i'). This conflict has to be fixed manually!"
                               % self.get_revision())

        # copy selected reference by overwriting and reloading
        # the config file of the current one
        if ref_exaconf != self:
            if keep_license:
                old_license = self.get_license_file()
            ref_exaconf.write_copy(self.get_conf_path())
            self.revert()
            if keep_license:
                self.set_license_file(old_license)

        self.__merge_node_uuids(exaconf_list)
        self.commit()

    # }}}
    # {{{ Merge node UUIDs

    def __merge_node_uuids(self, exaconf_list):
        """
        Merges the node UUIDs of the EXAConf instances in the given list into this EXAConf instance.
        Done by replacing all UUIDs with value "IMPORT" in this instance with the UUID of the same
        node in another instance (if that UUID is not "IMPORT").

        Throws an exception if different UUIDs are found for the same node.
        """

        for section in self.config.sections:
            if self.is_node(section):
                node_sec = self.config[section]
                nid = self.get_section_id(section)
                for exaconf in exaconf_list:
                    other_nodes = exaconf.get_nodes()
                    if nid in other_nodes:
                        other_node = other_nodes[nid]
                        # a.) copy UUID from other node
                        if node_sec["UUID"] == "IMPORT" and other_node.uuid != "IMPORT":
                            node_sec["UUID"] = other_node.uuid
                        # b.) compare UUIDs
                        elif node_sec["UUID"] != "IMPORT" and other_node.uuid != "IMPORT":
                            if node_sec["UUID"] != other_node.uuid:
                                raise EXAConfError("Node %s has different UUIDs: '%s' (current) and '%s' (other)." % (nid, node_sec["UUID"], other_node.uuid))

    # }}}
    # {{{ Set timezone

    def set_timezone(self, tz) -> None:
        """
        Set the system timezone to the given string.

        Note: the DB timezone can be set individually via 'Params = -sysTZ=Asia/Tokyo'
        """
        self.config["Global"]["Timezone"] = str(tz)

    # }}}
    # {{{ Set SLC globs

    def set_slc_globs(self, globs) -> None:
        """
        Set the SLCs as given list.
        """
        self.config["Global"]["SLC"] = ",".join(globs)

    # }}}
    # {{{ Set C4 socket path

    def set_c4socket(self, c4socket) -> None:
        """
        Set the c4 socket path to the given string.
        """
        self.config["Global"]["C4Socket"] = str(c4socket)

    # }}}
    # {{{ Set global thresholds

    def set_global_thresholds(self, conf) -> None:
        if "license_rawmem_threshold" in conf:
            if not 0 <= conf.license_rawmem_threshold <= 100:
                raise EXAConfError("Got invalid license_rawmem_threshold '%i' (must be between 0 and 100 percent)!" % conf.license_rawmem_threshold)
            self.config["Global"]["LicenseRAWMEMWarnThreshold"] = int(conf.license_rawmem_threshold)

    # }}}
    # {{{ Set hugepages

    def set_hugepages(self, hugepages) -> None:
        """
        Set the system hugepages to the given string.
        """
        str_hugepages = str(hugepages).strip().lower()
        if str_hugepages not in ('host', 'auto'):
            try:
                if int(str_hugepages) < 0:
                    raise RuntimeError()
            except: raise EXAConfError("Hugepages must be '0', 'host', 'auto' or a positive number of hugepages to allocate.")
        self.config["Global"]["Hugepages"] = str_hugepages

    # }}}
    # {{{ Set storage conf

    def set_storage_conf(self, conf) -> None:
        """
        Set the various configurable EXAStorage parameters.
        """
        if "EXAStorage" not in self.config.sections:
            self.config["EXAStorage"] = {}
        storage_sec = self.config["EXAStorage"]

        if "bg_rec_enabled" in conf:
            storage_sec["BgRecEnabled"] = conf.bg_rec_enabled
        if "bg_rec_limit" in conf:
            if not conf.bg_rec_limit > 0:
                raise EXAConfError("Got invalid bg_rec_limit '%i' (must be > 0)!" % conf.bg_rec_limit)
            storage_sec["BgRecLimit"] = conf.bg_rec_limit
        if "space_warn_threshold" in conf:
            if not 0 <= conf.space_warn_threshold <= 100:
                raise EXAConfError("Got invalid space_warn_threshold '%i' (must be between 0 and 100 percent)!" % conf.space_warn_threshold)
            storage_sec["SpaceWarnThreshold"] = conf.space_warn_threshold

        self.commit()

    # }}}
    # {{{ Set license file

    def set_license_file(self, license_file_path, commit = False) -> None:
        self.config["Global"]["LicenseFile"] = license_file_path
        self.license_filename = os.path.basename(license_file_path)
        if commit:
            self.commit()

    # }}}
    # {{{ Set SaaS enabled flag

    def set_saas(self, enabled, saas_db, commit = False) -> None:
        if 'SaaS' not in self.config.sections:
            self.config['SaaS'] = {}
            self.config.comments['SaaS'] = ['', '# SaaS specific section']
        saas = self.config['SaaS']
        saas['Enabled'] = bool(enabled)
        saas['EditionLockDays'] = 30
        if 'Enabled' not in saas.comments:
            saas.comments['Enabled'] = ['# set to True to enable SaaS services.']
        saas['DBName'] = str(saas_db)
        if 'DBName' not in saas.comments:
            saas.comments['DBName'] = ['# name of the DB used for SaaS.']
        self.config.setdefault("Chargebee", {})
        self.config.comments["Chargebee"] = ['', '# Chargebee specific section']
        self.config["Chargebee"].setdefault("Enabled", False)
        self.config["Chargebee"].setdefault("Configured", False)
        if commit:
            self.commit()
        self.config.setdefault("Keboola", {})
        self.config.comments["Keboola"] = ['', '# Keboola integration section']
        self.config["Keboola"].setdefault("StatusSyncToken", "")
        self.config["Keboola"].setdefault("StatusSyncURL", "")

    # }}}
    # {{{ Set external IdentityProvider config

    def set_idp_config(self, idp_config, commit = False) -> None:
        if 'IdP' not in self.config.sections:
            self.config['IdP'] = {}
            self.config.comments['IdP'] = ['', '# IdP specific section']
        idp = self.config['IdP']
        idp['Config'] = idp_config
        if commit:
            self.commit()
    # }}}

    # {{{ Monitoring plugins: add
    def add_plugin (self, plugin_name: str, bucketfs_name: Optional[str] = None,
                    bucket_name: Optional[str] = None, plugin_dir: Optional[str] = None,
                    commit: Optional[bool] = False) -> None:
        if not plugin_name:
            raise EXAConfError ('Empty plugin name is given')
        plugin_sec = f'Plugin: {plugin_name}'
        if plugin_sec not in self.config.sections:
            self.config[plugin_sec] = config ()
            self.config.comments[plugin_sec] = ['', f'# Plugin {plugin_name} section']
            self.config[plugin_sec]['BucketFS'] = self.def_bucketfs if not bucketfs_name else bucketfs_name
            self.config[plugin_sec]['Bucket'] =  self.def_bucket if not bucket_name else bucket_name
            self.config[plugin_sec]['Dir'] = self.def_plugin_dir if not plugin_dir else plugin_dir
            if commit:
                self.commit ()
        else:
            raise EXAConfError (f'{plugin_name} is already in EXAConf')
    # }}}
    # {{{ Monitoring plugins: remove
    def remove_plugin (self, plugin_name: str, commit: Optional[bool] = False) -> None:
        if not plugin_name:
            raise EXAConfError ('Empty plugin name was given')
        plugin_sec = f'Plugin: {plugin_name}'
        if plugin_sec in self.config.sections:
            for section in self.config.sections:
                if section == plugin_sec:
                    del self.config[plugin_sec]
                    if commit:
                        self.commit ()
                    break
        else:
            raise EXAConfError ('Given plugin ({plugin_name}) does not exist')
    # }}}
    # {{{ Monitoring plugins:get
    def get_plugins (self, plugin_name: Optional[str] = None) -> odict:
        plugins: odict = config ()
        for section in self.config.sections:
            if section.split (':')[0].strip () == 'Plugin':
                conf = config ()
                plugin_sec = self.config[section]
                pname = self.get_section_id (section)
                conf.plugin_name = pname
                conf.bucketfs = plugin_sec['BucketFS']
                conf.bucket = plugin_sec['Bucket']
                conf.dir = plugin_sec['Dir']

                if plugin_name is None or plugin_name == pname:
                    plugins[pname] = conf

        return plugins
    # }}}

    ############################## GETTER #################################

    # {{{ Get section ID

    def get_section_id(self, section: str) -> str:
        """
        Extracts and returns the part behind the ':' from the given section.
        """
        return section.split(":")[1].strip()

    # }}}
    # {{{ Get revision

    def get_revision(self) -> int:
        """
        Returns the EXAConf revision number (or 0, if not found).
        """
        if "Revision" in self.config["Global"].scalars:
            return int(self.config["Global"]["Revision"])
        else:
            return 0

    # }}}
    # {{{ Get conf path

    def get_conf_path(self):
        """
        Returns the path to '$RootDir/EXAConf'.
        """
        return self.conf_path

    # }}}
    # {{{ Get platform

    def get_platform(self):
        """
        Returns the platform of the current EXAConf.
        """
        return self.config["Global"]["Platform"]

    # }}}
    # {{{ Platform is

    def platform_is(self, platform):
        """
        Check if the given platform equals the one defined in EXAConf. Correctly handles upper/lower/camelcase.
        """
        return self.get_platform().lower() == platform.lower()

    # }}}
    # {{{ Get timezone

    def get_timezone(self):
        """
        Return the system timezone configured in the 'Global' section of EXAConf
        (or the defualt timezone if there's none).
        """
        if "Timezone" in self.config["Global"]:
            return self.config["Global"]["Timezone"]
        else:
            return self.def_timezone

    # }}}
    # {{{ Get LegacyPlatformManager
    def get_legacy_platform_manager(self):
        if self.has_tree(self.config, ["Global", "LegacyPlatformManager"]):
            return self.config["Global"].as_bool("LegacyPlatformManager")
        else:
            return self.def_legacy_platform_manager
    # }}}
    # {{{ Get SLC (script language container)

    def get_slc_globs(self):
        """
        Return the configured SLC paths.
        """
        try:
            return [x.strip() for x in self.config["Global"]["SLC"].split(',')]
        except KeyError:
            return self.def_slc_globs

    # }}}
    # {{{ Get resolv.conf filename

    def get_resolv_filename(self):
        """
        Return the resolv.conf filename configured in the 'Global' section of EXAConf
        """
        if "ResolvFilename" in self.config["Global"]:
            return self.config["Global"]["ResolvFilename"]
        else:
            return self.def_resolv_filename

    # }}}
    # {{{ Get hosts filename

    def get_hosts_filename(self):
        """
        Return the hosts filename configured in the 'Global' section of EXAConf
        """
        if "HostsFilename" in self.config["Global"]:
            return self.config["Global"]["HostsFilename"]
        else:
            return self.def_hosts_filename

    # }}}
    # {{{ Get rsyslog.conf filename

    def get_rsyslog_filename(self):
        """
        Return the rsyslog.conf filename configured in the 'Global' section of EXAConf
        """
        if "RSyslogFilename" in self.config["Global"]:
            return self.config["Global"]["RSyslogFilename"]
        else:
            return self.def_rsyslog_filename

    # }}}
    # {{{ Get log backup count

    def get_log_backup_count(self):
        """
        Return the maximal log backup count configured
        """
        try:
            backup_count = int(self.config["Logging"]["BackupCount"])
            if backup_count < 1:
                return self.def_logging_backup_count
            return backup_count
        except: return self.def_logging_backup_count

    # }}}
    # {{{ Get c4 socket path

    def get_c4socket(self):
        """
        Return the c4 socket path configured in the 'Global' section of EXAConf
        """
        if "C4Socket" in self.config["Global"]:
            return self.config["Global"]["C4Socket"]
        else:
            return self.def_c4socket

        # }}}
    # {{{ Get global thresholds

    def get_global_thresholds(self) -> config:
        thresholds_config = config()
        if 'LicenseRAWMEMWarnThreshold' in self.config["Global"]:
            thresholds_config.license_rawmem_threshold = int(self.config["Global"]['LicenseRAWMEMWarnThreshold'])
        else: thresholds_config.license_rawmem_threshold = self.def_license_rawmem_threshold
        return thresholds_config

    # }}}
    # {{{ Get hugepages

    def get_hugepages(self):
        """
        Return the system hugepages configured in the 'Global' section of EXAConf.
        Can be an integer or word 'auto', number '0' means hugepages should not be configured.
        """
        if "Hugepages" in self.config["Global"]:
            return str(self.config["Global"]["Hugepages"]).strip().lower()
        else:
            return self.def_hugepages

    # }}}
    # {{{ Get cluster name

    def get_cluster_name(self):
        """
        Returns the cluster name.
        """
        return self.config["Global"]["ClusterName"]

    # }}}
    # {{{ Get db version

    def get_db_version(self):
        """
        Returns the current DB version.
        """
        if self.initialized():
            return self.config["Global"]["DBVersion"]
        else:
            return self.db_version

    # }}}
    # {{{ Get os version

    def get_os_version(self):
        """
        Returns the current OS version.
        """
        if self.initialized():
            return self.config["Global"]["OSVersion"]
        else:
            return self.os_version

    # }}}
    # {{{ Get re version

    def get_re_version(self):
        """
        Returns the current EXARuntime version (introduced in version 6.1.3).
        deprecated
        """
        return exacos_constants.re_version()

    # }}}
    # {{{ Get img version

    def get_img_version(self) -> str:
        """
        Returns the current image version.
        """
        # has been introduced later, i. e. may be absent
        if self.initialized() and "ImageVersion" in self.config["Global"].scalars:
            return str(self.config["Global"]["ImageVersion"])
        else:
            return self.img_version

    # }}}
    # {{{ Get version

    def get_version(self) -> str:
        """
        Returns the version of the EXAConf python module (not the configuration file).
        """
        return self.version

    # }}}
    # {{{ Get file version

    def get_file_version(self):
        """
        Returns the version of the associated EXAConf file (not the EXAConf python module). If not yet initialized, the python module version is returned instead.
        """
        if self.initialized():
            return self.config["Global"]["ConfVersion"]
        else:
            return self.version

    # }}}
    # {{{ Get cored port

    def get_cored_port(self):
        """
        Returns the port number used by the 'Cored' daemon.
        """
        if "CoredPort" in self.config["Global"].scalars:
            return self.config["Global"]["CoredPort"]
        else:
            return self.def_cored_port

    # }}}
    # {{{ Get cored canary file dirs

    def get_cored_canary_file_dirs(self):
        """
        Returns the canary file directories used by cored.
        """
        if "CoredCanaryFileDirs" in self.config["Global"]:
            return self.config["Global"]["CoredCanaryFileDirs"]
        else:
            return self.def_cored_canary_file_dirs

    # }}}
    # {{{ Get cored canary timeout

    def get_cored_canary_timeout(self):
        """
        Returns the canary timeout used by cored.
        """
        if "CoredCanaryTimeout" in self.config["Global"]:
            return self.config["Global"]["CoredCanaryTimeout"]
        else:
            return self.def_cored_canary_timeout

    # }}}
    # {{{ Get cored canary grace period

    def get_cored_canary_grace_period(self):
        """
        Returns the canary grace period used by cored.
        """
        if "CoredCanaryGracePeriod" in self.config["Global"]:
            return self.config["Global"]["CoredCanaryGracePeriod"]
        else:
            return self.def_cored_canary_grace_period

    # }}}
    # {{{ Get cored canary status

    def get_cored_canary_logging_enabled(self):
        """
        Returns whether canary files are enabled.  If they are, cored aborts on failure to write to a canary file and on logging errors.
        """
        if "CoredCanaryLoggingEnabled" in self.config["Global"]:
            return self.config["Global"]["CoredCanaryLoggingEnabled"]
        else:
            return self.def_cored_canary_logging_enabled

    # }}}
    # {{{ Get ssh port

    def get_ssh_port(self):
        """
        Returns the port number used by the SSH daemon (introduced in version 6.0.7).
        """
        if "SSHPort" in self.config["Global"].scalars:
            return self.config["Global"]["SSHPort"]
        else:
            return self.def_ssh_port

    # }}}
    #
    # {{{
    def get_issue_tracker(self) -> str:
        if "IssueTracker" in self.config["Global"].scalars:
            return str(self.config["Global"]["IssueTracker"])
        else:
            return self.def_issue_tracker
    # }}}
    #
    # {{{ Get XMLRPC port

    def get_xmlrpc_port(self):
        """
        Returns the port number used by the XMLRPC API.
        """
        if "XMLRPCPort" in self.config["Global"].scalars:
            return int(self.config["Global"]["XMLRPCPort"])
        else:
            return self.def_xmlrpc_port

    # }}}
    # {{{ Get WebUI Backend port

    def get_webui_backend_port(self):
        """
        Returns the port number used by the webui backend.
        """
        if "WebUIBackendPort" in self.config["Global"].scalars:
            return int(self.config["Global"]["WebUIBackendPort"])
        else:
            return self.def_webui_backend_port

    # }}}
    # {{{ Get WebUI Domain

    def get_ui_domain(self):
        """
        Returns the domain of the webui.
        """
        if "WebUIDomain" in self.config["Global"].scalars:
            return self.config["Global"]["WebUIDomain"]
        else:
            return self.def_ui_domain

    # }}}
    # {{{ Get Loadbalancer flag
    def get_loadbalancer_flag(self):
        """
        Return loadbalancer flag, wether or not to use Cloud Provided Certificate
        """
        if "EnableLoadBalancer" in self.config["Global"].scalars:
            return json.loads(self.config["Global"]["EnableLoadBalancer"].lower())
        else:
            return self.def_loadbalancer_flag
    # }}}
    # {{{ Get WebUI backend binary
    def get_webui_backend_binray(self):
        """
        Return webui backend binary for either standalone or saas deployment
        """
        if "WebUIBackendBinary" in self.config["Global"].scalars:
            return self.config["Global"]["WebUIBackendBinary"]
        else:
            return self.def_ui_backend_binary
    # }}}
    # {{{ Get license file

    def get_license_file(self):
        """
        Returns the path to the license file.
        """
        return self.config["Global"]["LicenseFile"]

    # }}}
    # {{{ Get private network name

    def get_priv_net_name(self):
        """
        Returns the NAME of the private network.
        """
        priv_net_name = self.get_cluster_name() + "_priv"
        return priv_net_name

    # }}}
    # {{{ Get public network name

    def get_pub_net_name(self):
        """
        Returns the NAME of the public network.
        """
        pub_net_name = self.get_cluster_name() + "_pub"
        return pub_net_name

    # }}}
    # {{{ Get duplicates

    def get_duplicates(self, seq):
        """
        Returns a list off all duplicates in the given sequence.
        """
        seen = set()
        # This is not completely obvious, but the `seen.add()` here does not return a bool
        # It always returns None and the `or` is just used to invoke it for unseen items
        return [x for x in seq if x in seen or seen.add(x)] # type: ignore[func-returns-value]

    # }}}
    # {{{ Get network

    def get_network(self, net_type) -> str:
        """
        Returns a network (as a string) that includes the private/public IPs of all nodes in the config.
        Raises an EXAConfError if an invalid IP is found or the IP of at least one node is not part
        of the network defined by the first node section.

        This function assumes that all nodes have an entry for the requested network type. The calling
        function has to check if the network type is actually present (private / public).
        """

        network = ""
        for section in self.config.sections:
            if self.is_node(section):
                node_sec = self.config[section]
                node_network = node_sec.get(net_type)
                if not node_network or node_network == "":
                    raise EXAConfError("Network type '%s' is missing in section '%s'!" % (net_type, section))
                node_ip = node_network.split("/")[0].strip()
                # check if the extracted IP is valid
                if not self.ip_is_valid(node_ip):
                    raise EXAConfError("IP %s in section '%s' is invalid!" % (node_ip, section))

                # first node : choose the private net as the cluster network (and make it a 'real' network)
                if network == "":
                    subnet = ipaddr.IPNetwork(node_network)
                    network = "%s/%s" % (str(subnet.network), str(subnet.prefixlen))
                # other nodes : check if their IP is part of the chosen net
                elif ipaddr.IPAddress(node_ip) not in ipaddr.IPNetwork(network):
                    raise EXAConfError("IP %s is not part of network %s!" % (node_ip, network))

        return network

    # }}}
    # {{{ Get private network

    def get_priv_net(self) -> str:
        """
        Get a valid IP network containing the private IPs of all nodes (see get_network()).
        """
        return self.get_network("PrivateNet")

    # }}}
    # {{{ Get public network

    def get_pub_net(self) -> str:
        """
        Get a valid IP network containing the public IPs of all nodes (see get_network()).
        """
        return self.get_network("PublicNet")

    # }}}
    # {{{ Volume exists

    def volume_exists(self, name):
        """
        Checks if the given volume exists and returns section name.
        """

        for section in self.config.sections:
            if self.is_volume(section) or \
               self.is_object_volume(section) or \
               self.is_remote_volume(section):
                if str(name) == self.get_section_id(section):
                    return section
        return None

    # }}}
    # {{{ Exa volume exists

    def exa_volume_exists(self, name):
        """
        Checks if the given regular volume exists and returns section name.
        """

        for section in self.config.sections:
            if self.is_volume(section):
                if str(name) == self.get_section_id(section):
                    return section
        return None

    # }}}
    # {{{ Object volume exists

    def object_volume_exists(self, name):
        """
        Checks if the given object volume exists and returns section name.
        """

        for section in self.config.sections:
            if self.is_object_volume(section):
                if str(name) == self.get_section_id(section):
                    return section
        return None

    # }}}
    # {{{ Remote volume exists

    def remote_volume_exists(self, name):
        """
        Checks if the given remote volume exists and returns section name.
        """

        for section in self.config.sections:
            if self.is_remote_volume(section):
                if str(name) == self.get_section_id(section):
                    return section
        return None

    # }}}
    # {{{ Remote volume ID exists

    def remote_volume_id_exists(self, ID) -> bool:
        """
        Checks if a remote volume with the given ID exists.
        """

        for section in self.config.sections:
            if self.is_remote_volume(section):
                vol_sec = self.config[section]
                if str(ID) == str(vol_sec['ID']):
                    return True
        return False

    # }}}
    # {{{ Remote volume url exists

    def remote_volume_url_exists(self, url) -> bool:
        """
        Check if remote volume with the given url exists
        """
        for section in self.config.sections:
            if self.is_remote_volume(section):
                vol_sec = self.config[section]
                if url == vol_sec['URL']:
                    return True
        return False

    # }}}
    # {{{ Database exists

    def database_exists(self, name, *, db_uuid = None):
        """
        Checks if the given database exists.
        """

        for section in self.config.sections:
            if self.is_database(section):
                if str(name) == self.get_section_id(section):
                    return section
                if db_uuid is not None and 'ID' in section and section['ID'] == db_uuid:
                    return section
        return None

    # }}}
    # {{{ Database backup schedule exists

    def db_backup_exists(self, db_name, backup_name):
        """
        Checks if the given backup schedule exists in the given DB section.
        """
        db_sec_name = self.database_exists(db_name)
        if not db_sec_name:
            return False
        db_sec = self.config[db_sec_name]
        for ba_sec_name in db_sec.sections:
            ba_sec = ba_sec_name.split(':')
            if len(ba_sec) > 1 and ba_sec[0].strip() == 'Backup' and ba_sec[1].strip() == backup_name:
                return ba_sec_name
        return None

    # }}}
    # {{{ Database snapshot backup schedule exists

    def db_snapshot_backup_exists(self, db_name, backup_name):
        """
        Checks if the given snapshot backup schedule exists in the given DB section.
        """
        db_sec_name = self.database_exists(db_name)
        if not db_sec_name:
            return False
        db_sec = self.config[db_sec_name]
        for ba_sec_name in db_sec.sections:
            ba_sec = ba_sec_name.split(':')
            if len(ba_sec) > 1 and ba_sec[0].strip() == 'SnapshotBackup' and ba_sec[1].strip() == backup_name:
                return ba_sec_name
        return None

    # }}}
    # {{{ Node id exists

    def node_exists(self, nid):
        """
        Returns True if a node with the given ID exists, False otherwise.
        """
        for section in self.config.sections:
            if self.is_node(section):
                if str(nid) == self.get_section_id(section):
                    return section
        return None

    # }}}
    # {{{ User exists

    def user_exists(self, username) -> bool:
        """
        Returns True if a user with the given name exists in EXAConf (or it's a reserved username).
        """
        return username in self.get_users() or username in self.reserved_users

    # }}}
    # {{{ UID exists

    def uid_exists(self, uid) -> bool:
        """
        Returns True if a user with the given ID exists in EXAConf (or it's a reserved uid).
        """
        for u in self.get_users().values():
            if u.id == uid:
                return True
        for ruid in self.reserved_users.values():
            if ruid == uid:
                return True
        return False

    # }}}
    # {{{ User in group

    def user_in_group(self, user, groups) -> bool:
        """
        Returns True if the given user is in one of the given groups.
        'user' can be a name or ID. 'groups' can either be a list
        of group names or a single group name.
        """
        uname = self.to_uname(user)
        if not isinstance(groups, list):
            groups = [groups,]
        uconf = self.get_users()[uname]
        if uconf.group in groups:
            return True
        if "additional_groups" in uconf:
            for ag in uconf.additional_groups:
                if ag in groups:
                    return True
        return False

    # }}}
    # {{{ Group exists

    def group_exists(self, groupname) -> bool:
        """
        Returns True if a group with the given name exists in EXAConf (or it's a reserved groupname).
        """
        return groupname in self.get_groups() or groupname in self.reserved_groups

    # }}}
    # {{{ GID exists

    def gid_exists(self, groupid) -> bool:
        """
        Returns True if a group with the given ID exists in EXAConf.
        """
        for g in self.get_groups().values():
            if g.id == groupid:
                return True
        for rgid in self.reserved_groups.values():
            if rgid == groupid:
                return True
        return False

    # }}}
    # {{{ bucketfs exists

    def bucketfs_exists(self, name):
        """
        Checks if the given BucketFS exists.
        """

        for section in self.config.sections:
            if self.is_bucketfs(section):
                if str(name) == self.get_section_id(section):
                    return section
        return None

    # }}}
    # {{{ bucket exists

    def bucket_exists(self, name, bfs_name):
        """
        Checks if the given Bucket exists.
        """

        bucketfs_conf = self.get_bucketfs()
        for bfs in bucketfs_conf.values():
            if bfs.name == bfs_name:
                if name in bfs.buckets:
                    return bfs.buckets[name]._sec_name
                return None
        return None

    # }}}
    # {{{ Get max node id

    def get_max_node_id(self) -> int:
        """
        Returns the max ID of all existing nodes or 'max_reserved_node_id' if there are none.
        """
        max_nid = int(self.max_reserved_node_id)
        for section in self.config.sections:
            if self.is_node(section):
                nid = int(self.get_section_id(section))
                if nid > max_nid:
                    max_nid = nid
        return max_nid

    # }}}
    # {{{ Get max remote volume id

    def get_max_remote_volume_id(self):
        """
        Returns the max ID of all existing remote volumes or 'remote_vol_id_offset' if there are none.
        """
        max_vid = self.remote_vol_id_offset
        for section in self.config.sections:
            if self.is_remote_volume(section):
                vol_sec = self.config[section]
                vid = int(vol_sec['ID'])
                if vid > max_vid:
                    max_vid = vid
        return max_vid

    # }}}
    # {{{ Get def bg rec limit

    def get_def_bg_rec_limit(self):
        """
        Returns the default background recovery throughput limit (in MiB/s), depending on the network speed.
        """

        #FIXME : actually use netwoork speed (as soon as available)
        return self.def_bg_rec_limit_1gb

    # }}}
    # {{{ Get nodes

    def get_nodes(self) -> config:
        """
        Returns a config containing all nodes and their options within the config file.
        Options with empty values are omitted.
        """
        node_configs = config()
        for section in self.config.sections:
            if self.is_node(section):
                node_sec = self.config[section]
                nid = self.get_section_id(section)
                node_conf = config()
                node_conf._sec_name = section
                node_conf.id = nid
                node_conf.name = node_sec["Name"]
                if node_sec["PrivateNet"].strip() != "":
                    node_conf.private_net = self.to_net_str(node_sec["PrivateNet"], nid)
                    node_conf.private_ip = node_conf.private_net.split("/")[0]
                if node_sec["PublicNet"].strip() != "":
                    node_conf.public_net = self.to_net_str(node_sec["PublicNet"], nid)
                    node_conf.public_ip = node_conf.public_net.split("/")[0]
                node_conf.uuid = node_sec["UUID"]
                if "Affinity" in node_sec:
                    node_conf.affinity = node_sec["Affinity"]
                else: node_conf.affinity = nid
                # storage disks
                node_conf.disks = config()
                for subsec in node_sec.sections:
                    if self.is_disk(subsec):
                        disk_sec = node_sec[subsec]
                        disk_conf = config()
                        disk_conf.name = self.get_section_id(subsec)
                        # optional disk values
                        if "Component" in disk_sec.scalars:
                            disk_conf.component = disk_sec["Component"]
                        else:
                            disk_conf.component = self.def_disk_component
                        disk_conf.devices = []
                        if "Devices" in disk_sec.scalars:
                            disk_conf.devices = [ dev.strip() for dev in disk_sec["Devices"].split(",") if dev.strip() != "" ]
                        if "Drives" in disk_sec.scalars:
                            disk_conf.drives = [ dri.strip() for dri in disk_sec["Drives"].split(",") if dri.strip() != "" ]
                        if "Mapping" in disk_sec.scalars:
                            # the device-mapping entries, as they are found in the EXAConf file
                            disk_conf.mapping = [ (m.split(":")[0].strip(), m.split(":")[1].strip()) for m in disk_sec["Mapping"].split(",") if m.strip() != "" ]
                            # list of tuples that map an external device-file to a container-path
                            # --> converted to absolute paths, so they can be directly used by the docker-handler
                            disk_conf.mapped_devices = []
                            for dev, path in disk_conf.mapping:
                                dev_host = os.path.join(path, dev)
                                dev_container = os.path.join(self.container_root, self.storage_dir, dev)
                                disk_conf.mapped_devices.append((dev_host, dev_container))
                        if "Ephemeral" in disk_sec.scalars:
                            disk_conf.ephemeral = disk_sec.as_bool("Ephemeral")
                        else: disk_conf.ephemeral = False
                        if "DirectIO" in disk_sec.scalars:
                            disk_conf.direct_io = disk_sec.as_bool("DirectIO")
                        else: disk_conf.direct_io = True
                        node_conf.disks[disk_conf.name] = disk_conf
                # optional node values
                if "DockerVolume" in node_sec.scalars:
                    node_conf.docker_volume = os.path.join(self.config["Docker"]["RootDir"], node_sec["DockerVolume"])
                if "ExposedPorts" in node_sec.scalars:
                    node_conf.exposed_ports =  [ p.split(":") for p in node_sec["ExposedPorts"].split(",") ]
                    node_conf.exposed_ports[:] = [ (int(p[0].strip()), int(p[1].strip())) for p in node_conf.exposed_ports ]
                if "State" in node_sec.scalars:
                    node_conf.state = str(node_sec["State"]).strip().lower()
                else: node_conf.state = ''
                node_configs[nid] = node_conf
        return node_configs

    # }}}
    # {{{ Get node config

    def get_node_config(self, node_id) -> Optional[config]:
        return self.get_nodes().get(str(node_id))

    # }}}
    # {{{ Get node device list

    def get_node_device_list(self, node_id):
        node_conf = self.get_node_config(node_id)
        if node_conf:
            return [disk_conf.devices for disk_conf in node_conf.disks.values()]
        else:
            return []

    # }}}
    # {{{ Get num nodes

    def get_num_nodes(self) -> int:
        """
        Returns the nr. of nodes in the current EXAConf.
        """
        return len([sec for sec in self.config.sections if self.is_node(sec)])

    # }}}
    # {{{ Get storage conf

    def get_storage_conf(self) -> config:
        """
        Returns the various configurable EXAStorage parameters.
        """
        storage_conf = config()
        if "EXAStorage" not in self.config.sections:
            raise EXAConfError("Section 'EXAStorage' does not exist in '%s'!" % (self.conf_path))
        storage_sec = self.config["EXAStorage"]
        if "BgRecEnabled" in storage_sec.scalars and storage_sec["BgRecEnabled"].strip() != "":
            storage_conf.bg_rec_enabled = storage_sec.as_bool("BgRecEnabled")
        if "BgRecLimit" in storage_sec.scalars and storage_sec["BgRecLimit"].strip() != "":
            storage_conf.bg_rec_limit = int(storage_sec["BgRecLimit"])
        if "SpaceWarnThreshold" in storage_sec.scalars and storage_sec["SpaceWarnThreshold"].strip() != "":
            storage_conf.space_warn_threshold = int(storage_sec["SpaceWarnThreshold"])
        return storage_conf

    # }}}
    # {{{ Get volumes

    def get_volumes(self, filters=None) -> config:
        """
        Returns configurations describing all existing EXAStorage volumes.
        """
        volume_configs = config()
        for section in self.config.sections:
            if self.is_volume(section):
                vol_sec = self.config[section]
                # copy values to config
                vol_name: str = self.get_section_id(section)
                conf = config()
                conf._sec_name = section
                conf.name = vol_name
                conf.type = vol_sec["Type"].strip()
                # data or archive volume
                if conf.type == "data" or conf.type == "archive":
                    conf.size = units2bytes(vol_sec["Size"]) if vol_sec["Size"].strip() != "" else 0
                    conf.disk = vol_sec["Disk"].strip() if vol_sec["Disk"].strip() != "" else None
                    conf.redundancy = vol_sec.as_int("Redundancy")
                    conf.owner = tuple([ int(x.strip()) for x in vol_sec["Owner"].split(":") if x.strip() != "" ])
                    conf.nodes = [ int(n.strip()) for n in vol_sec["Nodes"].split(",") if n.strip() != "" ]
                    if 'PartitionSize' in vol_sec.scalars:
                        conf.partition_size = units2bytes(vol_sec["PartitionSize"])
                    # optional values
                    conf.permissions = vol_sec["Permissions"] if "Permissions" in vol_sec.scalars else self.def_vol_perm
                    conf.num_master_nodes = int(vol_sec["NumMasterNodes"]) if "NumMasterNodes" in vol_sec.scalars else len(conf.nodes)
                    conf.priority = int(vol_sec["Priority"]) if "Priority" in vol_sec.scalars else self.def_vol_prio
                    conf.shared = vol_sec.as_bool("Shared") if "Shared" in vol_sec.scalars else self.def_vol_shared
                    if "Labels" in vol_sec.scalars:
                        conf.labels = [ l.strip() for l in vol_sec["Labels"].split(",") if l.strip() != "" ]
                    if conf.type == "archive":
                        for port in ('http', 'https', 'ftp', 'ftps', 'sftp'):
                            port_conf, port_var = '%sPort' % port.capitalize(), '%s_port' % port
                            if port_conf in vol_sec.scalars:
                                conf[port_var] = int(vol_sec[port_conf])
                    # HIDDEN optional values:
                    if "BlockSize" in vol_sec.scalars:
                        conf.block_size = units2bytes(vol_sec["BlockSize"])
                    else:
                        if conf.type == "data":
                            conf.block_size = self.def_vol_block_size
                        elif conf.type == "archive":
                            conf.block_size = self.def_arch_vol_block_size
                        else:
                            raise EXAConfError("Found invalid volume type '%s'!" % conf.type)
                    if "StripeSize" in vol_sec.scalars:
                        conf.stripe_size = units2bytes(vol_sec["StripeSize"])
                    else:
                        if conf.type == "data":
                            conf.stripe_size = self.def_vol_stripe_size
                        elif conf.type == "archive":
                            conf.stripe_size = self.def_arch_vol_stripe_size
                        else:
                            raise EXAConfError("Found invalid volume type '%s'!" % conf.type)
                volume_configs[vol_name] = conf
        return self.filter_configs(volume_configs, filters)

    # }}}
    # {{{ Get object volumes

    def get_object_volumes(self, filters=None) -> config:
        """
        Returns configurations describing all existing object volumes.
        """
        volume_configs = config()
        for section in self.config.sections:
            if self.is_object_volume(section):
                vol_sec = self.config[section]
                # copy values to config
                vol_name = self.get_section_id(section)
                conf = config()
                conf.name = vol_name
                conf.type = vol_sec["Type"].strip()
                conf.bucket = vol_sec["Bucket"].strip()
                if "Prefix" in vol_sec: conf.prefix = vol_sec["Prefix"].strip()
                conf.owner = tuple([ int(x.strip()) for x in vol_sec["Owner"].split(":") if x.strip() != "" ])
                conf.region = vol_sec["Region"].strip()
                if 'EndPoint' in vol_sec: conf.end_point = vol_sec['EndPoint'].strip()
                conf.priority = int(vol_sec["Priority"]) if "Priority" in vol_sec.scalars else self.def_vol_prio
                conf.shared = vol_sec.as_bool("Shared") if "Shared" in vol_sec.scalars else self.def_vol_shared
                if 'Permissions' in vol_sec: conf.permissions = vol_sec['Permissions'].strip()
                if 'Credentials' in vol_sec: conf.credentials = vol_sec['Credentials'].strip()
                if 'Options' in vol_sec: conf.options = vol_sec["Options"].strip()
                if "Labels" in vol_sec.scalars:
                    conf.labels = [ l.strip() for l in vol_sec["Labels"].split(",") if l.strip() != "" ]
                volume_configs[vol_name] = conf
        return self.filter_configs(volume_configs, filters)

    # }}}
    # {{{ Get remote volumes

    def get_remote_volumes(self, filters=None) -> config:
        """
        Returns configurations describing all existing remote volumes (SMB, S3, etc.)
        """
        volume_configs = config()
        for section in self.config.sections:
            if self.is_remote_volume(section):
                vol_sec = self.config[section]
                # copy values to config
                vol_name = self.get_section_id(section)
                conf = config()
                conf.name = vol_name
                conf.type = vol_sec["Type"].strip()
                conf.vid = str(vol_sec["ID"]).strip()
                conf.url = vol_sec["URL"].strip()
                conf.owner = tuple([ int(x.strip()) for x in vol_sec["Owner"].split(":") if x.strip() != "" ])
                if 'Username' in vol_sec: conf.username = vol_sec["Username"].strip()
                if 'Passwd' in vol_sec:
                    conf.password = decode_remote_vol_passwd(vol_sec['Passwd'].strip())
                if 'Options' in vol_sec: conf.options = vol_sec["Options"].strip()
                if "Labels" in vol_sec.scalars:
                    conf.labels = [ l.strip() for l in vol_sec["Labels"].split(",") if l.strip() != "" ]
                for port_conf_name, port_var_name in (('HttpPort', 'http_port'),
                                                      ('HttpsPort', 'https_port'),
                                                      ('FtpPort', 'ftp_port'),
                                                      ('FtpsPort', 'ftps_port'),
                                                      ('SftpPort', 'sftp_port')):
                    if port_conf_name in vol_sec.scalars:
                        conf[port_var_name] = int(vol_sec[port_conf_name])
                volume_configs[vol_name] = conf
        return self.filter_configs(volume_configs, filters)

    # }}}
    # {{{ to_remote_volume_id

    def to_remote_volume_id(self, remote_volume_name):
        """
        convert remote volume name to remote volume id
        raise EXAConfError if the given remote volume name does not exist
        """
        rvid = None
        # is the remote_volume_name already a remote volume id?
        if isinstance(remote_volume_name, str):
            try:
                rvid = int(remote_volume_name)
            except ValueError:
                rvs = self.get_remote_volumes()
                if remote_volume_name in rvs:
                    rvid = rvs[remote_volume_name].vid
        # if remote volume name is already integer, return it
        else:
            rvid = remote_volume_name
        if rvid is None:
            raise EXAConfError('Remote volume {} does not exist in EXAConf'.format(remote_volume_name))
        return rvid

    # }}}
    # {{{ to_remote_volume_name

    def to_remote_volume_name(self, remote_volume_id):
        """
        convert remote volume id to remote volume name
        raise EXAConfError if the given remote volume id does not exist
        """
        rvname = None
        rvid = None
        # is the remote_volume_id already a remote volume name?
        if isinstance(remote_volume_id, str):
            try:
                rvid = int(remote_volume_id)
            except ValueError:
                rvname = remote_volume_id
        # if remote_volume_id is integer
        else:
            rvid = remote_volume_id
        rvs = self.get_remote_volumes()
        for rv in rvs.items():
            if str(rv[1].vid) == str(rvid):
                rvname = rv[0]
        if rvname is None:
            raise EXAConfError('Remote volume {} does not exist in EXAConf'.format(remote_volume_id))
        return rvname

    # }}}
    # {{{ Get databases

    def get_databases(self, filters=None) -> config:
        """
        Returns a config describing all existing EXASolution databases.
        """
        db_configs = config()
        for section in self.config.sections:
            if self.is_database(section):
                db_sec = self.config[section]
                db_name = self.get_section_id(section)
                conf = config()
                conf._sec_name = section
                conf.name = db_name
                conf.uuid = db_sec["ID"]
                conf.version = db_sec["Version"]
                conf.data_volume = db_sec["DataVolume"]
                if "CloudDataVolume" in db_sec.scalars:
                    conf.cloud_data_volume = db_sec["CloudDataVolume"]
                if "SnapshotVolume" in db_sec.scalars:
                    conf.snapshot_volume = db_sec["SnapshotVolume"]
                if "CloudDataVolumeSync" in db_sec.scalars:
                    conf.data_volume_sync = db_sec["CloudDataVolumeSync"]
                if "SnapshotVolumeSync" in db_sec.scalars:
                    conf.snapshot_volume_sync = db_sec["SnapshotVolumeSync"]
                conf.mem_size =  int(units2bytes(db_sec["MemSize"]) // 1048576)
                conf.port = db_sec.as_int("Port")
                conf.nodes = [ int(n.strip()) for n in db_sec["Nodes"].split(",") if n.strip() != "" ]
                if "NumActiveNodes" in db_sec.scalars:
                    conf.num_active_nodes = db_sec.as_int("NumActiveNodes")
                else:
                    conf.num_active_nodes = db_sec.as_int("NumMasterNodes")
                conf.owner = tuple([ int(x.strip()) for x in db_sec["Owner"].split(":") if x.strip() != "" ])
                # optional values:
                if "Params" in db_sec.scalars:
                    conf.params = db_sec["Params"]
                if "LdapServers" in db_sec.scalars:
                    conf.ldap_servers = [ l.strip() for l in db_sec["LdapServers"].split(",") if l.strip() != "" ]
                if "CacheVolumeDisk" in db_sec.scalars:
                    cache_disk_name, cache_volume_size = db_sec["CacheVolumeDisk"].split(':')
                    conf.cache_volume_disk = (cache_disk_name.strip(), units2bytes(cache_volume_size))
                if "EnableAuditing" in db_sec.scalars:
                    conf.enable_auditing = db_sec.as_bool("EnableAuditing")
                if "Interfaces" in db_sec.scalars and db_sec["Interfaces"] is not None:
                    conf.interfaces = [ i.strip() for i in db_sec["Interfaces"].split(",") if i.strip() != "" ]
                if "VolumeQuota" in db_sec.scalars:
                    conf.volume_quota = int(units2bytes(db_sec["VolumeQuota"]))
                if "VolumeMoveDelay" in db_sec.scalars:
                    conf.volume_move_delay = db_sec["VolumeMoveDelay"]
                if "InitialSQL" in db_sec.scalars:
                    conf.initial_sql = db_sec["InitialSQL"]
                if "DefaultSysPasswdHash" in db_sec.scalars:
                    conf.default_sys_passwd_hash = db_sec["DefaultSysPasswdHash"]
                if "AdditionalSysPasswdHashes" in db_sec.scalars:
                    conf.additional_sys_passwd_hashes = db_sec["AdditionalSysPasswdHashes"]
                if "BuiltinScriptLanguageName" in db_sec.scalars:
                    conf.builtin_script_language_name = db_sec["BuiltinScriptLanguageName"]

                if "MasterDatabase" in db_sec.scalars:
                    conf.master_database = db_sec["MasterDatabase"]
                    conf.auto_start = False # always disable auto start for worker databases
                elif "AutoStart" in db_sec.scalars:
                    conf.auto_start = db_sec.as_bool("AutoStart")
                else:
                    conf.auto_start = True # default to autostart for main database clusters

                # JDBC
                conf["jdbc"] = config()
                if "JDBC" in db_sec.sections:
                    jdbc_sec = db_sec["JDBC"]
                    conf.jdbc.bucketfs = jdbc_sec["BucketFS"]
                    conf.jdbc.bucket = jdbc_sec["Bucket"]
                    conf.jdbc.dir = jdbc_sec["Dir"]
                    if "AdditionalURLs" in jdbc_sec:
                        conf.jdbc.additional_urls = list(filter(lambda x: len(x) > 0,
                                                                [x.strip() for x in jdbc_sec["AdditionalURLs"].split(',')]))
                    else: conf.jdbc.additional_urls = []
                else:
                    conf.jdbc.bucketfs = self.def_bucketfs
                    conf.jdbc.bucket = self.def_bucket
                    conf.jdbc.dir = self.def_jdbc_driver_dir
                    conf.jdbc.additional_urls = []
                # Oracle
                conf["oracle"] = config()
                if "Oracle" in db_sec.sections:
                    oracle_sec = db_sec["Oracle"]
                    conf.oracle.bucketfs = oracle_sec["BucketFS"]
                    conf.oracle.bucket = oracle_sec["Bucket"]
                    conf.oracle.dir = oracle_sec["Dir"]
                else:
                    conf.oracle.bucketfs = self.def_bucketfs
                    conf.oracle.bucket = self.def_bucket
                    conf.oracle.dir = self.def_oracle_driver_dir
                # optional sections: backups
                for section in db_sec.sections:
                    if self.is_backup(section):
                        ba_sec = db_sec[section]
                        if 'backups' not in conf:
                            conf['backups'] = config()
                        backup_conf = config({
                            'enabled' : ba_sec.as_bool('Enabled'),
                            'volume' : ba_sec['Volume'],
                            'level' : ba_sec.as_int('Level'),
                            'expire' : str2sec(ba_sec['Expire']),
                            'minute' : ba_sec['Minute'],
                            'hour' : ba_sec['Hour'],
                            'day' : ba_sec['Day'],
                            'month' : ba_sec['Month'],
                            'weekday' : ba_sec['Weekday']})
                        conf.backups[self.get_section_id(section)] = backup_conf
                    if self.is_snapshot_backup(section):
                        ba_sec = db_sec[section]
                        if 'snapshot_backups' not in conf:
                            conf['snapshot_backups'] = config()
                        snapshot_backup_conf = config({
                            'enabled' : ba_sec.as_bool('Enabled'),
                            'expire' : str2sec(ba_sec['Expire']),
                            'minute' : ba_sec['Minute'],
                            'hour' : ba_sec['Hour'],
                            'day' : ba_sec['Day'],
                            'month' : ba_sec['Month'],
                            'weekday' : ba_sec['Weekday']})
                        conf.snapshot_backups[self.get_section_id(section)] = snapshot_backup_conf

                # add current database
                db_configs[db_name] = conf
        return self.filter_configs(db_configs, filters)

    # }}}
    # {{{ Get node usage

    def get_node_usage(self, nid) -> Optional[config]:
        """
        Returns a dict containing all volumes and / or DBs that the given node is part of ('None' otherwise).
        """

        result = config()
        result.volumes = []
        result.dbs = []
        # check volumes
        volumes = self.get_volumes()
        for v in volumes.values():
            if nid in v.nodes:
                result.volumes.append(v.name)
        # check DBs
        dbs = self.get_databases()
        for db in dbs.values():
            if nid in db.nodes:
                result.dbs.append(db.name)

        if len(result.volumes) == 0 and len(result.dbs) == 0:
            return None
        else:
            return result

    # }}}
    # {{{ Get volume usage

    def get_volume_usage(self, vname):
        """
        Returns a list containing all DBs that are using the given volume (includes 'EXA' and remote volumes).
        """

        result = []
        dbs = self.get_databases()
        for db in dbs.values():
            if db.data_volume == vname:
                result.append(db.name)

        if len(result) == 0:
            return None
        else:
            return result

    # }}}
    # {{{ Get bucketfs

    def get_bucketfs(self) -> config:
        """
        Returns a config containing entries for all bucket filesystems and their buckets.
        """
        bfs_config = config()
        for section in self.config.sections:
            if self.is_bucketfs(section):
                bfs_sec = self.config[section]
                bfs_conf = config()
                bfs_conf._sec_name = section
                bfs_conf.name = self.get_section_id(section)
                bfs_conf.owner = tuple([ int(x.strip()) for x in bfs_sec["Owner"].split(":") if x.strip() != "" ])
                bfs_conf.http_port = bfs_sec.as_int("HttpPort")
                bfs_conf.https_port = bfs_sec.as_int("HttpsPort")
                bfs_conf.sync_key = bfs_sec["SyncKey"]
                bfs_conf.sync_period = bfs_sec["SyncPeriod"]
                if "mode" in bfs_sec.keys():
                    bfs_conf.mode = bfs_sec["mode"]
                else:
                    bfs_conf.mode = bfs_sec["mode"] = self.def_bucketfs_mode
                if "bucketvolume" in bfs_sec.keys():
                    bfs_conf.bucketvolume = bfs_sec["bucketvolume"]
                # optional values
                if "Path" in bfs_sec.scalars:
                    bfs_conf.path = bfs_sec["Path"]

                # buckets
                bfs_conf.buckets = config()
                for subsec in bfs_sec.sections:
                    if self.is_bucket(subsec):
                        b_sec = bfs_sec[subsec]
                        b_conf = config()
                        b_conf._sec_name = subsec
                        b_conf.name = self.get_section_id(subsec)
                        b_conf.read_passwd = b_sec["ReadPasswd"]
                        b_conf.write_passwd = b_sec["WritePasswd"]
                        b_conf.public = b_sec.as_bool("Public")
                        if "AdditionalFiles" in b_sec.scalars:
                            b_conf.additional_files = [ f.strip() for f in b_sec["AdditionalFiles"].split(",") if f.strip() != "" ]
                        bfs_conf.buckets[b_conf.name] = b_conf
                bfs_config[bfs_conf.name] = bfs_conf
        return bfs_config

    # }}}
    # {{{ Get ssl conf

    def get_ssl_conf(self) -> config:
        """
        Returns the SSL configuration.
        """
        if "SSL" not in self.config.sections:
            raise EXAConfError("Section 'SSL' does not exist in '%s'!" % (self.conf_path))
        ssl_config = config()
        ssl_sec = self.config["SSL"]
        ssl_config.cert = ssl_sec["Cert"]
        ssl_config.cert_key = ssl_sec["CertKey"]
        ssl_config.cert_auth = ssl_sec["CertAuth"]
        ssl_config.cert_verify = ssl_sec.get ("CertVerify", 'none').lower ()
        ssl_config.cert_dn = ssl_sec.get ("CertDomainName", '').lower ()
        return ssl_config

    # }}}
    # {{{ Get nameservers

    def get_nameservers(self):
        """
        Returns the list of nameservers or an empty list, if there are none.
        """
        res = []
        if "NameServers" in self.config["Global"]:
            res = [ x.strip() for x in self.config["Global"]["NameServers"].split(",") if x.strip() != "" ]
        return res

    # }}}
    # {{{ Get searchdomains

    def get_searchdomains(self):
        """
        Returns the list of nameservers or an empty list, if there are none.
        """
        res = []
        if "SearchDomains" in self.config["Global"]:
            res = [ x.strip() for x in self.config["Global"]["SearchDomains"].split(",") if x.strip() != "" ]
        return res

    # }}}
    # {{{ Get os dir

    def get_os_dir(self, version = None) -> str:
        """
        Returns the absolute path to EXAClusterOS (with the same version number as the EXAConf).
        If version is specified then return the absolute path where that EXAClusterOS version
        would be expected to be installed
        """
        if version is None:
            return self.os_dir
        else:
            cos_major = version.split(".")[0].strip()
            if int(cos_major) < 8:
                return f"/usr/opt/EXASuite-{cos_major}/EXAClusterOS-{version}"
            else:
                return f"/opt/exasol/cos-{version}"

    # }}}
    # {{{ Get db dir

    def get_db_dir(self, version = None) -> str:
        """
        Returns the absolute path to EXASolution (with the same version number as the EXAConf).
        If version is specified (e.g. '6.1.1') then it returns the path where a DB with that
        version should be installed.
        """
        if version is None:
            return self.db_dir
        else:
            db_major = version.split(".")[0].strip()
            if int(db_major) < 8:
                return f"/usr/opt/EXASuite-{db_major}/EXASolution-{version}"
            else:
                return f"/opt/exasol/db-{version}"

    # }}}
    # {{{ Get init cmd

    def get_init_cmd(self) -> Tuple[str, List[str]]:
        """
        Returns the path to the init binary and the arguments to the command as a tuple (to be used with 'os.execv').
        """
        try:
            numa_node = int(os.environ.get('COSLWD_NUMA_NODE', '').strip())
            init_args = ["--cpunodebind=%d" % numa_node, "--membind=%d" % numa_node]
        except:
            init_args = ["--interleave=all"]
        init_args.append(os.path.join(self.os_dir, "bin/exainit"))
        return ("numactl", init_args)

    # }}}
    # {{{ Get user remote volumes file

    def get_user_remote_volumes_file(self, user):
        """
        Returns the absolute path to the given user's remote volume configuration file.
        """
        cf = os.path.join(self.container_root, self.conf_remote_volumes_dir,
                          self.to_uname(user) + "." + str(self.to_uid(user)) + ".conf")
        return cf

    # }}}
    # {{{ Get gui dir

    def get_webterm_dir(self) -> str:
        """
        Returns the full path to the directory containing the GUI files (aka. the "GUI web root").
        """
        return os.path.join(self.get_os_dir(), self.def_webterm_subdir)

    # }}}
    # {{{ Get object storage connection timeout

    def get_storage_connect_timeout(self):
        if "StorageConnectTimeout" in self.config["Global"]:
            return int(self.config["Global"]["StorageConnectTimeout"])
        return self.def_storage_connect_timeout

    # }}}
    # {{{ Get object storage connection request timeout

    def get_storage_request_timeout(self):
        if "StorageRequestTimeout" in self.config["Global"]:
            return int(self.config["Global"]["StorageRequestTimeout"])
        return self.def_storage_request_timeout

    # }}}
    # {{{ Get object storage connection threads

    def get_storage_connection_threads(self):
        if "StorageConnectionThreads" in self.config["Global"]:
            return int(self.config["Global"]["StorageConnectionThreads"])
        return self.def_storage_connection_threads

    # }}}
    # {{{ Get object storage max connections

    def get_storage_max_connections(self):
        if "StorageMaxConnections" in self.config["Global"]:
            return int(self.config["Global"]["StorageMaxConnections"])
        return self.def_storage_max_connections

    # }}}
    # {{{ Get storage proxy URL

    def get_storage_proxy_url(self):
        if "StorageProxyURL" in self.config["Global"]:
            url = self.config["Global"]["StorageProxyURL"]
        else: url = self.def_storage_proxy_url
        if url is None:
            return (None, None, None)
        parsed_url = urlparse(str(url))
        if not parsed_url.scheme:
            return (None, None, None)
        else: scheme = parsed_url.scheme
        if parsed_url.hostname is None:
            host = 'localhost'
        else: host = parsed_url.hostname
        if parsed_url.port is None:
            port = 3128
        else: port = parsed_url.port
        return (scheme, host, port)

    # }}}
    # {{{ Get device type

    def get_device_type(self):
        """
        Returns the device-type used for this cluster. The return value depends on the actual platform.
        """
        if self.platform_is("Docker"):
            return self.config["Docker"]["DeviceType"]
        else:
            return self.def_device_type

    # }}}
    # {{{ Get users

    def get_users(self, filters=None) -> config:
        """
        Returns a config containing all users defined in EXAConf.
        """
        user_configs = config()
        if "Users" in self.config.sections:
            for user in self.config["Users"].sections:
                user_sec = self.config["Users"][user]
                user_conf = config()
                user_conf.group = user_sec["Group"]
                user_conf.id = user_sec.as_int("ID")
                user_conf.login_enabled = user_sec.as_bool("LoginEnabled")
                if "Passwd" in user_sec.scalars:
                    user_conf.passwd = user_sec["Passwd"]
                else: user_conf.passwd = ""
                if "AdditionalGroups" in user_sec.scalars:
                    user_conf.additional_groups = [ g.strip() for g in user_sec["AdditionalGroups"].split(',') if g.strip() != "" ]
                else: user_conf.additional_groups = []
                if "AuthorizedKeys" in user_sec.scalars:
                    user_conf.authorized_keys = [ k.strip() for k in user_sec["AuthorizedKeys"].split(',') if k.strip() != "" ]
                else: user_conf.authorized_keys = []
                user_configs[user] = user_conf
        return self.filter_configs(user_configs, filters)

    # }}}
    # {{{ Get groups

    def get_groups(self, filters=None) -> config:
        """
        Returns a config containing all groups defined in EXAConf.
        """
        group_configs = config()
        if "Groups" in self.config.sections:
            for group in self.config["Groups"].sections:
                group_sec = self.config["Groups"][group]
                group_conf = config()
                group_conf.id = group_sec.as_int("ID")
                group_configs[group] = group_conf
        return self.filter_configs(group_configs, filters)

    # }}}
    # {{{ To uid

    def to_uid(self, uname):
        """
        Returns the user ID of the given username. If it's already an ID, it either returns it directly
        or converts it to an int if it's a string.
        """
        uid = None
        # is it a string?
        if isinstance(uname, str):
            # does the string contain a UID?
            # YES -> convert to int
            try:
                uid = int(uname)
            # NO -> convert to UID
            except ValueError:
                users = self.get_users()
                if uname in users:
                    uid = users[uname].id
        # NO -> it's already a valid uid, do nothing
        else:
            uid = uname

        if uid is None:
            raise EXAConfError("User '%s' does not exist in EXAConf!" % uname)
        return uid

    # }}}
    # {{{ To uname

    def to_uname(self, uid):
        """
        Returns the username of the given user ID. ID can be an int or a string. If ID is already a username, returns it unmodified.
        """
        uname = None
        # is it a string?
        if isinstance(uid, str):
            # does the string contain a UID?
            # YES -> convert to int and then to username
            try:
                uid = int(uid)
                users = self.get_users()
                for u in users.items():
                    if u[1].id == uid:
                        uname = u[0]
            # NO -> it's already a valid username, do nothing
            except ValueError:
                uname = uid
        # NO -> convert uid to username
        else:
            users = self.get_users()
            for u in users.items():
                if u[1].id == uid:
                    uname = u[0]

        if uname is None:
            raise EXAConfError("User with ID %i does not exist in EXAConf!" % int(uid))
        return uname

    # }}}
    # {{{ To gid

    def to_gid(self, gname):
        """
        Returns the group ID of the given groupname. If it's already an ID, it either returns it directly
        or converts it to an int if it's a string.
        """
        gid = None
        # is it a string?
        if isinstance(gname, str):
            # does the string contain a GID?
            # YES -> convert to int
            try:
                gid = int(gname)
            # NO -> convert to GID
            except ValueError:
                groups = self.get_groups()
                if gname in groups:
                    gid = groups[gname].id
        # NO -> it's already a valid gid, do nothing
        else:
            gid = gname

        if gid is None:
            raise EXAConfError("group '%s' does not exist in EXAConf!" % gname)
        return gid

    # }}}
    # {{{ To gname

    def to_gname(self, gid):
        """
        Returns the name of the given group ID. ID can be an int or a string. If ID is already a groupname, returns it unmodified.
        """
        gname = None
        # is it a string?
        if isinstance(gid, str):
            # does the string contain a GID?
            # YES -> convert to int and then to username
            try:
                gid = int(gid)
                groups = self.get_groups()
                for g in groups.items():
                    if g[1].id == gid:
                        gname = g[0]
            # NO -> it's already a valid username, do nothing
            except ValueError:
                gname = gid
        # NO -> convert gid to groupname
        else:
            groups = self.get_groups()
            for g in groups.items():
                if g[1].id == gid:
                    gname = g[0]

        if gname is None:
            raise EXAConfError("Group with ID %i does not exist in EXAConf!" % int(gid))
        return gname

    # }}}
    # {{{ Filter configs

    def filter_configs(self, configs: config, filters: Optional[Dict[str, Any]]) -> config:
        """
        Applies the given filters (a dict) to the given config object by removing all items that
        don't match the filter criteria. It assumes that 'configs' is in fact a dict with config
        objects as values (e. g. some volumes or database configurations).
        """
        if not filters:
            return configs
        configs_copy = configs.copy ()
        for item in configs.items(): # use a copy!
            for f in filters.items():
                if f[0] in item[1] and f[1] != item[1][f[0]]:
                    del configs_copy[item[0]]
                    break
        return configs_copy

    # }}}
    # {{{ Check if SaaS is enabled

    def is_saas(self) -> bool:
        if 'SaaS' not in self.config.sections: return False
        if 'Enabled' not in self.config['SaaS']: return False
        return bool(self.config['SaaS']['Enabled'])

    # }}}
    # {{{ Get SaaS DB name

    def saas_db_name(self) -> Optional[str]:
        if not self.is_saas(): return None
        if 'DBName' not in self.config['SaaS']: return None
        return str(self.config['SaaS']['DBName'])

    # }}}
    # {{{ Check chargebee credentials are provided

    def chargebee_credentials_provided(self) -> bool:
        try:
            idp_config = json.loads(base64.b64decode(self.get_idp_config()).decode())
            chargebee_config = idp_config["chargebee"]
            return True if chargebee_config["api_key"] and chargebee_config["site"] else False
        except:
            return False

    # }}}
    # {{{ Retrieve item_family_id
    def get_item_family_id(self) -> Optional[str]:
        result: Optional[str] = (self.config.get("Chargebee") or {}).get("ItemFamilyId") or None
        return result
    # }}}
    # {{{ Check chargebee already configured

    def chargebee_configured(self) -> bool:
        try:
            configured: bool = self.config["Chargebee"].as_bool("Configured")
            return configured
        except:
            return False

    # }}}
    # {{{ Get configuration of external identity provider

    def get_idp_config(self):
        return self.config['IdP']['Config']

    # }}}

    ################### SIMPLE API (dedicated functions for selected actions) ##############

    # {{{ Add node disk

    def add_node_disk(self, node_id, disk, component = None, devices = None, drives = None,
                      ephemeral = False, overwrite_existing = False, no_odirect = False, commit=True) -> None:
        """
        Adds a new disk to the given node in EXAConf.

        - 'component' is a string that denotes the component that should use this disk: ['exastorage'|'db'|'swap']
        - 'devices' is a list of device names
        - 'drives' is a list of drive IDs
        - 'overwrite_existing' will overwrite an existing disk with the same name

        If both 'devices' and 'drives' are given, 'devices' will take precedence. If only 'drives' is given,
        the drives in that list will be used to create new devices for the disk (during the next startup).
        """

        nodes_conf = self.get_nodes()
        if str(node_id) not in nodes_conf:
            raise EXAConfError("Node %s does not exist in '%s'." % (node_id, self.conf_path))
        node_conf = nodes_conf[str(node_id)]
        # check if a disk with same name already exists
        if "disks" in node_conf and disk in node_conf.disks and overwrite_existing is False:
            raise EXAConfError("Node %s alrady contains disk '%s'." % (str(node_id), disk))
        # create entry for the disk
        disk_entry = config()
        if component is not None:
            disk_entry.component = component
        if devices is not None:
            disk_entry.devices = devices
        if drives is not None:
            disk_entry.drives = drives
        disk_entry.ephemeral = ephemeral
        if no_odirect:
            disk_entry.direct_io = False
        node_conf.disks[disk] = disk_entry

        self.set_node_conf(node_conf, node_conf.id, commit)

    # }}}
    # {{{ Remove node disk

    def remove_node_disk(self, node_id, disk) -> None:
        """
        Removes the given storage disk (or all disks) from the given node.
        """

        nodes_conf = self.get_nodes()
        if str(node_id) not in nodes_conf:
            raise EXAConfError("Node %s does not exist in '%s'." % (node_id, self.conf_path))
        node_conf = nodes_conf[str(node_id)]
        if "disks" in node_conf:
            if disk == "_all":
                node_conf.disks.clear()
            else:
                for d in tuple(node_conf.disks.values()):
                    if d.name == disk:
                        del node_conf.disks[d.name]

        self.set_node_conf(node_conf, node_conf.id, remove_disks=True)

    # }}}
    # {{{ Add node device

    def add_node_device(self, node_id, disk, device, path = None, commit=True) -> None:
        """
        Adds the given device to the given disk on the given node. If 'path' is specified, a mapping is also added.

        The given disk has to exist (can be created with 'add_node_disk()').
        """

        nodes_conf = self.get_nodes()
        if str(node_id) not in nodes_conf:
            raise EXAConfError("Node %s does not exist in '%s'." % (node_id, self.conf_path))
        node_conf = nodes_conf[str(node_id)]
        if "disks" not in node_conf or disk not in node_conf.disks:
            raise EXAConfError("Node %s does not have a disk named '%s'." % (str(node_id), disk))
        node_disks = node_conf.disks
        # add devices
        if "devices" not in node_disks[disk]:
            node_disks[disk].devices = []
        elif device in node_disks[disk].devices:
            raise EXAConfError("Disk '%s' of node %s already contains device '%s'." % (disk, str(node_id), device))
        node_disks[disk].devices.append(device)
        if path and path != "":
            if "mapping" not in node_disks[disk]:
                node_disks[disk].mapping = []
            node_disks[disk].mapping.append((device,path))

        self.set_node_conf(node_conf, node_conf.id, commit)

    # }}}
    # {{{ Remove node device

    def remove_node_device(self, node_id, disk, device, remove_empty_disk=True, commit=True) -> None:
        """
        Removes the given device from the node and disk. Also deletes the disk
        if it does not contain other devices and 'remove_empty_disk' is True.
        """

        nodes_conf = self.get_nodes()
        if str(node_id) not in nodes_conf:
            raise EXAConfError("Node %s does not exist in '%s'." % (node_id, self.conf_path))
        node_conf = nodes_conf[str(node_id)]
        if "disks" not in node_conf:
            raise EXAConfError("Node %s does not have any disks." % node_id)
        node_disks = node_conf.disks
        if disk not in node_disks:
            raise EXAConfError("Node %s does not have a disk named '%s'." % (node_id, disk))
        if "devices" not in node_disks[disk]:
            raise EXAConfError("Disk '%s' of node %s does not have any devices." % (disk, node_id))
        # delete device
        node_disks[disk].devices = [ d for d in node_disks[disk].devices if d != device ]
        if len(node_disks[disk].devices) == 0:
            del node_disks[disk]["devices"]
        # delete mapping
        if "mapping" in node_disks[disk]:
            node_disks[disk].mapping = [ m for m in node_disks[disk].mapping if m[0] != device ]
            if len(node_disks[disk].mapping) == 0:
                del node_disks[disk]["mapping"]
        # delete disk if empty (and requested)
        if remove_empty_disk and "devices" not in node_disks[disk]:
            del node_disks[disk]

        self.set_node_conf(node_conf, node_conf.id, remove_disks = remove_empty_disk, commit=commit)

    # }}}
    # {{{ Remove node drives

    def remove_node_drives(self, node_id, disk, drives_to_remove) -> None:
        """
        Removes given drives from the disk.
        """
        assert type(drives_to_remove) is list

        nodes_conf = self.get_nodes()
        if str(node_id) not in nodes_conf:
            raise EXAConfError("Node %s does not exist in '%s'." % (node_id, self.conf_path))
        node_conf = nodes_conf[str(node_id)]
        if "disks" not in node_conf:
            raise EXAConfError("Node %s does not have any disks." % node_id)
        if disk not in node_conf.disks:
            raise EXAConfError('Node {} does not have disk {}'.format(node_id, disk.full()))

        disk_conf = node_conf.disks[disk]
        disk_conf.drives = [ d for d in disk_conf.drives if d not in drives_to_remove ]

        if not disk_conf.drives:
            del disk_conf["drives"]

        self.set_node_conf(node_conf, node_conf.id)

    # }}}
    # {{{ Use disk for volumes

    def use_disk_for_volumes(self, disk, bytes_per_node, vol_type=None, min_vol_size = None, vol_resize_step=None) -> None:
        """
        Adds the given disk to all volumes of the given type that don't have a disk assigned yet.
        The given 'bytes_per_node' space is distributed equally across all suitable volumes.
        """

        # we only consider volumes without disks
        filters = {"disk": None}
        if vol_type and vol_type != "":
            filters["type"] = vol_type
        volumes = self.get_volumes(filters=filters)
        if len(volumes) == 0:
            return
        bytes_per_volume_node = bytes_per_node // len(volumes)

        for volume in volumes.items():
            vol_sec_name = self.volume_exists(volume[0])
            if vol_sec_name is None: continue
            vol_sec = self.config[vol_sec_name]
            vol_sec["Disk"] = disk
            # decrease volume size to the next multiple of the vol_resize_step (if given)
            if vol_resize_step and vol_resize_step > 0:
                vol_size = bytes2units((vol_resize_step * (bytes_per_volume_node // vol_resize_step)) // volume[1].redundancy)
                if units2bytes(vol_size) < vol_resize_step:
                    vol_size = bytes2units(vol_resize_step)
            else:
                vol_size = bytes2units(bytes_per_volume_node // volume[1].redundancy)
            # check size if given
            if min_vol_size and units2bytes(vol_size) < min_vol_size:
                raise EXAConfError("Can't assign disk to volume because resulting size '%s' is below min. size %s!" % (vol_size, bytes2units(min_vol_size)))
            vol_sec["Size"] = vol_size

        self.commit()

    # }}}
    # {{{ Set node network

    def set_node_network(self, node_id, private=None, public=None) -> None:
        """
        Sets the private and / or public network of the given node.
        """

        nodes = self.get_nodes()
        if node_id not in nodes:
            raise EXAConfError("Node %s does not exist in '%s'!" % (str(node_id), self.conf_path))

        node_conf = nodes[str(node_id)]

        if private and private != "":
            node_conf.private_net = private
            if 'private_ip' in node_conf:
                del node_conf.private_ip #delete old IP
        if public and public != "":
            node_conf.public_net = public
            if 'public_ip' in node_conf:
                del node_conf.public_ip #delete old IP

        self.set_node_conf(node_conf, node_id)

    # }}}
    # {{{ Set node network

    def set_node_ip(self, node_id, private=None, public=None) -> None:
        """
        Sets the private and / or public IP address of the given node.
        """
        node_id = str(node_id)
        nodes = self.get_nodes()
        if node_id not in nodes:
            raise EXAConfError(f"Node {node_id} does not exist({nodes})!")

        node_conf = nodes[node_id]

        if private and private != "":
            node_conf.private_ip = private
            if 'private_net' in node_conf:
                del node_conf.private_net #don't overwrite old network
        if public and public != "":
            node_conf.public_ip = public
            if 'public_net' in node_conf:
                del node_conf.public_net #don't overwrite old network

        self.set_node_conf(node_conf, node_id)

    # }}}

    ##############################  DOCKER EXCLUSIVE STUFF ##################################

    # {{{ Get docker image

    def get_docker_image(self):
        """
        Returns the name of the docker image used for this cluster.
        """
        return self.config["Docker"]["Image"]

    # }}}
    # {{{ Update docker image

    def update_docker_image(self, image) -> None:
        """
        Replaces the docker image for all containers of this cluster with the given one.
        The cluster has to be restarted in order to create new containers from the
        new image.
        """
        self.config["Docker"]["Image"] = image
        self.commit()

    # }}}
    # {{{ Get docker root directory

    def get_docker_root_dir(self):
        """
        Returns the docker root-directory of this cluster.
        """
        return self.config["Docker"]["RootDir"]

    # }}}
    # {{{ Get docker node volumes

    def get_docker_node_volumes(self) -> config:
        """
        Returns a config containing the absolute path to the docker-volume of all nodes.
        """

        node_volumes = config()
        for section in self.config.sections:
            if self.is_node(section):
                node_volumes[self.get_section_id(section)] = os.path.join(self.root, self.config[section]["DockerVolume"])
        return node_volumes

    # }}}
    # {{{ Get docker conf

    def get_docker_conf(self) -> config:
        """
        Returns a config object containing all entries from the 'Docker' section.
        """

        if not self.platform_is("Docker"):
            raise EXAConfError("This function is only supported for the 'Docker' platform!")
        conf = config()
        docker_sec = self.config["Docker"]
        conf.root_dir = docker_sec["RootDir"]
        conf.image = docker_sec["Image"]
        conf.device_type = docker_sec["DeviceType"]
        # optional values
        if "Privileged" in docker_sec.scalars and docker_sec["Privileged"] != "":
            conf.privileged = docker_sec.as_bool("Privileged")
        else:
            conf.privileged = self.def_docker_privileged
        if "CapAdd" in docker_sec.scalars and docker_sec["CapAdd"] != "":
            conf.cap_add = [str(c) for c in docker_sec["CapAdd"].split(",") if c.strip() != ""]
        else:
            conf.cap_add = []
        if "CapDrop" in docker_sec.scalars and docker_sec["CapDrop"] != "":
            conf.cap_drop = [str(c) for c in docker_sec["CapDrop"].split(",") if c.strip() != ""]
        else:
            conf.cap_drop = []
        if "NetworkMode" in docker_sec.scalars and docker_sec["NetworkMode"] != "":
            conf.network_mode = docker_sec["NetworkMode"]
        else:
            conf.network_mode = self.def_docker_network_mode
        if "DefaultVolumes" in docker_sec.scalars: # may be emtpy string to deactivate them
            conf.default_volumes = [v.strip() for v in docker_sec["DefaultVolumes"].split(",") if v.strip() != ""]
        else:
            conf.default_volumes = self.def_docker_volumes
        if "AdditionalVolumes" in docker_sec.scalars and docker_sec["AdditionalVolumes"] != "":
            conf.additional_volumes = [v.strip() for v in docker_sec["AdditionalVolumes"].split(",") if v.strip() != ""]
        else:
            conf.additional_volumes = []
        if "IpcMode" in docker_sec.scalars and docker_sec["IpcMode"] != "":
            conf.ipc_mode = docker_sec["IpcMode"]
        else:
            conf.ipc_mode = self.def_docker_ipc_mode
        return conf

    # }}}
    # {{{ Set docker privileged

    def set_docker_privileged(self, enabled) -> None:
        """
        Set the flag for privileged mode to 'True' or 'False'. Can be used to signal that a container has been
        started without privileged mode. May raise exceptions if the current configuration is not supported in
        unprivileged containers (e. g. device-type 'block').
        """
        if not self.platform_is("Docker"):
            raise EXAConfError("This function is only supported for the 'Docker' platform!")
        self.config["Docker"]["Privileged"] = str(enabled)
        # sanity checks
        if enabled is False:
            if self.get_device_type() != "file":
                raise EXAConfError("Only device-type 'file' is supported in unprivileged Docker containers (current mode is '%s')!" % self.get_device_type())
        self.commit()

    # }}}

    ############################# UTILITY FUNCTIONS #################################

    # {{{ Check fix local dev path

    def check_fix_local_dev_path(self, dev_file):
        """
        Compatibility function: checks if the given device has a meta file (old format)
        and returns a tuple of the data and metafile path if so (see SPOT-6478).

        ONLY WORKS FOR LOCAL FILES!
        """
        try:
            # NOTE: don't use 'isfile()' because it returns False for block device files
            if os.path.exists(dev_file):
                return (dev_file,)
            else:
                data_file = dev_file + self.data_dev_suffix
                if os.path.exists(data_file):
                    return (data_file, dev_file + self.meta_dev_suffix)
                else:
                    raise EXAConfError("Could not find device file '%s'!" % dev_file)
        except Exception as e:
            raise EXAConfError("Failed to check device file '%s': %s" % (dev_file, e))

    # }}}

    def has_tree(self, config, tree):
        if len(tree) < 2:
            return True
        if not tree[1] in config[tree[0]]:
            return False
        return self.has_tree(config[tree[0]], tree[1:])


    def get_cored_use_privileged_ports(self):
        if self.has_tree(self.config, ["Global", "CoredUsePrivilegedPorts"]):
            return self.config["Global"].as_bool("CoredUsePrivilegedPorts")
        else:
            return False


    def set_cored_use_privileged_ports(self, activate: bool) -> None:
        self.config["Global"]["CoredUsePrivilegedPorts"] = bool(activate)


    def get_cored_allow_only_same_subnet(self):
        if self.has_tree(self.config, ["Global", "CoredAllowOnlySameSubnet"]):
            return self.config["Global"].as_bool("CoredAllowOnlySameSubnet")
        else:
            return False


    def set_cored_allow_only_same_subnet(self, activate: bool) -> None:
        self.config["Global"]["CoredAllowOnlySameSubnet"] = bool(activate)


    def get_cored_subnets(self):
        if self.has_tree(self.config, ["Global", "CoredSubnets"]):
            return self.config["Global"]["CoredSubnets"]
        else:
            return ""


    def set_cored_subnets(self, subnets: str) -> None:
        self.config["Global"]["CoredSubnets"] = subnets
