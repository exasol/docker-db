#!/bin/bash

# Default values
db_version="latest"
network_name="exasol-network"
disk_size="100G"
external_port=8563
container_name="exasol-db-rootless"
external_volume=""
CONTENG=podman


# Help function
usage() {
    echo "Usage: $0 [options]"
    echo "Options:"
    echo "  --db-version <version>      Set the Exasol DB version (default: latest)"
    echo "  --network <name>           Set the $CONTENG network name (default: exasol-network)"
    echo "  --disk-size <size>         Set the disk size (default: 100G)"
    echo "  --external-port <port>     Set the external port mapping to container port 8563 (default: 8563)"
    echo "  --container-name <name>    Set the container name (default: exasol-container)"
    echo "  --external-volume <path>   Set the external volume name to be mapped (optional)"
    echo "  -h, --help                Show this help message and exit"
    exit 0
}

# Parse arguments
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --db-version)
      db_version="$2"; shift 2;;
    --network)
      network_name="$2"; shift 2;;
    --disk-size)
      disk_size="$2"; shift 2;;
    --external-port)
      external_port="$2"; shift 2;;
    --container-name)
      container_name="$2"; shift 2;;
    --external-volume)
      external_volume="$2"; shift 2;;
    -h|--help)
      usage;;
    *)
      echo "Unknown parameter: $1"; usage;;
  esac
done

# Extract numeric part from disk_size (e.g., "100G" -> "100")
disk_size_num=$(echo "$disk_size" | grep -oE '[0-9]+')

# Calculate 90% of the disk size
volume_size_num=$(echo "$disk_size_num * 0.9" | bc | awk '{printf "%.0f", $1}')

# Append "G" to maintain gigabyte format
volume_size="${volume_size_num}G"

# Create network if not exists
$CONTENG network inspect "$network_name" >/dev/null 2>&1 || \
    $CONTENG network create "$network_name"

# Prepare volume mapping if external volume is provided
volume_mapping=""
if [[ -n "$external_volume" ]]; then
    volume_mapping="-v $external_volume:/exa"
fi

# Run container initialization  
$CONTENG run -d \
    --name "$container_name" \
    --entrypoint bash \
    --network="$network_name" \
    --sysctl kernel.msgmax=1073741824 \
    --sysctl kernel.msgmnb=1073741824 \
    --sysctl kernel.shmmni=32768 \
    --cap-add=SYS_ADMIN --cap-add=SYS_PTRACE \
    --security-opt seccomp=unconfined \
    --security-opt label=disable \
    --security-opt apparmor=unconfined \
    -p 8563:${external_port} \
    $volume_mapping \
    exasol/docker-db:$db_version \
    -c '
        if [ -z "$CURRENTCOS" ]; then
            echo "ERROR: CURRENTCOS is not set!" >&2
            exit 1
        fi
        ip_address=$(ip -4 -o addr show up scope global | awk "{print \$4}" | head -n 1)
        mkdir -p /.root /exa
        if [ ! -e /exa/etc/EXAConf ]; then
            $CURRENTCOS/sbin/exact mount /.root / /exa init-sc -i 11 -t
            truncate -s '"$disk_size"' /exa/data/storage/dev.1
            exaconf modify-volume -n DataVolume1 -s '"$volume_size"'
            sed -e "s/ Hugepages\s*=.*/ Hugepages = host/" -i /exa/etc/EXAConf
            sed -e "s/ Checksum\s*=.*/ Checksum = COMMIT/" -i /exa/etc/EXAConf
        fi
        if [ ! -e /exa/init.sh ]; then
            echo "#!/bin/bash" > /exa/init.sh
            echo \$CURRENTCOS/sbin/exact mount /.root / /exa init-sc -i 11 >> /exa/init.sh
            chmod +x /exa/init.sh
        fi
        exaconf modify-node -n 11 -p $ip_address
        /exa/init.sh
    ' &&  \
    echo "Setup completed. Container '$container_name' started using $CONTENG ... " && \
    $CONTENG ps --all


