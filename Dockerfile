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
    lsof \
    psmisc \
    rsync && \
    yum clean all

RUN yum --disablerepo=epel -y update ca-certificates && \
    yum install -y \
    python-pam \
    rlwrap 

LABEL name="EXASOL DB Docker Image"  \
      version="8.20.0" \
      dbversion="8.20.0" \
      osversion="8.32.0" \
      reversion="8.4.0" \
      license="Proprietary" \
      vendor="EXASOL AG"


COPY license/license.xml     /.license.xml
ADD EXAClusterOS-8.32.0_LS-DOCKER-CentOS-7.5.1804_x86_64.tar.gz              /
ENV PATH=/opt/exasol/cos-8.32.0/bin:/opt/exasol/cos-8.32.0/sbin:/opt/exasol/runtime-8.4.0/bin:/opt/exasol/runtime-8.4.0/sbin:/opt/exasol/db-8.20.0/bin/Console:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    MANPATH=/opt/exasol/cos-8.32.0/man:/usr/local/share/man:/usr/share/man \
    EXA_IMG_VERSION="8.20.0" \
    EXA_DB_VERSION="8.20.0" \
    EXA_OS_VERSION="8.32.0" \
    EXA_RE_VERSION="8.4.0" 

ENTRYPOINT ["/opt/exasol/cos-8.32.0/docker/entrypoint.sh"]
CMD ["init-sc"]
