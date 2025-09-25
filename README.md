> [!WARNING]
> Please note that this is an open source project which is __not officially supported__ by Exasol.
> We will try to help you as much as possible, but can't guarantee anything since this is not an official Exasol product.

> [!IMPORTANT]
> Please note that the underlying base image for the Exasol 7.1 Docker image is CentOS 7, which has reached its end-of-life (EOL).
> Consequently, official support for CentOS 7 is no longer available. __Please use Exasol 8__.

> [!NOTE]
> Since 2024-04-14 this repository only contains __documentation__ for the Exasol Docker image.
> For the binaries, please refer to [exasol/docker-db on DockerHub](https://hub.docker.com/r/exasol/docker-db)

> [!NOTE]
> GitHub issues are disabled on this repository on purpose. Exasol Docker DB is a project mainly intended for automatic testing of Exasol deployments.

# Exasol Docker version

Exasol is a high-performance, in-memory, MPP database specifically designed for analytics. 
This repository contains a dockerized version of the Exasol DB for testing purposes.

The dockerized version of Exasol in this repository can be used with up to 10GiB of data. If you need more please get in contact with us via https://exasol.com/get-in-touch.

Your use of this repository is subject to the [Exasol Terms & Conditions](https://www.exasol.com/terms-and-conditions)

###### Please note that this is an open source project which is *not officially supported* by Exasol. We will try to help you as much as possible, but can't guarantee anything since this is not an official Exasol product.

Currently supported features:
- create / start / stop a database in a virtual cluster
- use the UDF framework
- expose ports from containers on the local host
- update the virtual cluster
- create backups on archive volumes

# Table of contents
[Requirements](#requirements)

[Recommendations](#recommendations)

[Creating a stand-alone Exasol container (`docker run`)](#creating-a-stand-alone-exasol-container)

[Creating a multi-host Exasol cluster (by connecting multiple containers)](#creating-a-multi-host-exasol-cluster)

[Managing disks and devices](#managing-disks-and-devices)

[Installing custom JDBC drivers](#installing-custom-jdbc-drivers)

[Installing Oracle drivers](#installing-oracle-drivers)

[Connecting to the database](#connecting-to-the-database)

[Troubleshooting](#troubleshooting)


# Requirements
 
## Docker

The Exasol Docker image has been developed and tested with Docker 18.03.1-ce (API 1.37) on Fedora 27. It may also work with earlier versions, but that is not guaranteed.
 
Please see [the Docker installation documentation](https://docs.docker.com/installation/) for details on how to upgrade your Docker daemon.
 
### Privileged mode

Docker privileged mode is required for permissions management, UDF support and environment configuration and validation (sysctl, hugepages, block-devices, etc.).

## CPU Architecture

Exasol currently only supports 64-bit *x86-64* platforms with SSSE3 featured CPUs (Intel64 or AMD64) and is for this architecture optimized. See also the [System Requirements](https://docs.exasol.com/db/7.1/administration/on-premise/installation/system_requirements.htm).

## Host OS

We currently only support Docker on Linux. If you are using a Windows host you'd have to create a Linux VM.

The host OS must support O_DIRECT access for the Exasol containers (see [Troubleshooting](#troubleshooting)).

## Host environment

### Memory
Each database instance needs at least **2 GiB RAM**. We recommend that the host reserves at least **4 GiB RAM** for each running Exasol container.

### Services

Make sure that **NTP** is configured correctly on the host. Also, the **RNG** daemon must be running in order to provide enough entropy for the Exasol services in the container.       

# Recommendations
 
## Performance optimization

We strongly recommend to set the CPU governor on the host to `performance`, in order to avoid serious performance problems. There are various tools to do that, depending on your distribution. Usually, the following command works:
```console
for F in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance >$F; done
```

## Hugepages

We recommend to enable hugepages for hosts with at least 64GB RAM. In order to do so, you have to set the `Hugepages` option in EXAConf to either `auto`, `host` or the nr. of hugepages per container. 

If you set it `auto`, the nr. of hugepages will be determined automatically, depending on the DB settings. 

When setting it to `host` the nr. of hugepages from the host system will be used (i. e. `/proc/sys/vm/nr_hugepages` will not be changed). However, `/proc/sys/vm/hugetlb_shm_group` will always be set to an internal value!
 
## Resource limitation

It's possible to limit the resources of your Exasol container with the following `docker run` options: 
```console
docker run --cpuset-cpus="1,2,3,4" --memory=20g --memory-swap=20g --memory-reservation=10g exasol/docker-db:<version>
```
This is especially recommended if you have multiple Exasol containers (or other services) on the same host. In that case, you should evenly distribute the available CPUs and memory throughout your Exasol containers.

See [https://docs.docker.com/config/containers/resource_constraints/](https://docs.docker.com/config/containers/resource_constraints/) for more options.

# Creating a stand-alone Exasol container

You can create an Exasol container from the Exasol docker image using the following command:

```console
$ docker run --name exasoldb -p 127.0.0.1:9563:8563 --detach --privileged --stop-timeout 120  exasol/docker-db:<version>
```
The available version can be found on [Docker-Hub](https://hub.docker.com/r/exasol/docker-db/tags).

In this example port 8563 (within the container) is exposed on the local port 9563. Use this port to connect to the DB.

## Making the data of a container persistent

All data, configuration and logfiles of an Exasol container are stored below `/exa`. With the command above, this data is lost when the container is removed. However, you can make it persistent by mounting a volume into the container at `/exa`, for example:

```console
$ docker run --name exasoldb  -p 127.0.0.1:9563:8563 --detach --privileged --stop-timeout 120 -v exa_volume:/exa exasol/docker-db:<version>
```

See [the Docker volumes documentation](https://docs.docker.com/engine/tutorials/dockervolumes/) for more examples on how to create and manage persistent volumes.

## Stopping an Exasol container

It is important to make sure that the database has been shut down correctly before stopping a container. A high stop-timeout (see example above) increases the chance that the DB can be shut down gracefully before the container is stopped, but it's not guaranteed. It's better to stop the DB manually by executing the following command within the container (after attaching to it):

```console
$ dwad_client stop-wait DB1
```

Or from outside the container:

```console
$ docker exec -ti exasoldb dwad_client stop-wait DB1
```

## Updating the persistent volume of a stand-alone Exasol container

An existing persistent volume can be updated (for use with a later version of an Exasol image) by calling the following command with the *new* image:

```console
$ docker run --rm -v exa_volume:/exa exasol/docker-db:<new version> update-sc
```

If everything works correctly, you should see output similar to this:

```console
Updating EXAConf '/exa/etc/EXAConf' from version '7.0.2' to '7.0.3'
Container has been successfully updated!
- Image ver. :  7.0.2 --> 7.0.3
- DB ver.    :  7.0.2 --> 7.0.3
- OS ver.    :  7.0.2 --> 7.0.3
```

After that, a new container can be created (from the new image) using the old / updated volume.


# Creating a multi-host Exasol cluster

It is possible to create multiple containers on different hosts and connect them to a cluster (one container per host). 

## 1. Create the configuration 

First you have to create the configuration for the cluster. There are two possible ways to do so:
 
### a. Create an /exa/ directory that stores all persistent data from one container (RECOMMENDED):

Execute the following command (`--num-nodes` is the number of containers in the cluster):

```console
$ export CONTAINER_EXA="$HOME/container_exa/"
$ docker run -v "$CONTAINER_EXA":/exa --rm -i exasol/docker-db:<version> init-sc --template --num-nodes 3
```

After the command has finished, the directory `$CONTAINER_EXA` contains all subdirectories as well as an EXAConf template (in `/etc`). 

**NOTE: you need to add `--privileged` if the host directory belongs to root.**
 
### b. Create an EXAConf template

You can create a template file and redirect it to wherever you want by executing: 

```console
$ docker run --rm -i exasol/docker-db:<version> -q init-sc --template --num-nodes 3 -p > ~/MyExaConf
```

**NOTE: we recommend creating an /exa/ template directory and the following steps assume that you did so. If you choose to only create the EXAConf file, you have to build a new Docker image with it and create the EXAStorage devices files within that image.**

## 2. Complete the configuration

The configuration has to be completed before the cluster can be started.

#### Private network of all nodes:
```console
[Node : 11]
    PrivateNet = 10.10.10.11/24 # <-- replace with the real network
```
You can also change the IP using the `exaconf` CLI tool from the Exasol image:
```console
docker run --rm -v $CONTAINER_EXA:/exa exasol/docker-db:<version> exaconf modify-node -n 11 -p 10.10.10.11/24
```

#### EXAStorage devices on all nodes:
```console
[[Disk : disk1]]
    Devices = dev.1    #'dev.1' must be located in '/exa/data/storage'
```

For more information on device management, see [Managing disks and devices](#managing-disks-and-devices).

**NOTE: You can leave this entry as it is if you create the devices as described below.**

#### EXAVolume sizes:
```console
[EXAVolume : DataVolume1]
    Type = data
    Nodes = 11, 12, 13
    Disk = disk1
    # Volume size (e. g. '1 TiB')
    Size =  # <-- enter volume size here
```
You can also change the volume size using the `exaconf` CLI tool from the Exasol image:
```console
docker run --rm -v $CONTAINER_EXA:/exa exasol/docker-db:<version> exaconf modify-volume -n DataVolume1 -s 1TiB
```
  
#### Network port numbers (optional)

If you are using the host network mode (see "Start the cluster" below), you may have to adjust the port numbers used by the Exasol services. The one that's most likely to collide is the SSH daemon, which is using the well-known port 22. You can change it in EXAConf:
```console
[Global]
    SSHPort = 22  # <-- replace with any unused port number
```

The other Exasol services (e. g. Cored, BucketFS and the DB itself) are using port numbers above 1024. However, you can change them all by editing EXAConf.
 
#### Nameservers (optional):
```console
[Global]
    ...
    # Comma-separated list of nameservers for this cluster.
    NameServers =
```
 
## 3. Copy the configuration to all nodes

Copy `$CONTAINER_EXA` to all cluster nodes (the exact path is not relevant, but should be identical on all nodes).

## 4. Create the EXAStorage device files

You can create device files by executing (**on each node**):

```console
$ truncate -s 1G $CONTAINER_EXA/data/storage/dev.1
```

This will create a sparse file of 1GB (1000 blocks of 1 MB) that holds the data. Adjust the size of the data file to your needs. Repeat this step to create multiple file devices.

**IMPORTANT: Each device should be slightly bigger (~1%) than the required space for the volume(s), because a part of it will be reserved for metadata and checksums.**

For more information on device management, see [Managing disks and devices](#managing-disks-and-devices).

## 5. Start the cluster

The cluster is started by creating all containers individually and passing each of them its ID from the EXAConf. For `n11` the command would be:

```console
$ docker run --detach --network=host --privileged -v $CONTAINER_EXA:/exa exasol/docker-db:<version> init-sc --node-id 11
```

**NOTE: this example uses the host network stack, i. e. the containers are directly accessing a host interface to connect to each other. There is no need to expose ports in this mode: they are all accessible on the host.**
 
# Managing disks and devices
 
All EXAStorage devices have to be located below `/exa/data/storage/` (within the container). 

## Creating EXAStorage file devices

You can create file devices by executing (**on each node**):

```console
$ truncate -s 1G $CONTAINER_EXA/data/storage/dev.2
```
or (alternatively):
```console
$ dd if=/dev/zero of=$CONTAINER_EXA/data/storage/dev.2 bs=1M count=1 seek=999
```

This will create a sparse file of 1GB (1000 blocks of 1 MB) that holds the data. Adjust the size to your needs. 

**NOTE:** you can also create the files in any place you like and mount them to `/exa/data/storage/` with `docker run -v /path/to/dev.x:/exa/data/storage/dev.x`.

**IMPORTANT: Each device should be slightly bigger (~1%) than the required space for the volume(s), because a part of it will be reserved for metadata and checksums.**

## Adding devices to EXAConf

After the devices have been created, they need to be added to EXAConf. You can either do this manually by editing EXAConf:

```console
[Node : 11]
    [[Disk : disk1]]
        Devices = dev.1, dev.2
```
or use the `exaconf` CLI tool from the Exasol image:
```console
docker run --rm -v $CONTAINER_EXA:/exa exasol/docker-db:<version> exaconf add-node-device -D disk1 -d dev.2 -n 11
```

## Adding disks to EXAConf

You can add additional disks to each node in EXAConf (a `disk` is a group of devices that is assigned to a volume). This may be useful if you want to use different devices for different volumes.

Similar to adding devices, you can either manually edit EXAConf:
```console
[Node : 11]
    [[Disk : disk1]]
        Devices = dev.1, dev.2
    [[Disk : disk2]]
        Devices = dev.3, dev.4
```
or use the `exaconf` CLI tool from the Exasol image:
```console
docker run --rm -v $CONTAINER_EXA:/exa exasol/docker-dev-7.0.0:juk exaconf add-node-disk -D disk2 -n 11
docker run --rm -v $CONTAINER_EXA:/exa exasol/docker-db:<version> exaconf add-node-device -D disk2 -d dev.3 -n 11
docker run --rm -v $CONTAINER_EXA:/exa exasol/docker-db:<version> exaconf add-node-device -D disk2 -d dev.4 -n 11
```
**NOTE: Don't forget to copy the modified EXAConf to all other nodes.**

## Enlarging an EXAStorage file device

If you need to enlarge a file device of an existing Exasol container, you can use the following commands to do so:

### 1. Open a terminal in the container:

$ docker exec -ti <containername> /bin/bash

### 2. Physically enlarge the file (e. g. by 10GB):

$ truncate --size=+10GB /exa/data/storage/dev.1

### 3. Logically enlarge the device (i. e. tell EXAStorage about the new size):

$ cshdd --enlarge --node-id 11 -h /exa/data/storage/dev.1[.data]

### 4. Repeat these steps for all devices and containers

# Installing custom JDBC drivers

Custom JDBC drivers can be added by uploading them into a bucket. The bucket and path for the drivers can be configured in each database section of EXAConf. The default configuration is:

```console
[DB : DB1]
    ...
    # OPTIONAL: JDBC driver configuration
    [[JDBC]]
        BucketFS = bfsdefault
        Bucket = default
        # Directory within the bucket that contains the drivers
        Dir = drivers/jdbc
```

In order for the database to find the driver, you need to upload it into a subdirectory of `drivers/jdbc` of the default bucket (which is automatically created if you don't modify EXAConf). See the section `Installing Oracle drivers` for help on how to upload files to BucketFS.

In addition to the driver file(s), you also have to create and upload a file called `settings.cfg` , that looks like this:

```console
DRIVERNAME=MY_JDBC_DRIVER
JAR=my_jdbc_driver.jar
DRIVERMAIN=com.mydriver.jdbc.Driver
PREFIX=jdbc:mydriver:
FETCHSIZE=100000
INSERTSIZE=-1
```

Change the variables DRIVERNAME, JAR, DRIVERMAIN and PREFIX according to your driver and upload the file (into the **same directory** as the driver itself). Please make sure that every line, including the final line ends with a newline (LF) character.

**IMPORTANT: Do not modify the last two lines!**

If you use the default bucket and the default path, you can add multiple JDBC drivers during runtime. The DB will find them without having to restart it (as long as they're located in a subfolder of the default path). Otherwise, a container restart is required. 
 

# Installing Oracle drivers

Oracle drivers can be added by uploading them into a bucket. The bucket and path for the drivers can be configured in each database section of EXAConf. The default configuration is:

```console
[DB : DB1]
    ...
    # OPTIONAL: Oracle driver configuration
    [[ORACLE]]
        BucketFS = bfsdefault
        Bucket = default
        # Directory within the bucket that contains the drivers
        Dir = drivers/oracle
```

In order for the database to find the driver, you have to upload it to `drivers/oracle` of the default bucket (which is automatically created if you don't modify EXAConf).

You can use `curl` for uploading, e. g.:

```
$ curl -v -X PUT -T instantclient-basic-linux.x64-12.1.0.2.0.zip http://w:PASSWORD@10.10.10.11:2580/default/drivers/oracle/instantclient-basic-linux.x64-12.1.0.2.0.zip
```

Replace `PASSWORD` with the `WritePasswd` for the bucket. See [Connecting to BucketFS](#connecting-to-bucketfs) for details.

**NOTE: The only currently supported driver version is 12.1.0.2.0. Please download the package `instantclient-basic-linux.x64-12.1.0.2.0.zip` from oracle.com and upload it as described above.**
 
# Connecting to the database

Connecting to the default Exasol DB inside a Docker container is not different from the "normal" version. You can use any supported client and authenticate with username `sys` and password `exasol`. 

Please refer to the [offical manual](https://www.exasol.com/portal/display/DOC/Database+User+Manual) for further information.

## Connecting to BucketFS

The default port of the BucketFS inside of the docker container is `2580`. You must however expose this port in the docker command so that you can access it from outside of the container. For example by adding `-p 2580:2580` to your `docker run` command.

Read and write passwords for the BucketFS are autogenerated.
You can find them in the EXAConf. They are base64-encoded and can be decoded like this:

```
$ awk '/WritePasswd/{ print $3; }' EXAConf | base64 -d
```

Please refer to the [BucketFS manual](https://docs.exasol.com/database_concepts/bucketfs/access_control.htm) for information on how to access files in the BucketFS.

# Troubleshooting

### Error after modifying EXAConf

> ERROR::EXAConf: Integrity check failed! The stored checksum 'a2f605126a2ca6052b5477619975664f' does not match the actual checksum 'f9b9df0b9247b4696135c135ea066580'. Set checksum to 'COMMIT' if you made intentional changes.

If you see a message similar to the one above, you probably modified an EXAConf that has already been used by an Exasol container or `exadt`. It is issued by the EXAConf integrity check that protects EXAConf from accidental changes and detects file corruption.

In order to solve the problem you have to set the checksum within EXAConf to 'COMMIT'. It can be found in the 'Global' section, near the top of the file:

```console
[Global]
...
Checksum = COMMIT
...
```
### Error during container start because of missing O_DIRECT support

> WORKER::ERROR: Failed to open device '/exa/data/storage/dev.1.data'!
> WORKER:: errno = Invalid argument

If the container does not start up properly and you see an error like this in the logfiles below `/exa/logs/cored/`, your filesystem probably does not support `O_DIRECT ` I/O mode. 

We strongly recommend to always use O_DIRECT, but if you really can't, then you can disable O_DIRECT mode by adding a line to each disk in EXAConf:

```console
[Node : 11]
    ...
    [[Disk : disk1]]
        DirectIO = False
```

**IMPORTANT: Disabling O_DIRECT mode may cause significantly higher memory usage and fluctuating I/O throughput!**

### Error when starting the database

> Could not start database: system does not have enough active nodes or DWAd was not able to create startup parameters for system

If all containers started successfully but the database did not and you see a message similar to this in the output of `docker logs`, you may not have enough memory in your host(s). The DB needs at least 2 GiB RAM per node (that's also the default value in EXAConf).
