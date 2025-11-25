# Frequently Asked Questions

## Can I use docker-db in Production?

No.

Currently, docker-db is only approved for functional testing.

## Does docker-db Come With a License?

Yes.

Docker-db has a license pre-installed that comes with restrictions. The restrictions are chosen to accommodate the needs of functional testing.

## Are There Hard CPU Limits?

Docker-db does not enforce any CPU limits.

## Are There Hard RAM Limits?

Docker-db does not enforce any RAM limits.

## Are There Hard Storage Limits?

The docker-db image license enforces a 10 GiB raw data storage limit.

## Can I use docker-db in a Cluster?

The license limits docker-db to a single node.

## If I had a Cluster License, Could I Replace Running Nodes?

In a running Exasol cluster, you can replace a *data node*, if you configure the cluster with reserve nodes.

Doing that for many data nodes will cause a cluster reorganization that will get expensive.

That is currently a theoretical possibility, though, since the license is for single nodes only.

You cannot, however, replace the running management node.

## Could I Resize a Data Node?

Theoretically, yes, but it is not recommended.

Downsizing any data node will define the maximum RAM size of all the data nodes in the cluster and, thus, create a bottleneck.

## How do I Upgrade the Version of a docker-db Container?

We recommend that you replace the container with a new one.

If you have data inside the container that you want to keep, please export it to a file and then import it into a new container.

See also:

* [EXPORT](https://docs.exasol.com/db/latest/sql/export.htm)
* [IMPORT](https://docs.exasol.com/db/latest/sql/import.htm)

## Does docker-db Support Object Storage (Like S3)?

No.

We made docker-db to be used in functional testing and that must work with local storage.

## How do I Determine Service Health From Outside the Exasol Database?

1. You can check the health of the database process by running a simple SQL query on the database port (default 8563).

    ```sql
    SELECT 1;
    ```

2. The BucketFS service can be checked by listing the buckets on the default port (2581). The returned list contains the entry `default`, which is the name of the one bucket that always exists right out of the box. Depending on whether you added more buckets, there can be additional buckets.

    ```shell
    curl https://localhost:2581/ -k
    ```
   
    The option `-k` disables the certificate check. If you want to also make sure in your health check that the TLS certificate is valid, you will need to install a proper certificate in the Exasol Docker instance. Out of the box it comes with a self-signed certificate. 

3. You can check if UDFs in your desired programming language are available by running a simple UDF script in that language.
   Here is an example for checking if Python UDFs are available.

    ```sql
    -- Preparation
    CREATE SCHEMA health_check;
    
    CREATE OR REPLACE PYTHON3 SCALAR SCRIPT health_check.return_one() RETURNS INTEGER AS
    def run(ctx):
    return 1
    /
    ;
   
    -- UDF Health Check 
    SELECT health_check.return_one();
    ```
   
Please note that if you want to check from outside the Docker network, you need port forwarding.

Why would you need a UDF check at all? Depending on which Script Language Containers (SLC) you installed, different runtimes and libraries for certain programming languages are available.

At the time of this writing (2025-11-25), `docker-db` comes with a SLCs for Java, Python and R preinstalled.

You also don't have to repeat this check. If the SQL engine works, and you established that the UDF language you need is there, testing once is sufficient.

If you plan to use the preinstalled SLC languages, you can even skip this check. 