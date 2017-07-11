#!/bin/sh

if [ ! -e /exa/data/storage/dev.1.data ]; then
    mkdir -p /exa/etc /exa/metadata/storage /exa/metadata/dwad /exa/data/storage /exa/data/bucketfs /exa/logs/logd
    chown -R 500:500 /exa
    dd if=/dev/zero of=/exa/data/storage/dev.1.meta bs=1048576 count=1 >/var/log/dev.1.out 2>&1
    dd if=/dev/zero of=/exa/data/storage/dev.1.data seek=9216 bs=1048576 count=1 >>/var/log/dev.1.out 2>&1
fi

export EXA_NODE_ID=11
export HOSTNAME="n$EXA_NODE_ID"
hostname "$HOSTNAME"
/sbin/ip addr

while true; do
    CURIP="$(/sbin/ip addr | awk '{ if (n>0) { n+=1; } if (n==3) { net=$2; n=0; } } /state UP/{ n=1; } END { print net; }')"
    [ ! -z "$CURIP" ] && break
    sleep 1
done
sed -i "s+PrivateNet *= *.*$+PrivateNet = $CURIP+" /exa/etc/EXAConf || echo "FAILED TO WRITE THE NETWORK CONFIGURATION TO EXAConf!"
rm -f /var/run/ecos_unix_auth

exec /usr/opt/EXASuite-6/EXAClusterOS-6.0.1/libexec/exainit.py
