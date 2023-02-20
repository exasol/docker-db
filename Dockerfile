FROM centos:7.5.1804

MAINTAINER EXASOL "service@exasol.com"

RUN yum update -y --exclude=kernel* && \
    yum install -y \
    epel-release \
    java-1.8.0-openjdk-headless \
    openssh-server \
    openssh-clients \
    which \
    sudo \
    vim \
    tar \
    man \
    iproute \
    strace \
    mtr \
    lvm2 \
    rsyslog \
    rsyslog-gnutls \
    cronie \
    samba-client \
    lftp \
    rsync && \
    yum clean all

RUN yum --disablerepo=epel -y update ca-certificates && \
    yum install -y \
    python-pam \
    rlwrap 

LABEL name="EXASOL DB Docker Image"  \
      version="7.0.22" \
      dbversion="7.0.22" \
      osversion="7.0.22" \
      reversion="7.0.22" \
      license="Proprietary" \
      vendor="EXASOL AG"


COPY license/license.xml     /.license.xml
ADD EXAClusterOS-7.0.22_LS-DOCKER-CentOS-7.5.1804_x86_64.tar.gz              /
ENV PATH=/usr/opt/EXASuite-7/EXAClusterOS-7.0.22/bin:/usr/opt/EXASuite-7/EXAClusterOS-7.0.22/sbin:/usr/opt/EXASuite-7/EXARuntime-7.0.22/bin:/usr/opt/EXASuite-7/EXARuntime-7.0.22/sbin:/usr/opt/EXASuite-7/EXASolution-7.0.22/bin/Console:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    MANPATH=/usr/opt/EXASuite-7/EXAClusterOS-7.0.22/man:/usr/local/share/man:/usr/share/man \
    EXA_IMG_VERSION="7.0.22" \
    EXA_DB_VERSION="7.0.22" \
    EXA_OS_VERSION="7.0.22" \
    EXA_RE_VERSION="7.0.22" 

ENTRYPOINT ["/usr/opt/EXASuite-7/EXAClusterOS-7.0.22/docker/entrypoint.sh"]
CMD ["init-sc"]
