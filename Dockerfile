FROM ubuntu:22.04

MAINTAINER EXASOL "service@exasol.com"

# Disable installation of optional dependencies
RUN echo 'APT::Install-Suggests "0";' >> /etc/apt/apt.conf.d/00-docker
RUN echo 'APT::Install-Recommends "0";' >> /etc/apt/apt.conf.d/00-docker
# Unminimize the image (enables man-pages and installs some basic packages)
RUN yes | /usr/local/sbin/unminimize
# Install required packages
RUN http_proxy=http://repoproxy.core.exasol.com:3128 DEBIAN_FRONTEND=noninteractive apt-get -y update &&     http_proxy=http://repoproxy.core.exasol.com:3128 DEBIAN_FRONTEND=noninteractive apt-get -y upgrade &&     http_proxy=http://repoproxy.core.exasol.com:3128 DEBIAN_FRONTEND=noninteractive apt-get -y install     default-jdk-headless     openssh-server     openssh-client     locales     sudo     vim     tar     cron     anacron     iproute2     strace     mtr     udev     lvm2     kmod     rsyslog     rsyslog-gnutls     smbclient     lftp     python3-pam     rlwrap     man-db     iputils-ping     ca-certificates     lsb-release     less     sed     rsync &&     http_proxy=http://repoproxy.core.exasol.com:3128 apt-get -y clean && rm -rf /var/lib/apt/lists/*
# Set default locale
RUN locale-gen C.utf8 && update-locale LANG=C.utf8 LC_ALL=C.utf8

LABEL name="EXASOL DB Docker Image"        version="8.25.0"       dbversion="8.25.0"       osversion="8.44.0"       reversion="8.5.0"       license="Proprietary"       vendor="EXASOL AG"

COPY license/license.xml     /.license.xml
ADD EXAClusterOS-8.44.0_LS-DOCKER-CentOS-7.5.1804_x86_64.tar.gz              /

ENV PATH=/opt/exasol/cos-8.44.0/bin:/opt/exasol/cos-8.44.0/sbin:/opt/exasol/runtime-8.5.0/bin:/opt/exasol/runtime-8.5.0/sbin:/opt/exasol/db-8.25.0/bin/Console:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin     MANPATH=/opt/exasol/cos-8.44.0/man:/usr/local/share/man:/usr/share/man     EXA_IMG_VERSION="8.25.0"     EXA_DB_VERSION="8.25.0"     EXA_OS_VERSION="8.44.0"     EXA_RE_VERSION="8.5.0"

ENTRYPOINT ["/opt/exasol/cos-8.44.0/docker/entrypoint.sh"]
CMD ["init-sc"]
