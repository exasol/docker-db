#! /bin/bash

source "$(dirname $0)/functions.sh"
EXADT_DIR="$(readlink -f "$(dirname "$0")/../")"
LICENSE="$(readlink -f "$(dirname "$0")/../license/license.xml")"
ROOT="$HOME"
IMAGE="exasol/docker-db:latest"
DO_PULL="true"
DOCKER="$(which docker)"
PIPENV="$(which pipenv)"
TMP_DIR=$(mktemp -d)
INFO_FILE="$TMP_DIR/exasol_info.tgz"
NUM_NODES=1

cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup exit
  
log() { 
    echo "[$(basename ${0})]: ${*}"
}
     
die() { 
    log "FATAL: ${*}"
    exit 1
}
 
if [[ -z "${INSTALL_LOG}" ]]; then
    INSTALL_LOG="1"
fi

usage() {
    echo "Usage: $0 [-i IMAGE] [-r ROOT] [-b EXADT_BINARY_DIR] [-D DOCKER CMD]"
    echo "Parameters:"
    echo "-i    : Docker image to use for the test (default: '$IMAGE')."
    echo "-n    : Number of nodes  / containers for the test-cluster (default: '$NUM_NODES')."
    echo "-D    : Docker command (default: '$DOCKER')."
    echo "-r    : Root directory for cluster creation (default: '$ROOT')."
    echo "-b    : The folder that contains the exadt binary (default: '$EXADT_DIR')."
    echo "-l    : The license (default: '$LICENSE')."
    echo "-P    : Don't try to pull Docker image (default: '$DO_PULL')."
    echo "-p    : Path to 'pipenv' binary (default: '$PIPENV')."
}

# parse parameters
while getopts "i:n:D:r:b:l:p:Ph" opt; do
    case "$opt" in
        i)
            IMAGE="$OPTARG"
            log "INFO:: Using image '$IMAGE'."
            ;;
        n)
            NUM_NODES="$OPTARG"
            log "INFO:: Using '$NUM_NODES' nodes for test-cluster."
            ;;
        D)
            DOCKER="$OPTARG"
            log "INFO:: Using Docker command '$DOCKER'."
            ;;
        r)
            ROOT="$(readlink -f "$OPTARG")"
            log "INFO:: Using root directory '$ROOT'."
            ;;
        b)
            EXADT_DIR="$(readlink -f "$OPTARG")"
            log "INFO:: Using exadt binary from '$EXADT_DIR'."
            ;;
        l)
            LICENSE="$(readlink -f "$OPTARG")"
            log "INFO:: Using license '$LICENSE'."
            ;;
        p)
            PIPENV="$(readlink -f "$OPTARG")"
            log "INFO:: Using pipenv from '$PIPENV'"
            ;;
        P)
            DO_PULL="false"
            log "INFO:: Not pulling the Docker image."
            ;;
        h)
            usage
            exit 0
            ;;
        ?)
            usage
            exit 1
            ;;
    esac
done

if [[ ! -f $PIPENV ]]; then
    die "Could not find 'pipenv'!"
fi
if [[ ! -f "$EXADT_DIR/exadt" ]]; then
    die "Could not find 'exadt'!"
fi

log "=== Starting exadt basic test ==="
if [[ "$DO_PULL" == "true" ]]; then
    $DOCKER pull "$IMAGE" #does not work with locally built dev-images
fi
set -e -x
cd "$EXADT_DIR" # necessary for pipenv
"$PIPENV" install -r exadt_requirements.txt 2>> "$INSTALL_LOG"
"$PIPENV" run ./exadt list-clusters
"$PIPENV" run ./exadt create-cluster --root "$ROOT/MyCluster/" --create-root MyCluster
"$PIPENV" run ./exadt list-clusters
"$PIPENV" run ./exadt collect-info --outfile "$INFO_FILE" MyCluster
"$PIPENV" run ./exadt init-cluster --image "$IMAGE" --num-nodes "$NUM_NODES" --license "$LICENSE" --device-type file --auto-storage --force MyCluster
"$PIPENV" run ./exadt list-clusters
"$PIPENV" run ./exadt collect-info --outfile "$INFO_FILE" MyCluster
"$PIPENV" run ./exadt start-cluster MyCluster
"$PIPENV" run ./exadt ps MyCluster
"$PIPENV" run ./exadt exec -c "/bin/date" -a MyCluster
wait_db "$EXADT_DIR" "DB1" MyCluster
"$PIPENV" run ./exadt list-dbs MyCluster
"$PIPENV" run ./exadt collect-info --outfile "$INFO_FILE" MyCluster
"$PIPENV" run ./exadt exec -c "/bin/bash -c 'X=\$(shopt -s nullglob; ls -1 /usr/opt/EXASuite-*/EXASolution-*/bin/Console/exaplus /opt/exasol/db-*/bin/Console/exaplus | tail -n1); echo \"SELECT 123*42345;\" | \$X -c n11:8563 -u sys -P exasol -jdbcparam validateservercertificate=0'" MyCluster 2>&1 | tee /dev/stderr | grep -q 5208435
"$PIPENV" run ./exadt list-dbs MyCluster
"$PIPENV" run ./exadt stop-db MyCluster
"$PIPENV" run ./exadt start-db MyCluster
"$PIPENV" run ./exadt stop-cluster MyCluster
"$PIPENV" run ./exadt create-file-devices --size 10GiB MyCluster
yes | "$PIPENV" run ./exadt create-file-devices --size 10GiB MyCluster --replace --path $HOME
"$PIPENV" run ./exadt update-cluster --image "$IMAGE" MyCluster
"$PIPENV" run ./exadt start-cluster MyCluster
"$PIPENV" run ./exadt stop-cluster MyCluster
"$PIPENV" run ./exadt start-cluster MyCluster --command "/bin/sleep 30"
"$PIPENV" run ./exadt stop-cluster MyCluster --timeout 2
yes | "$PIPENV" run ./exadt delete-cluster MyCluster
set +x
log "=== Successful! ==="
