FROM centos:6.8

MAINTAINER EXASOL "service@exasol.com"

RUN yum update -y --exclude=kernel* && \
    yum install -y \
    java-1.8.0-openjdk-headless \
    openssh-server \
    openssh-clients \
    which \
    sudo \
    vim \
    tar \
    man \
    rsync && \
    yum clean all

LABEL name="EXASOL DB Docker Image"  \
      version="6.0.11-d1" \
      dbversion="6.0.11" \
      osversion="6.0.11" \
      reversion="6.0.11" \
      license="Proprietary" \
      vendor="EXASOL AG"


COPY license/license.xml     /.license.xml
ADD EXAClusterOS-6.0.11_LS-DOCKER-CentOS-6.8_x86_64.tar.gz              /
ENV PATH=/usr/opt/EXASuite-6/EXAClusterOS-6.0.11/bin:/usr/opt/EXASuite-6/EXAClusterOS-6.0.11/sbin:/usr/opt/EXASuite-6/EXARuntime-6.0.11/bin:/usr/opt/EXASuite-6/EXARuntime-6.0.11/sbin:/usr/opt/EXASuite-6/EXASolution-6.0.11/bin/Console:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    MANPATH=/usr/opt/EXASuite-6/EXAClusterOS-6.0.11/man:/usr/local/share/man:/usr/share/man \
    EXA_IMG_VERSION="6.0.11-d1" \
    EXA_DB_VERSION="6.0.11" \
    EXA_OS_VERSION="6.0.11" \
    EXA_RE_VERSION="6.0.11" 

ENTRYPOINT ["/usr/opt/EXASuite-6/EXAClusterOS-6.0.11/devel/docker/exadt"]
CMD ["init-sc"]
