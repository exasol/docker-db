# Resource-based EXAConf Sizing for Container Deployments

This guide describes how to set memory-related EXAConf values.
When deploying into a container, the container currently does not automatically respect limits set by the container manager,
so values need to be adjusted manually.
When moving the container to a host with different resouces, EXAConf memory values need to be adjusted manually.

## Scope

This document covers these settings:

- database `MemSize` (effective `OVERALL_DBRAM`)
- global `Hugepages`
- database `AutoStart` (optional operational guard)

There are settings that are not covered in this document, such as `MaxSystemHeapMemory` and other database settings that may be relevant to memory usage. These are not covered here but may need to be set as well.

## Core Behavior

### 1. DBRAM safety at database start

Database startup applies a safety cap to `OVERALL_DBRAM` based on available node memory.
If configured DBRAM is too high, it is reduced before start.

This cap currently does not take into account container memory limits.
When using a container, the calculation below must be done manually

### 2. Maximum DBRAM formula

Maximum DBRAM in MiB is calculated as:

- if `node_memory_in_mib < 2048`: `int(node_memory_in_mib * 0.7) * db_nodes_number`
- otherwise:
  - `node_mem_gb = node_memory_in_mib / 1024.0`
  - `os_memory_gb = sqrt(0.8 * node_mem_gb) * log10(node_mem_gb)`
  - `max_db_mem_mib = (node_memory_in_mib - int(os_memory_gb * 1024)) * db_nodes_number`

The runtime cap uses the smallest active node memory among DB nodes.

### 3. Hugepages modes

`Hugepages` in EXAConf supports:

- `host`: do not change `/proc/sys/vm/nr_hugepages`
- `0`: disable hugepages (set to `0`)
- `<number>`: set exactly that number
- `auto`: derive from configured DB memory

For `auto` (per node):

1. Start with sum of per-node DB memory for DBs on that node.
2. Cap by `calc_db_memory(mem_total_mb, 1)`.
3. Subtract `MaxSystemHeapMemory` (or default 32 GiB per DB if unset).
4. If result is greater than `60 GiB`, set:
   - `nr_hugepages = hp_mem_mib * 1024 / 2048`
   - otherwise `0`.

## Reconfiguration Procedure

Depending on the deployment type, two main configuration approaches are possible:

### Privileged Container with Host Resource Access and no other resource intensive software running

1. Set global `Hugepages` to `auto`
2. set `MemSize` to total node RAM

### All other cases (including non-privileged containers)

1. Inspect actual container/node resources.
2. Set global `Hugepages` to `host` from the given `DBRAM` (per node) as in the calculation in [Hugepages Mode](#3-hugepages-modes)
3. Set `vm.hugetlb_shm_group` to the host group that maps to gid `55554` in the container
4. Set database `MemSize` for each DB according to actual memory available to the DB, respecting resource limits.

## Where to Set These Values

Set them in EXAConf through ConfD jobs (for example via `confd_client`):

- Set global hugepages mode:

```console
confd_client general_settings changes: '{"Global":{"Hugepages":"auto"}}'
```

- Set database memory size (`MemSize` / `OVERALL_DBRAM` source value):

```console
confd_client db_configure db_name: DB1 mem_size: '256 GiB'
```

In order for the database to pick up the changes, you may need to restart the database.
Always verify the effective settings after making changes.

- Stop the database

```console
confd_client db_stop db_name: DB1
```

Start the database

```console
confd_client db_start db_name: DB1
```
