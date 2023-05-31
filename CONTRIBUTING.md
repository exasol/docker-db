# Contributing to the development of the Exasol Docker version

## Reporting bugs
                                 
Bugs that are specifically related to the Docker version of the Exasol DB should be reported as a [Github Issue](https://github.com/EXASOL/docker-db/issues).

If you're able to execute commands in the Exasol container, then please use `exasupport` to generate an archive containing all logs and coredumps (if any).

If the container startup fails, there are two ways to collect the logs:

1.) If you are using `exadt`, collect logs with `exadt collect-info <ClusterName>` and add them to your bug report. 

2.) If you created containers with `docker run`, please provide the following information:

* your Docker version (the output of `docker version`)
* the output of `docker logs` and `docker inspect` (of container and image)
* the content of `/exa/logs` and `/exa/etc` from within the container

Also try to answer the following questions in your bug report:

* What were you doing before the error occured?
* What did you expect to happen?
* What actually happened?
 

## Suggesting improvements / providing fixes

Feel free to suggest improvements by creating Github issues. If you'd like to submit patches, make sure that they are in `unified` format and attach them to your issue.

## Creating pull requests

**While it's possible to submit patches using Github PRs, please be aware of some constraints:**

- We use Github as one of several ways to distribute our product, but we don't use Git as our internal VCS
- The files in this repository are automatically created from our internal VCS during the release process
- All changes made solely through Git/Github (like merging pull requests) will be lost after the next release
- PRs that are specific to Git/Github will be rejected if they conflict with other ways of distribution

So, in order to make your PR permanent, we have to transfer it to our internal VCS. 
It will be reviewed and tested and eventually become visible in this repository after the next release. 
Therefore, even if your PR is acceppted, it may not be merged via Git/Github.
